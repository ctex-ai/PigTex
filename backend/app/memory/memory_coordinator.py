"""
Memory Coordinator - Orchestrates between Server DB and Local Storage.
This is the main entry point for the memory system.

Architecture (v2 - 2-Stream Memory):
  Stream 1: UserProfileStore  – strict identity-only fields (name, age, ...)
  Stream 2: ContextMemoryStore – scoped context (workspace, conversation, temporary)
  GateKeeper: MemoryGate       – routes messages to the correct stream
"""

from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, field
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
import uuid
import asyncio
import json
import re
import logging
import os
import unicodedata
from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from ..local_storage import (
    LocalDatabase,
    LocalConversation,
    LocalMessage,
    LocalKnowledgeItem,
    LocalFact,
    LocalUserPreference,
    LocalMemoryAssertion,
    LocalMemoryEvidence,
    LocalMemoryPendingChange,
)
from ..local_storage.local_db import get_storage_dir
from ..upstream_request import UpstreamRequestConfig
from .prompt_injector import PromptInjector, get_prompt_injector
from .memory_gate import MemoryGate, MemoryStream
from .user_profile_store import UserProfileStore
from .context_memory_store import ContextMemoryStore

# Optional: Local embedding service for semantic search
try:
    from ..local_storage.embedding_service import LocalEmbeddingService, get_embedding_service
    EMBEDDINGS_AVAILABLE = True
except ImportError:
    EMBEDDINGS_AVAILABLE = False
    get_embedding_service = None

# Fact extraction (legacy, kept for backward compat)
try:
    from .fact_extractor import FactExtractor, get_fact_extractor
    FACT_EXTRACTION_AVAILABLE = True
except ImportError:
    FACT_EXTRACTION_AVAILABLE = False
    get_fact_extractor = None

logger = logging.getLogger(__name__)


@dataclass
class WorkingMemory:
    """
    In-memory session state (like RAM).
    Holds immediate context for current conversation.
    """
    conversation_id: Optional[str] = None
    workspace_id: Optional[str] = None
    
    # Rolling buffer of recent messages
    message_buffer: deque = field(default_factory=deque)
    max_messages: int = 240
    
    # Active knowledge items being referenced
    active_knowledge: List[str] = field(default_factory=list)
    
    # Current detected intent
    current_intent: Optional[str] = None
    
    # Token budget
    max_tokens: int = 4000
    current_tokens: int = 0

    # Memory injection anti-repeat state
    turn_index: int = 0
    memory_injection_history: deque = field(default_factory=lambda: deque(maxlen=120))
    
    def add_message(self, role: str, content: str, tokens: int = 0):
        """Add message to buffer"""
        # Estimate tokens if not provided
        if tokens == 0:
            tokens = int(len(content.split()) * 1.3)
        
        self.message_buffer.append({
            "role": role,
            "content": content,
            "tokens": tokens
        })
        self.current_tokens += tokens
        
        # Evict oldest if over budget or message count gets too large.
        while (
            (self.current_tokens > self.max_tokens and len(self.message_buffer) > 2)
            or len(self.message_buffer) > self.max_messages
        ):
            evicted = self.message_buffer.popleft()
            self.current_tokens = max(0, self.current_tokens - int(evicted.get("tokens", 0)))
    
    def get_messages(self) -> List[Dict[str, str]]:
        """Get messages in OpenAI format"""
        return [
            {"role": m["role"], "content": m["content"]}
            for m in self.message_buffer
        ]
    
    def clear(self):
        """Clear working memory"""
        self.message_buffer.clear()
        self.active_knowledge.clear()
        self.current_intent = None
        self.current_tokens = 0
        self.turn_index = 0
        self.memory_injection_history.clear()

    def begin_turn(self):
        """Advance to a new turn for memory anti-repeat tracking."""
        self.turn_index += 1

    def remember_injected_memory(self, fingerprint: str):
        """Track that a memory item was injected this turn."""
        if not fingerprint:
            return
        self.memory_injection_history.append({
            "fingerprint": fingerprint,
            "turn": self.turn_index
        })

    def turns_since_memory_injection(self, fingerprint: str) -> Optional[int]:
        """Return how many turns ago a memory item was injected."""
        if not fingerprint:
            return None

        for item in reversed(self.memory_injection_history):
            if item.get("fingerprint") == fingerprint:
                turn = item.get("turn")
                if isinstance(turn, int):
                    return max(0, self.turn_index - turn)
                return None
        return None


@dataclass
class AssembledContext:
    """Context assembled for an AI request"""
    system_prompt: str = ""
    rules_context: str = ""
    messages: List[Dict[str, str]] = field(default_factory=list)
    knowledge_context: str = ""
    facts_context: str = ""
    total_tokens: int = 0
    sources: List[Dict] = field(default_factory=list)


class MemoryCoordinator:
    """
    Main coordinator between Server (MySQL) and Local (SQLite).
    
    Server handles:
    - User auth
    - Usage tracking
    - System prompts, skills (bơm ngầm)
    
    Local handles:
    - Conversation history
    - Knowledge items
    - Facts and preferences
    """

    SINGLE_VALUE_PREDICATES = {
        "name",
        "user_nickname",
        "age",
        "gender",
        "birth_date",
        "location",
        "timezone",
        "occupation",
        "company",
        "education",
        "nationality",
        "native_language",
        "marital_status",
        "response_language",
        "response_style",
        "tone_preference",
        "primary_language",
        "editor",
        "preferred_os",
        "project_goal",
        "project_deadline",
        "naming_convention",
    }

    GUARDED_SINGLE_VALUE_PREDICATES = {
        "name",
        "age",
        "gender",
        "birth_date",
        "occupation",
        "company",
        "education",
        "nationality",
    }

    # Core identity facts that should ALWAYS be injected into context
    # so the AI always knows who it's talking to
    IDENTITY_PREDICATES = {
        "name",
        "user_nickname",
        "age",
        "gender",
        "occupation",
        "location",
        "education",
        "nationality",
    }

    MAX_CONTEXT_PREFERENCES = 16
    MAX_CONTEXT_FACTS = 12
    MAX_USER_CONTEXT_TOKENS = 640
    MAX_PREFERENCE_CONTEXT_TOKENS = 180
    MAX_FACT_CONTEXT_TOKENS = 420
    MAX_PREFERENCES_PER_TURN = 8
    MAX_SYSTEM_FACTS_PER_TURN = 8
    MAX_WORKSPACE_FACTS_PER_TURN = 10
    MEMORY_REPEAT_HARD_COOLDOWN_TURNS = 2
    MEMORY_REPEAT_SOFT_COOLDOWN_TURNS = 5
    AI_EXTRACTION_MIN_CHARS = 18
    AI_EXTRACTION_MAX_CONTEXT_MESSAGES = 10
    MAX_QUERY_TOKENS = 24
    KNOWLEDGE_VECTOR_MIN_SIMILARITY = 0.24
    KNOWLEDGE_VECTOR_CANDIDATE_MULTIPLIER = 4
    KNOWLEDGE_FTS_CANDIDATE_MULTIPLIER = 5
    HISTORY_RECENT_MESSAGES_BALANCED = 18
    HISTORY_RECENT_MESSAGES_LOW_LATENCY = 10
    HISTORY_MAX_SCOUT_MESSAGES = 80
    HISTORY_MAX_RELEVANT_PICKS = 6
    HISTORY_MIN_OVERLAP = 0.16
    SMALLTALK_TOKEN_LIMIT = 5
    MAX_CONTEXT_IDENTITY_ASSERTIONS = 3
    MAX_CONTEXT_PREFERENCE_ASSERTIONS = 3
    MAX_CONTEXT_FACT_ASSERTIONS = 3
    TEMPORARY_ASSERTION_TTL_DAYS = 7
    MEMORY_DRIFT_MERGE_SIMILARITY = 0.85

    MIN_ASSERTION_CONFIDENCE_BY_TYPE = {
        "identity": 0.85,
        "preference": 0.85,
        "fact": 0.85,
        "temporary": 0.85,
    }

    KEY_CONFIRMATION_REQUIRED = {
        "response_style",
        "response_language",
        "tone_preference",
        "emoji_usage",
        "user_name",
        "occupation",
        "company",
        "location",
    }

    KEY_SINGLE_VALUE = {
        "user_name",
        "user_nickname",
        "user_age",
        "gender",
        "location",
        "timezone",
        "occupation",
        "company",
        "response_language",
        "response_style",
        "tone_preference",
        "emoji_usage",
    }

    MEMORY_KEY_ALIASES = {
        "name": "user_name",
        "user_name": "user_name",
        "user_nickname": "user_nickname",
        "age": "user_age",
        "user_age": "user_age",
        "gender": "gender",
        "location": "location",
        "timezone": "timezone",
        "occupation": "occupation",
        "company": "company",
        "response_language": "response_language",
        "response_style": "response_style",
        "tone_preference": "tone_preference",
        "tone": "tone_preference",
        "likes": "likes",
        "dislikes": "dislikes",
        "hobby": "hobby",
        "favorite": "favorite",
        "primary_language": "primary_language",
        "preferred_os": "preferred_os",
        "editor": "editor",
        "naming_convention": "naming_convention",
        "project_goal": "project_goal",
        "project_deadline": "project_deadline",
        "project_rule": "project_rule",
    }

    MEMORY_VALUE_ALIASES = {
        "response_style": {
            "concise": "concise_structured",
            "brief": "concise_structured",
            "ngan gon": "concise_structured",
            "ngan gon co cau truc": "concise_structured",
            "ngan gon co cau truc ro rang": "concise_structured",
            "chi tiet": "detailed_structured",
            "detailed": "detailed_structured",
            "step_by_step": "step_by_step",
            "tung buoc": "step_by_step",
        },
        "emoji_usage": {
            "avoid": "avoid",
            "off": "avoid",
            "no": "avoid",
            "khong dung": "avoid",
            "khong dung emoji": "avoid",
            "do not use emoji": "avoid",
            "dont use emoji": "avoid",
            "allowed": "allowed",
            "on": "allowed",
            "yes": "allowed",
            "co the dung": "allowed",
            "emoji cung duoc": "allowed",
        },
    }

    TEMPORAL_MARKERS = (
        "today",
        "tonight",
        "tomorrow",
        "this week",
        "this month",
        "currently",
        "for now",
        "temporary",
        "during this trip",
        "hôm nay",
        "hom nay",
        "tuần này",
        "tuan nay",
        "tháng này",
        "thang nay",
        "hiện tại",
        "hien tai",
        "tạm thời",
        "tam thoi",
        "lúc này",
        "luc nay",
        "chuyến đi này",
        "chuyen di nay",
    )
    KNOWLEDGE_SEARCH_STOPWORDS = {
        "the", "a", "an", "and", "or", "for", "from", "with", "into",
        "about", "this", "that", "these", "those", "what", "when", "where",
        "which", "while", "how", "why", "please", "help", "need",
        "trong", "cua", "la", "gi", "cho", "toi", "ban", "hay", "va", "hoac",
        "nhung", "nay", "do", "voi", "mot", "nhieu", "duoc", "khong",
    }
    
    def __init__(
        self, 
        server_db: Session, 
        user_id: str,
        local_storage: Optional[LocalDatabase] = None
    ):
        self.server_db = server_db
        self.user_id = user_id
        self.local = local_storage or LocalDatabase(user_id)
        self.local_user_id = self.local.user_id
        self.prompt_injector = get_prompt_injector(server_db)
        
        # In-memory working state
        self.working = WorkingMemory()
        
        # Embedding service (optional, lazy loaded)
        self._embedding_service = None
        self._embeddings_enabled = os.getenv("PIGTEX_EMBEDDINGS_ENABLED", "1").strip().lower() in {
            "1", "true", "yes", "on"
        }
        if not self._embeddings_enabled:
            logger.info("Local embeddings disabled by PIGTEX_EMBEDDINGS_ENABLED")
        preload_timeout_raw = os.getenv("PIGTEX_STREAM_CONTEXT_PRELOAD_TIMEOUT_MS", "280").strip()
        try:
            preload_timeout_ms = int(preload_timeout_raw)
        except ValueError:
            preload_timeout_ms = 280
        self._stream_context_preload_timeout_ms = max(0, min(5000, preload_timeout_ms))
        balanced_preload_timeout_raw = os.getenv("PIGTEX_CONTEXT_PRELOAD_TIMEOUT_MS", "3500").strip()
        try:
            balanced_preload_timeout_ms = int(balanced_preload_timeout_raw)
        except ValueError:
            balanced_preload_timeout_ms = 3500
        self._context_preload_timeout_ms = max(0, min(30000, balanced_preload_timeout_ms))
        self._context_maintenance_enabled = os.getenv(
            "PIGTEX_CONTEXT_MAINTENANCE_ENABLED", "0"
        ).strip().lower() in {"1", "true", "yes", "on"}
        
        # Fact extraction (optional, legacy)
        self._fact_extractor = None
        self._fact_extraction_enabled = True
        self._ai_fact_extraction_enabled = True
        self._legacy_unified_backfill_attempted = False
        self._request_upstream_config: Optional[UpstreamRequestConfig] = None
        self._request_ai_model: Optional[str] = None

        # ─── v2: 2-Stream Memory ───
        self._memory_gate = MemoryGate()
        self._profile_store = UserProfileStore(self.local, self.local_user_id)
        self._context_store = ContextMemoryStore(self.local, self.local_user_id)
        self._v2_migration_done = False

    def set_request_upstream_context(
        self,
        upstream_config: Optional[UpstreamRequestConfig],
        *,
        ai_model: Optional[str] = None,
    ) -> None:
        """Attach request-scoped upstream credentials for background AI tasks."""
        self._request_upstream_config = upstream_config
        self._request_ai_model = (ai_model or "").strip() or None
    
    # =========================================================================
    # Conversation Management (LOCAL)
    # =========================================================================
    
    def create_conversation(
        self,
        title: str = "New Conversation",
        workspace_id: Optional[str] = None
    ) -> LocalConversation:
        """Create a new conversation (stored locally)"""
        conv = LocalConversation(
            id=str(uuid.uuid4()),
            user_id=self.local_user_id,
            workspace_id=workspace_id,
            title=title,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        self.local.save_conversation(conv)
        
        # Set as current conversation
        self.working.conversation_id = conv.id
        self.working.workspace_id = workspace_id
        self.working.clear()
        
        return conv
    
    def load_conversation(self, conversation_id: str) -> Optional[LocalConversation]:
        """Load an existing conversation into working memory"""
        conv = self.local.get_conversation(conversation_id)
        if not conv:
            return None
        
        # Load into working memory
        self.working.conversation_id = conversation_id
        self.working.workspace_id = conv.workspace_id
        self.working.clear()
        
        # Load recent messages into buffer
        messages = self.local.get_recent_messages(
            conversation_id,
            max_tokens=self.working.max_tokens
        )
        for msg in messages:
            self.working.add_message(msg.role, msg.content, msg.token_count)
        
        return conv
    
    def add_message(
        self,
        role: str,
        content: str,
        model: Optional[str] = None,
        sources: Optional[List[Any]] = None
    ) -> LocalMessage:
        """Add a message to current conversation"""
        if not self.working.conversation_id:
            raise ValueError("No active conversation. Call create_conversation first.")
        
        # Estimate tokens
        token_count = int(len(content.split()) * 1.3)
        
        # Create message
        msg = LocalMessage(
            id=str(uuid.uuid4()),
            conversation_id=self.working.conversation_id,
            role=role,
            content=content,
            token_count=token_count,
            model=model,
            sources_json=json.dumps(sources) if sources else None,
            created_at=datetime.now()
        )
        
        # Save to local DB
        self.local.save_message(msg)
        
        # Add to working memory
        self.working.add_message(role, content, token_count)
        
        # Generate embedding in background (async-safe)
        if self._embeddings_enabled:
            self._embed_message_async(msg.id, content)
        
        # ─── v2: 2-Stream extraction ───
        if role == "user":
            self._extract_memories_v2(
                content=content,
                workspace_id=self.working.workspace_id,
                conversation_id=self.working.conversation_id,
            )
        
        return msg
    
    def _extract_memories_v2(
        self,
        content: str,
        workspace_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ):
        """
        v2 extraction pipeline: GateKeeper → Profile Store / Context Store.

        This replaces the monolithic _extract_facts_from_message with a
        clean 2-stream architecture:
          - PROFILE → UserProfileStore (name, age, gender, ...)
          - CONTEXT → ContextMemoryStore (project, tech stack, ...)
          - SKIP    → No extraction at all
        """
        if not content or len(content.strip()) < 8:
            return

        # One-time migration from legacy data
        if not self._v2_migration_done:
            self._run_v2_migration()

        # Classify message
        stream = self._memory_gate.classify(content, workspace_id)

        if stream == MemoryStream.SKIP:
            return

        # Stream 1: Profile extraction
        if stream in (MemoryStream.PROFILE, MemoryStream.BOTH):
            try:
                saved_profile = self._profile_store.extract_from_message(content)
                if saved_profile:
                    logger.info(
                        "Profile extracted: %d fields from message",
                        len(saved_profile),
                    )
            except Exception as e:
                logger.warning("Profile extraction failed: %s", e)

        # Stream 2: Context extraction
        if stream in (MemoryStream.CONTEXT, MemoryStream.BOTH):
            try:
                saved_context = self._context_store.extract_from_message(
                    content, workspace_id, conversation_id
                )
                if saved_context:
                    logger.info(
                        "Context extracted: %d items from message",
                        len(saved_context),
                    )
            except Exception as e:
                logger.warning("Context extraction failed: %s", e)

        # Legacy fallback: still run old extraction for any remaining patterns
        # that the new stores don't cover yet (will be removed in future)
        if self._fact_extraction_enabled and stream != MemoryStream.SKIP:
            try:
                self._extract_facts_from_message(
                    content=content,
                    source_id=conversation_id,
                    workspace_id=workspace_id,
                )
            except Exception as e:
                logger.debug("Legacy fact extraction skipped: %s", e)

    def _run_v2_migration(self):
        """One-time migration from legacy memory to v2 stores."""
        if self._v2_migration_done:
            return
        self._v2_migration_done = True
        try:
            self._profile_store.migrate_from_legacy()
        except Exception as e:
            logger.warning("Profile migration failed: %s", e)
        try:
            self._context_store.migrate_from_legacy()
        except Exception as e:
            logger.warning("Context migration failed: %s", e)

    def _extract_facts_from_message(
        self,
        content: str,
        source_id: Optional[str] = None,
        workspace_id: Optional[str] = None
    ):
        """Extract and persist facts/preferences from one user message."""
        if not FACT_EXTRACTION_AVAILABLE:
            return

        if not content or not content.strip():
            return

        if not self._should_extract_facts_from_message(content, workspace_id):
            return

        try:
            extractor = self._get_fact_extractor()
            result = extractor.extract_enriched(
                message=content,
                workspace_id=workspace_id,
                source_id=source_id
            )
            facts, preferences = self._filter_name_occupation_conflicts(
                result.facts,
                result.preferences,
            )

            existing_facts = self.local.get_facts(limit=5000)
            known_name_values = {
                str(f.object).strip()
                for f in existing_facts
                if str(f.predicate).strip().lower() in {"name", "user_nickname"}
                and str(f.object).strip()
            }
            known_name_values.update(
                str(f.object).strip()
                for f in facts
                if str(getattr(f, "predicate", "")).strip().lower() in {"name", "user_nickname"}
                and str(getattr(f, "object", "")).strip()
            )
            removed_conflict_values = self._cleanup_name_conflicting_occupation_memories(
                name_values=known_name_values,
                existing_facts=existing_facts,
            )
            if removed_conflict_values:
                existing_facts = [
                    fact for fact in existing_facts
                    if not (
                        str(getattr(fact, "predicate", "")).strip().lower() == "occupation"
                        and self._normalize_memory_value_for_compare(
                            str(getattr(fact, "object", "")).strip()
                        ) in removed_conflict_values
                    )
                ]
            fact_index, single_value_index = self._build_fact_indexes(existing_facts)

            for fact in facts:
                self._upsert_fact(fact, fact_index, single_value_index)

            self._upsert_preferences(
                preferences,
                source_conversation_id=source_id
            )

            self._upsert_unified_memory(
                facts=facts,
                preferences=preferences,
                content=content,
                source_id=source_id,
                workspace_id=workspace_id,
            )

            if self._ai_fact_extraction_enabled and self._should_run_ai_extraction(
                content,
                len(facts),
                workspace_id=workspace_id,
            ):
                self._schedule_ai_extraction(
                    content=content,
                    source_id=source_id,
                    workspace_id=workspace_id,
                )
        except Exception as e:
            # Don't fail the request if extraction fails
            logger.warning("Fact extraction skipped: %s", e)

    def _normalize_memory_value_for_compare(self, value: str) -> str:
        normalized = self._normalize_text_for_trigger_matching(value or "")
        return re.sub(r"\s+", " ", normalized).strip()

    def _is_name_occupation_collision(self, occupation_value: str, name_values: set[str]) -> bool:
        occupation_norm = self._normalize_memory_value_for_compare(occupation_value)
        if not occupation_norm:
            return False

        occ_tokens = {token for token in re.split(r"[^a-z0-9]+", occupation_norm) if token}
        for name_value in name_values:
            name_norm = self._normalize_memory_value_for_compare(name_value)
            if not name_norm:
                continue

            if occupation_norm == name_norm:
                return True
            if occupation_norm in name_norm or name_norm in occupation_norm:
                return True

            name_tokens = {token for token in re.split(r"[^a-z0-9]+", name_norm) if token}
            if not occ_tokens or not name_tokens:
                continue
            overlap = len(occ_tokens & name_tokens)
            if overlap == 0:
                continue
            if overlap / len(occ_tokens) >= 0.8 or overlap / len(name_tokens) >= 0.8:
                return True

        return False

    def _filter_name_occupation_conflicts(
        self,
        facts: List[Any],
        preferences: List[Any],
    ) -> Tuple[List[Any], List[Any]]:
        """Drop occupation entries that are likely just duplicated user names."""
        name_values = {
            str(getattr(fact, "object", "")).strip()
            for fact in facts
            if str(getattr(fact, "predicate", "")).strip().lower() in {"name", "user_nickname"}
            and str(getattr(fact, "object", "")).strip()
        }
        if not name_values:
            return facts, preferences

        filtered_facts: List[Any] = []
        dropped_occupation_values: set[str] = set()
        for fact in facts:
            predicate = str(getattr(fact, "predicate", "")).strip().lower()
            fact_value = str(getattr(fact, "object", "")).strip()
            if predicate == "occupation" and self._is_name_occupation_collision(fact_value, name_values):
                dropped_occupation_values.add(self._normalize_memory_value_for_compare(fact_value))
                continue
            filtered_facts.append(fact)

        if not dropped_occupation_values:
            return filtered_facts, preferences

        filtered_preferences: List[Any] = []
        for pref in preferences:
            category = str(getattr(pref, "category", "")).strip().lower()
            key = str(getattr(pref, "key", "")).strip().lower()
            value = str(getattr(pref, "value", "")).strip()
            if (
                category == "work"
                and key == "occupation"
                and self._normalize_memory_value_for_compare(value) in dropped_occupation_values
            ):
                continue
            filtered_preferences.append(pref)

        return filtered_facts, filtered_preferences

    def _cleanup_name_conflicting_occupation_memories(
        self,
        name_values: set[str],
        existing_facts: Optional[List[LocalFact]] = None,
    ) -> set[str]:
        """Delete persisted occupation facts/preferences that collide with known user names."""
        if not name_values:
            return set()

        facts = existing_facts if existing_facts is not None else self.local.get_facts(limit=5000)
        removed_values: set[str] = set()

        for fact in facts:
            predicate = str(getattr(fact, "predicate", "")).strip().lower()
            if predicate != "occupation":
                continue
            value = str(getattr(fact, "object", "")).strip()
            if not value or not self._is_name_occupation_collision(value, name_values):
                continue
            if self.local.delete_fact(str(getattr(fact, "id", ""))):
                removed_values.add(self._normalize_memory_value_for_compare(value))

        if not removed_values:
            return set()

        for pref in self.local.get_preferences():
            category = str(getattr(pref, "category", "")).strip().lower()
            key = str(getattr(pref, "key", "")).strip().lower()
            value = str(getattr(pref, "value", "")).strip()
            if (
                category == "work"
                and key == "occupation"
                and self._normalize_memory_value_for_compare(value) in removed_values
            ):
                self.local.delete_preference(pref.id)

        return removed_values

    def _normalize_text_for_trigger_matching(self, text: str) -> str:
        lowered = (text or "").strip().lower()
        if not lowered:
            return ""
        normalized = unicodedata.normalize("NFD", lowered)
        normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
        normalized = normalized.replace("đ", "d")
        return normalized

    def _is_transient_query(self, text: str) -> bool:
        """Check if the message is a transient informational query, not a personal statement."""
        lowered = (text or "").strip().lower()
        normalized = self._normalize_text_for_trigger_matching(text)

        # Vietnamese & English question patterns about transient topics
        transient_patterns = [
            # Price/cost queries
            r"\b(?:giá|gia|price|cost|rate|tỷ giá|ti gia)\b.*\b(?:hôm nay|hom nay|today|bao nhiêu|bao nhieu|how much)\b",
            r"\b(?:hôm nay|hom nay|today|bao nhiêu|bao nhieu|how much)\b.*\b(?:giá|gia|price|cost|rate|tỷ giá|ti gia)\b",
            # Weather queries
            r"\b(?:thời tiết|thoi tiet|weather|forecast|dự báo|du bao)\b",
            # News/current events
            r"\b(?:tin tức|tin tuc|news|sự kiện|su kien|event)\b.*\b(?:hôm nay|hom nay|today|mới nhất|moi nhat|latest)\b",
            # Time queries
            r"\b(?:mấy giờ|may gio|what time|when is|bây giờ|bay gio)\b",
            # Lookup/search queries (not personal statements)
            r"\b(?:cho tôi biết|cho toi biet|tell me|what is|explain|giải thích|giai thich)\b.*\b(?:giá|gia|price|tin|news|thời tiết|thoi tiet)\b",
            # Gold/stock/crypto price queries
            r"\b(?:vàng|vang|gold|bitcoin|btc|stock|chứng khoán|chung khoan)\b.*\b(?:giá|gia|price|bao nhiêu|bao nhieu)\b",
            r"\b(?:giá|gia|price)\b.*\b(?:vàng|vang|gold|bitcoin|btc|stock|chứng khoán|chung khoan)\b",
        ]
        for pattern in transient_patterns:
            if re.search(pattern, lowered, re.IGNORECASE) or re.search(pattern, normalized, re.IGNORECASE):
                return True

        return False

    def _should_extract_facts_from_message(
        self,
        content: str,
        workspace_id: Optional[str] = None
    ) -> bool:
        """
        Gate fact extraction to reduce noisy 'remember everything' behavior.

        Extract only when user message has explicit memory/personal/project signals.
        """
        text = (content or "").strip()
        if len(text) < 8:
            return False

        if self._is_smalltalk_turn(text):
            return False

        # Skip transient queries (price checks, weather, news, etc.)
        if self._is_transient_query(text):
            return False

        lowered = text.lower()
        normalized_lowered = self._normalize_text_for_trigger_matching(text)
        tokens = self._tokenize_for_memory(text)
        has_code_block = "```" in text
        has_path_like = bool(re.search(r"([A-Za-z]:\\|[./]{1,2}|/)", text))
        has_dense_symbols = len(re.findall(r"[{}();=<>\[\]]", text)) >= 6

        explicit_memory_triggers = [
            "remember", "ghi nho", "nho rang", "dung quen", "dung quen rang",
            "luu y", "note that", "save this", "hay nho", "please remember",
            "from now on",
        ]
        personal_signals = [
        "my name", "call me", "you can call me",
        "i am", "i'm",
        "answer using", "reply using", "respond using",
        "bullet points", "avoid emoji", "action:",
        "i work as", "my job is", "my role is",
        "i live in", "i am from", "i'm from", "my birthday", "i was born",
        "i prefer", "i like", "i dislike", "my hobby",
        "i am male", "i am female", "my gender",
        "i study", "i studied", "i graduated",
        "i am single", "i am married",
        "my native language", "my mother tongue",
        # Vietnamese with diacritics
        "toi ten", "ban co the goi toi la",
        "toi la", "em la", "minh la",
        "toi lam vi tri", "toi lam vai tro", "toi lam cong viec",
        "toi song", "toi o", "toi den tu", "toi sinh",
        "toi thich", "toi ghet", "so thich cua toi",
        # Vietnamese informal pronouns (em, mình)
        "em ten", "minh ten", "ten em", "ten minh",
        "em la nam", "em la nu", "minh la nam", "minh la nu",
        "em lam", "minh lam",
        "em song", "em o", "em den tu", "minh song", "minh o",
        "em sinh", "minh sinh",
        "em thich", "minh thich", "em ghet", "minh ghet",
        "em hoc", "minh hoc", "em tot nghiep", "minh tot nghiep",
        # Gender-specific
        "gioi tinh", "toi la nam", "toi la nu", "toi la con trai", "toi la con gai",
        # Age variants
        "nam nay", "tuoi",
        # Education
        "sinh vien", "hoc sinh", "tot nghiep", "dang hoc",
        # Nationality / quê quán
        "que toi", "que em", "que minh", "toi la nguoi",
        # Marital
        "doc than", "ket hon", "co gia dinh",
    ]
        workspace_signals = [
            "project", "repo", "workspace", "codebase", "architecture",
            "du an", "trong du an", "quy uoc", "convention", "deadline", "tech stack",
        ]

        has_explicit_memory_trigger = any(
            t in lowered or t in normalized_lowered
            for t in explicit_memory_triggers
        )
        has_personal_signal = any(
            t in lowered or t in normalized_lowered
            for t in personal_signals
        )
        has_workspace_signal = any(
            t in lowered or t in normalized_lowered
            for t in workspace_signals
        )

        # Passive user-profile learning: first-person natural language statements.
        first_person_markers = [
            "i ", "i'm", "im ", "my ", "me ",
            "toi ", "minh ", "em ", "mình ", "tôi ", "em "
        ]
        has_first_person_statement = any(
            marker in lowered or marker in normalized_lowered
            for marker in first_person_markers
        )

        if has_explicit_memory_trigger:
            return True

        is_probably_pure_code = has_code_block or (has_path_like and has_dense_symbols)
        if is_probably_pure_code and not (has_personal_signal or has_workspace_signal):
            # Pure code/task turns should not become persistent memory unless explicit.
            return False

        if has_personal_signal:
            return True

        if workspace_id and has_workspace_signal:
            return True

        # Weak fallback: medium-length statement with memory signals.
        if len(tokens) >= 10 and (has_workspace_signal or has_personal_signal):
            return True

        # Broader fallback for user-centric natural language turns.
        if not is_probably_pure_code and has_first_person_statement and len(tokens) >= 8:
            return True

        return False

    def _should_run_ai_extraction(
        self,
        content: str,
        pattern_fact_count: int,
        workspace_id: Optional[str] = None
    ) -> bool:
        """Heuristic gate to control AI extraction cost/latency."""
        text = (content or "").strip()
        if len(text) < self.AI_EXTRACTION_MIN_CHARS:
            return False

        if self._is_smalltalk_turn(text):
            return False

        lowered = text.lower()
        normalized_lowered = self._normalize_text_for_trigger_matching(text)
        has_code_block = "```" in text

        explicit_memory_triggers = [
            "remember", "ghi nho", "hay nho", "please remember", "save this"
        ]
        has_explicit_memory_trigger = any(
            keyword in lowered or keyword in normalized_lowered
            for keyword in explicit_memory_triggers
        )

        trigger_keywords = [
            "my name", "call me", "you can call me",
            "i work as", "my job is", "my role is",
            "i live in", "i am from", "i'm from", "my birthday", "i was born",
            "i prefer", "i like", "i dislike", "my hobby",
            "toi ten", "ban co the goi toi la",
            "toi lam vi tri", "toi lam vai tro", "toi lam cong viec",
            "toi song", "toi o", "toi den tu", "toi sinh",
            "toi thich", "toi ghet", "so thich",
            "project", "du an", "deadline", "muc tieu", "trong du an",
        ]
        has_trigger = any(
            keyword in lowered or keyword in normalized_lowered
            for keyword in trigger_keywords
        )

        if has_explicit_memory_trigger:
            return True

        # For non-explicit messages, run AI extraction only on rich, signal-heavy turns.
        if not has_trigger:
            return False

        if has_code_block and len(text.split()) < 22:
            return False

        if workspace_id and any(k in lowered for k in ("project", "du an", "workspace")):
            return len(text.split()) >= 8

        # Pattern extractor already found enough facts -> avoid over-remembering.
        if pattern_fact_count >= 3:
            return False

        return len(text.split()) >= 12

    def _schedule_ai_extraction(
        self,
        content: str,
        source_id: Optional[str],
        workspace_id: Optional[str],
    ) -> None:
        """Schedule AI extraction asynchronously without blocking chat response."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        upstream_config = self._request_upstream_config
        ai_model = self._request_ai_model
        if upstream_config is None or not ai_model:
            return

        loop.create_task(
            self._extract_and_save_ai_facts(
                content=content,
                source_id=source_id,
                workspace_id=workspace_id,
                upstream_config=upstream_config,
                ai_model=ai_model,
            )
        )

    async def _extract_and_save_ai_facts(
        self,
        content: str,
        source_id: Optional[str],
        workspace_id: Optional[str],
        upstream_config: UpstreamRequestConfig,
        ai_model: str,
    ) -> None:
        """Run AI extraction and persist additional facts/preferences."""
        if not FACT_EXTRACTION_AVAILABLE:
            return

        try:
            extractor = self._get_fact_extractor()
            recent_user_messages = [
                str(m.get("content", ""))
                for m in list(self.working.message_buffer)[-self.AI_EXTRACTION_MAX_CONTEXT_MESSAGES:]
                if m.get("role") == "user"
            ]
            if not recent_user_messages:
                recent_user_messages = [content]

            existing_facts = self.local.get_facts(limit=200)
            ai_facts = await extractor.extract_with_ai(
                messages=recent_user_messages,
                existing_facts=existing_facts,
                workspace_id=workspace_id,
                source_id=source_id,
                max_facts=8,
                upstream_config=upstream_config,
                model_hint=ai_model,
            )
            if not ai_facts:
                return

            # Rebuild indexes to avoid conflicts with newly stored pattern facts.
            all_facts = self.local.get_facts(limit=5000)
            fact_index, single_value_index = self._build_fact_indexes(all_facts)
            for fact in ai_facts:
                self._upsert_fact(fact, fact_index, single_value_index)

            ai_preferences = extractor.extract_preferences(message=content, facts=ai_facts)
            self._upsert_preferences(ai_preferences, source_conversation_id=source_id)
            self._upsert_unified_memory(
                facts=ai_facts,
                preferences=ai_preferences,
                content=content,
                source_id=source_id,
                workspace_id=workspace_id,
            )
            self._cleanup_noisy_facts(limit=1500)
        except Exception as e:
            logger.warning("AI fact extraction skipped: %s", e)

    def _build_fact_indexes(
        self,
        facts: List[LocalFact]
    ) -> Tuple[Dict[Tuple[str, str, str, str], LocalFact], Dict[Tuple[str, str, str], LocalFact]]:
        """Build lookup maps for exact and single-value fact upserts."""
        exact_index: Dict[Tuple[str, str, str, str], LocalFact] = {}
        single_value_index: Dict[Tuple[str, str, str], LocalFact] = {}

        for fact in facts:
            exact_key = self._fact_exact_key(fact)
            exact_index[exact_key] = fact

            single_key = self._fact_single_key(fact)
            current = single_value_index.get(single_key)
            if current is None:
                single_value_index[single_key] = fact
                continue

            current_time = current.updated_at or current.created_at
            fact_time = fact.updated_at or fact.created_at
            if fact_time and current_time and fact_time > current_time:
                single_value_index[single_key] = fact

        return exact_index, single_value_index

    def _fact_exact_key(self, fact: LocalFact) -> Tuple[str, str, str, str]:
        return (
            (fact.workspace_id or "").strip().lower(),
            (fact.subject or "").strip().lower(),
            (fact.predicate or "").strip().lower(),
            (fact.object or "").strip().lower(),
        )

    def _fact_single_key(self, fact: LocalFact) -> Tuple[str, str, str]:
        return (
            (fact.workspace_id or "").strip().lower(),
            (fact.subject or "").strip().lower(),
            (fact.predicate or "").strip().lower(),
        )

    def _upsert_fact(
        self,
        new_fact: LocalFact,
        exact_index: Dict[Tuple[str, str, str, str], LocalFact],
        single_value_index: Dict[Tuple[str, str, str], LocalFact]
    ) -> None:
        """Upsert a fact with dedup + single-value replacement semantics."""
        if self._is_volatile_fact(new_fact) or not self._is_plausible_identity_fact(new_fact):
            return

        now = datetime.now()
        exact_key = self._fact_exact_key(new_fact)
        existing_exact = exact_index.get(exact_key)

        if existing_exact:
            existing_exact.confidence = max(existing_exact.confidence, new_fact.confidence)
            existing_exact.access_count = (existing_exact.access_count or 0) + 1
            existing_exact.updated_at = now
            existing_exact.source_type = new_fact.source_type
            existing_exact.source_id = new_fact.source_id
            self.local.save_fact(existing_exact)
            exact_index[exact_key] = existing_exact
            single_value_index[self._fact_single_key(existing_exact)] = existing_exact
            return

        single_key = self._fact_single_key(new_fact)
        if new_fact.predicate in self.SINGLE_VALUE_PREDICATES:
            existing_single = single_value_index.get(single_key)
            if existing_single:
                if self._should_keep_existing_single_value(existing_single, new_fact):
                    return

                old_exact_key = self._fact_exact_key(existing_single)

                existing_single.object = new_fact.object
                existing_single.category = new_fact.category
                existing_single.confidence = max(existing_single.confidence, new_fact.confidence)
                existing_single.updated_at = now
                existing_single.source_type = new_fact.source_type
                existing_single.source_id = new_fact.source_id
                self.local.save_fact(existing_single)

                exact_index.pop(old_exact_key, None)
                exact_index[self._fact_exact_key(existing_single)] = existing_single
                single_value_index[single_key] = existing_single
                return

        self.local.save_fact(new_fact)
        exact_index[exact_key] = new_fact
        single_value_index[single_key] = new_fact

    def _cleanup_noisy_facts(self, limit: int = 1500) -> int:
        """Best-effort cleanup of low-quality/volatile facts already stored."""
        removed = 0
        for fact in self.local.get_facts(limit=limit):
            if self._is_volatile_fact(fact) or not self._is_plausible_identity_fact(fact):
                if self.local.delete_fact(fact.id):
                    removed += 1
        return removed

    def _is_volatile_fact(self, fact: LocalFact) -> bool:
        predicate = (fact.predicate or "").strip().lower()
        obj = (fact.object or "").strip().lower()
        category = (fact.category or "").strip().lower()

        if not predicate or not obj:
            return True

        volatile_keywords = (
            "emotion", "sentiment", "mood", "feeling",
            "insult", "compliment", "greeting", "farewell",
            "urgent", "current_", "latest_", "recent_",
        )
        if any(keyword in predicate for keyword in volatile_keywords):
            return True

        if obj in {"true", "false", "yes", "no"}:
            if predicate.startswith(("is_", "was_", "did_", "has_")):
                return True
            if category in {"general", "communication", "relationship"}:
                return True

        return False

    def _is_plausible_identity_fact(self, fact: LocalFact) -> bool:
        predicate = (fact.predicate or "").strip().lower()
        value = (fact.object or "").strip()
        if not value:
            return False

        if predicate == "name":
            tokens = value.split()
            if not tokens or len(tokens) > 5:
                return False
            if any(any(ch.isdigit() for ch in token) for token in tokens):
                return False
            return True

        if predicate == "occupation":
            lowered = value.lower()
            if len(lowered.split()) > 6:
                return False
            if any(snippet in lowered for snippet in ("bài thơ", "bai tho", "poem", "tin nhắn", "message")):
                return False
            if re.search(r"\b(with|với|for|để|to)\b", lowered) and len(lowered.split()) >= 3:
                return False
            return True

        return True

    def _should_keep_existing_single_value(
        self,
        existing_fact: LocalFact,
        new_fact: LocalFact
    ) -> bool:
        if (existing_fact.object or "").strip().lower() == (new_fact.object or "").strip().lower():
            return False

        predicate = (new_fact.predicate or "").strip().lower()
        if predicate not in self.GUARDED_SINGLE_VALUE_PREDICATES:
            return False

        if (new_fact.source_type or "").strip().lower() == "user_input":
            return False

        existing_conf = float(existing_fact.confidence or 0.0)
        new_conf = float(new_fact.confidence or 0.0)

        if predicate == "name" and new_conf < 0.9:
            return True
        if predicate == "occupation" and new_conf < 0.84:
            return True

        # Prevent low-confidence overwrite drift for core profile fields.
        return new_conf + 0.06 < existing_conf

    def _upsert_preferences(
        self,
        preferences: List[Any],
        source_conversation_id: Optional[str] = None
    ) -> None:
        """Upsert extracted preferences by (category, key)."""
        if not preferences:
            return

        existing_prefs = self.local.get_preferences()
        pref_index: Dict[Tuple[str, str], LocalUserPreference] = {
            (p.category.strip().lower(), p.key.strip().lower()): p
            for p in existing_prefs
        }

        now = datetime.now()
        for pref in preferences:
            category = str(getattr(pref, "category", "")).strip()
            key = str(getattr(pref, "key", "")).strip()
            value = str(getattr(pref, "value", "")).strip()
            confidence = float(getattr(pref, "confidence", 0.8))

            if not category or not key or not value:
                continue

            lookup_key = (category.lower(), key.lower())
            existing = pref_index.get(lookup_key)
            if existing:
                existing.value = value
                existing.confidence = max(existing.confidence, confidence)
                existing.source_conversation_id = source_conversation_id or existing.source_conversation_id
                existing.updated_at = now
                self.local.save_preference(existing)
                pref_index[lookup_key] = existing
                continue

            new_pref = LocalUserPreference(
                id=str(uuid.uuid4()),
                category=category,
                key=key,
                value=value,
                confidence=confidence,
                source_conversation_id=source_conversation_id,
                created_at=now,
                updated_at=now
            )
            self.local.save_preference(new_pref)
            pref_index[lookup_key] = new_pref

    def _slugify_memory_key(self, value: str) -> str:
        slug = re.sub(r"[^a-z0-9_]+", "_", (value or "").strip().lower()).strip("_")
        return slug[:64] if slug else ""

    def _normalize_memory_value_text(self, value: str) -> str:
        normalized = self._normalize_text_for_trigger_matching(value or "")
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized

    def _canonicalize_memory_key(self, raw_key: str) -> str:
        slug_key = self._slugify_memory_key(raw_key)
        if not slug_key:
            return ""
        return self.MEMORY_KEY_ALIASES.get(slug_key, slug_key)

    def _canonicalize_memory_value(self, key: str, raw_value: str) -> str:
        value = (raw_value or "").strip()
        if not value:
            return ""

        alias_map = self.MEMORY_VALUE_ALIASES.get(key)
        if not alias_map:
            return value

        normalized = self._normalize_memory_value_text(value)
        return alias_map.get(normalized, value)

    def _memory_similarity(self, left: str, right: str) -> float:
        left_norm = self._normalize_memory_value_text(left)
        right_norm = self._normalize_memory_value_text(right)
        if not left_norm or not right_norm:
            return 0.0
        if left_norm == right_norm:
            return 1.0

        ratio = SequenceMatcher(None, left_norm, right_norm).ratio()
        left_tokens = set(token for token in left_norm.split(" ") if token)
        right_tokens = set(token for token in right_norm.split(" ") if token)
        token_overlap = 0.0
        if left_tokens and right_tokens:
            token_overlap = len(left_tokens & right_tokens) / max(len(left_tokens), len(right_tokens))
        return max(ratio, token_overlap)

    def _is_temporary_candidate(self, content: str, value: str) -> bool:
        text = f"{content or ''} {value or ''}".lower()
        normalized = self._normalize_text_for_trigger_matching(text)
        return any(marker in text or marker in normalized for marker in self.TEMPORAL_MARKERS)

    def _infer_assertion_type_from_fact(self, key: str, category: str) -> str:
        identity_keys = {
            "user_name",
            "user_nickname",
            "user_age",
            "gender",
            "location",
            "timezone",
            "occupation",
            "company",
            "education",
            "nationality",
            "native_language",
            "marital_status",
        }
        if key in identity_keys:
            return "identity"

        preference_keys = {
            "response_language",
            "response_style",
            "tone_preference",
            "emoji_usage",
            "primary_language",
            "editor",
            "preferred_os",
            "likes",
            "dislikes",
            "favorite",
            "hobby",
        }
        if key in preference_keys:
            return "preference"

        if (category or "").strip().lower() in {"preference", "coding_style", "communication"}:
            return "preference"
        return "fact"

    def _infer_assertion_scope(
        self,
        workspace_id: Optional[str],
        conversation_id: Optional[str],
        assertion_type: str
    ) -> str:
        if assertion_type == "temporary" and conversation_id:
            return "conversation"
        if workspace_id:
            return "workspace"
        return "user"

    def _is_explicit_preference_change(self, content: str) -> bool:
        lowered = (content or "").strip().lower()
        normalized = self._normalize_text_for_trigger_matching(content)
        explicit_patterns = (
            "change",
            "update",
            "from now on",
            "cap nhat",
            "thay doi",
            "tu gio",
            "hãy đổi",
            "hay doi",
            "please change",
            "remember this change",
        )
        return any(pattern in lowered or pattern in normalized for pattern in explicit_patterns)

    def _append_memory_evidence(
        self,
        *,
        assertion_id: Optional[str],
        key: str,
        value: str,
        normalized_value: str,
        confidence: float,
        source_type: str,
        source_id: Optional[str],
        workspace_id: Optional[str],
        conversation_id: Optional[str],
        category: str,
        scope: str,
        assertion_type: str,
        raw_snippet: Optional[str],
    ) -> Optional[LocalMemoryEvidence]:
        try:
            evidence = LocalMemoryEvidence(
                id=str(uuid.uuid4()),
                user_id=self.local_user_id,
                assertion_id=assertion_id,
                type=assertion_type,
                scope=scope,
                key=key,
                value=value,
                normalized_value=normalized_value,
                confidence=max(0.0, min(1.0, float(confidence or 0.0))),
                source_type=source_type,
                source_id=source_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                category=category,
                raw_snippet=raw_snippet,
                created_at=datetime.now(),
            )
            self.local.save_memory_evidence(evidence)
            return evidence
        except Exception as e:
            logger.warning("memory evidence save skipped key=%s error=%s", key, e)
            return None

    def _upsert_unified_memory(
        self,
        *,
        facts: List[LocalFact],
        preferences: List[Any],
        content: str,
        source_id: Optional[str],
        workspace_id: Optional[str],
    ) -> None:
        """
        Convert extracted facts/preferences into canonical assertions with:
        - canonical key/value
        - dedupe / drift control
        - temporary TTL
        - confirmation workflow for sensitive single-value changes
        """
        candidates: List[Dict[str, Any]] = []

        for fact in facts:
            raw_key = str(getattr(fact, "predicate", "") or "").strip()
            key = self._canonicalize_memory_key(raw_key)
            raw_value = str(getattr(fact, "object", "") or "").strip()
            value = self._canonicalize_memory_value(key, raw_value)
            if not key or not value:
                continue

            base_type = self._infer_assertion_type_from_fact(
                key=key,
                category=str(getattr(fact, "category", "general") or "general"),
            )
            assertion_type = "temporary" if self._is_temporary_candidate(content, value) else base_type
            fact_workspace_id = getattr(fact, "workspace_id", None) or workspace_id
            scope = self._infer_assertion_scope(
                workspace_id=fact_workspace_id,
                conversation_id=source_id,
                assertion_type=assertion_type
            )

            candidates.append(
                {
                    "key": key,
                    "value": value,
                    "normalized_value": self._normalize_memory_value_text(value),
                    "type": assertion_type,
                    "scope": scope,
                    "workspace_id": fact_workspace_id if scope == "workspace" else None,
                    "conversation_id": source_id if scope == "conversation" else None,
                    "category": str(getattr(fact, "category", "general") or "general"),
                    "confidence": max(0.0, min(1.0, float(getattr(fact, "confidence", 0.0) or 0.0))),
                    "source_type": str(getattr(fact, "source_type", "pattern_extraction") or "pattern_extraction"),
                    "source_id": source_id,
                }
            )

        for pref in preferences:
            raw_key = str(getattr(pref, "key", "") or "").strip()
            key = self._canonicalize_memory_key(raw_key)
            raw_value = str(getattr(pref, "value", "") or "").strip()
            value = self._canonicalize_memory_value(key, raw_value)
            if not key or not value:
                continue

            assertion_type = "temporary" if self._is_temporary_candidate(content, value) else self._infer_assertion_type_from_fact(
                key=key,
                category=str(getattr(pref, "category", "preference") or "preference"),
            )
            scope = self._infer_assertion_scope(workspace_id=workspace_id, conversation_id=source_id, assertion_type=assertion_type)

            candidates.append(
                {
                    "key": key,
                    "value": value,
                    "normalized_value": self._normalize_memory_value_text(value),
                    "type": assertion_type,
                    "scope": scope,
                    "workspace_id": workspace_id if scope == "workspace" else None,
                    "conversation_id": source_id if scope == "conversation" else None,
                    "category": str(getattr(pref, "category", "preference") or "preference"),
                    "confidence": max(0.0, min(1.0, float(getattr(pref, "confidence", 0.0) or 0.0))),
                    "source_type": "pattern_extraction",
                    "source_id": source_id,
                }
            )

        if not candidates:
            return

        deduped: Dict[Tuple[str, str, str, str, str], Dict[str, Any]] = {}
        for candidate in candidates:
            dedup_key = (
                candidate["scope"],
                (candidate.get("workspace_id") or "").strip(),
                (candidate.get("conversation_id") or "").strip(),
                candidate["key"],
                candidate["normalized_value"],
            )
            prev = deduped.get(dedup_key)
            if prev is None or float(candidate["confidence"]) >= float(prev["confidence"]):
                deduped[dedup_key] = candidate

        for candidate in deduped.values():
            assertion_type = candidate["type"]
            confidence = float(candidate["confidence"])
            min_conf = self.MIN_ASSERTION_CONFIDENCE_BY_TYPE.get(assertion_type, 0.80)
            if confidence < min_conf:
                self._append_memory_evidence(
                    assertion_id=None,
                    key=candidate["key"],
                    value=candidate["value"],
                    normalized_value=candidate["normalized_value"],
                    confidence=confidence,
                    source_type=candidate["source_type"],
                    source_id=candidate["source_id"],
                    workspace_id=candidate["workspace_id"],
                    conversation_id=candidate["conversation_id"],
                    category=candidate["category"],
                    scope=candidate["scope"],
                    assertion_type=assertion_type,
                    raw_snippet=None,
                )
                continue

            existing = self.local.find_active_memory_assertion(
                key=candidate["key"],
                scope=candidate["scope"],
                workspace_id=candidate["workspace_id"],
                conversation_id=candidate["conversation_id"],
            )

            if existing is None:
                now = datetime.now()
                created = LocalMemoryAssertion(
                    id=str(uuid.uuid4()),
                    user_id=self.local_user_id,
                    type=assertion_type,
                    scope=candidate["scope"],
                    key=candidate["key"],
                    value=candidate["value"],
                    workspace_id=candidate["workspace_id"],
                    conversation_id=candidate["conversation_id"],
                    category=candidate["category"],
                    confidence=confidence,
                    access_count=0,
                    status="active",
                    expires_at=(
                        now + timedelta(days=self.TEMPORARY_ASSERTION_TTL_DAYS)
                        if assertion_type == "temporary"
                        else None
                    ),
                    source_evidence_id=None,
                    created_at=now,
                    updated_at=now,
                )
                # Persist the canonical row first so evidence can safely
                # reference it when SQLite foreign keys are enabled.
                self.local.save_memory_assertion(created)
                evidence = self._append_memory_evidence(
                    assertion_id=created.id,
                    key=created.key,
                    value=created.value,
                    normalized_value=candidate["normalized_value"],
                    confidence=confidence,
                    source_type=candidate["source_type"],
                    source_id=candidate["source_id"],
                    workspace_id=created.workspace_id,
                    conversation_id=created.conversation_id,
                    category=created.category,
                    scope=created.scope,
                    assertion_type=created.type,
                    raw_snippet=None,
                )
                if evidence:
                    created.source_evidence_id = evidence.id
                    self.local.save_memory_assertion(created)
                continue

            similarity = self._memory_similarity(existing.value, candidate["value"])
            if (
                candidate["key"] in self.KEY_SINGLE_VALUE
                and self._normalize_memory_value_text(existing.value) != candidate["normalized_value"]
            ):
                # Single-value fields must flow through confirmation/explicit-update
                # logic instead of semantic drift merge, otherwise structured ids
                # like NAME-...-OLD/NEW collapse incorrectly.
                similarity = 0.0
            evidence = self._append_memory_evidence(
                assertion_id=existing.id,
                key=candidate["key"],
                value=candidate["value"],
                normalized_value=candidate["normalized_value"],
                confidence=confidence,
                source_type=candidate["source_type"],
                source_id=candidate["source_id"],
                workspace_id=candidate["workspace_id"],
                conversation_id=candidate["conversation_id"],
                category=candidate["category"],
                scope=candidate["scope"],
                assertion_type=assertion_type,
                raw_snippet=None,
            )

            # Drift merge: semantic duplicates map to one canonical value.
            if similarity >= self.MEMORY_DRIFT_MERGE_SIMILARITY:
                existing.confidence = max(float(existing.confidence or 0.0), confidence)
                existing.updated_at = datetime.now()
                existing.source_evidence_id = evidence.id if evidence else existing.source_evidence_id
                if assertion_type == "temporary":
                    existing.expires_at = datetime.now() + timedelta(days=self.TEMPORARY_ASSERTION_TTL_DAYS)
                self.local.save_memory_assertion(existing)
                continue

            # Single-value sensitive keys require confirmation by default.
            if (
                candidate["key"] in self.KEY_SINGLE_VALUE
                and assertion_type != "temporary"
                and not self._is_explicit_preference_change(content)
            ):
                pending_exists = any(
                    p.assertion_id == existing.id and self._normalize_memory_value_text(p.proposed_value) == candidate["normalized_value"]
                    for p in self.local.get_pending_memory_changes(status="pending", limit=200)
                )
                if not pending_exists:
                    pending = LocalMemoryPendingChange(
                        id=str(uuid.uuid4()),
                        user_id=self.local_user_id,
                        assertion_id=existing.id,
                        key=candidate["key"],
                        old_value=existing.value,
                        proposed_value=candidate["value"],
                        proposed_confidence=confidence,
                        type=assertion_type,
                        scope=candidate["scope"],
                        workspace_id=candidate["workspace_id"],
                        conversation_id=candidate["conversation_id"],
                        source_evidence_id=evidence.id if evidence else None,
                        reason="requires_confirmation",
                        status="pending",
                        created_at=datetime.now(),
                    )
                    self.local.save_pending_memory_change(pending)
                continue

            # Allow overwrite for explicit or stronger updates.
            if confidence + 0.03 >= float(existing.confidence or 0.0):
                existing.value = candidate["value"]
                existing.type = assertion_type
                existing.category = candidate["category"]
                existing.confidence = max(float(existing.confidence or 0.0), confidence)
                existing.updated_at = datetime.now()
                existing.source_evidence_id = evidence.id if evidence else existing.source_evidence_id
                existing.expires_at = (
                    datetime.now() + timedelta(days=self.TEMPORARY_ASSERTION_TTL_DAYS)
                    if assertion_type == "temporary"
                    else None
                )
                self.local.save_memory_assertion(existing)

        try:
            self.local.cleanup_expired_memory_assertions()
        except Exception as e:
            logger.debug("memory assertion cleanup skipped: %s", e)
    
    def _get_fact_extractor(self):
        """Lazy load fact extractor"""
        if self._fact_extractor is None and FACT_EXTRACTION_AVAILABLE:
            self._fact_extractor = get_fact_extractor(self.local_user_id)
        return self._fact_extractor
    
    def _embed_message_async(self, message_id: str, content: str):
        """Generate and save embedding for a message without blocking request flow."""
        if not EMBEDDINGS_AVAILABLE:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Non-async context fallback.
            self._embed_message_sync(message_id, content)
            return

        loop.create_task(self._embed_message_background(message_id, content))

    async def _embed_message_background(self, message_id: str, content: str):
        """Background embedding task."""
        try:
            embedding_bytes = await asyncio.to_thread(self._embed_content_to_bytes, content)
            with self.local._get_connection() as conn:
                conn.execute(
                    "UPDATE messages SET embedding = ? WHERE id = ?",
                    (embedding_bytes, message_id)
                )
        except Exception as e:
            # Don't fail the request if embedding fails
            logger.warning("Embedding generation skipped: %s", e)

    def _embed_message_sync(self, message_id: str, content: str):
        """Synchronous embedding fallback when no event loop is available."""
        try:
            embedding_bytes = self._embed_content_to_bytes(content)
            with self.local._get_connection() as conn:
                conn.execute(
                    "UPDATE messages SET embedding = ? WHERE id = ?",
                    (embedding_bytes, message_id)
                )
        except Exception as e:
            logger.warning("Embedding generation skipped: %s", e)

    def _embed_content_to_bytes(self, content: str) -> bytes:
        service = self._get_embedding_service()
        return service.embed_to_bytes(content)
    
    def _get_embedding_service(self):
        """Lazy load embedding service"""
        if self._embedding_service is None and EMBEDDINGS_AVAILABLE:
            self._embedding_service = get_embedding_service()
        return self._embedding_service

    # =========================================================================
    # Rules (LOCAL PIGTEX.md)
    # =========================================================================

    def _rules_file_path(self, workspace_id: Optional[str] = None) -> Path:
        """Resolve path to global/workspace local rules file."""
        base = get_storage_dir(self.local_user_id) / "brain" / "rules"
        if workspace_id:
            base = base / "workspaces" / workspace_id
        return base / "PIGTEX.md"

    def _read_rules_file(self, workspace_id: Optional[str] = None) -> str:
        """Read rules file content safely."""
        path = self._rules_file_path(workspace_id)
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning("Rules load skipped (%s): %s", path, e)
            return ""

    def get_rules_context(self, workspace_id: Optional[str] = None) -> str:
        """Get merged rules context: global + optional workspace rules."""
        global_rules = self._read_rules_file(None)
        workspace_rules = self._read_rules_file(workspace_id) if workspace_id else ""

        sections: List[str] = []
        if global_rules:
            sections.append(f"## Global Rules\n{global_rules}")
        if workspace_rules:
            sections.append(f"## Workspace Rules\n{workspace_rules}")

        return "\n\n".join(sections)
    
    # =========================================================================
    # Knowledge Management (LOCAL)
    # =========================================================================

    def _build_knowledge_search_queries(self, query: str) -> List[str]:
        """Build fallback search queries from a user message."""
        raw = (query or "").strip()
        if not raw:
            return []

        tokens = re.findall(r"\w+", raw.lower(), flags=re.UNICODE)
        if not tokens:
            return [raw]

        unique_tokens: List[str] = []
        seen_tokens: set[str] = set()
        for token in tokens:
            if token in seen_tokens:
                continue
            seen_tokens.add(token)
            unique_tokens.append(token)

        signal_tokens = [
            token for token in unique_tokens
            if "_" in token or any(ch.isdigit() for ch in token) or len(token) >= 10
        ]
        content_tokens = [
            token for token in unique_tokens
            if len(token) >= 3 and token not in self.KNOWLEDGE_SEARCH_STOPWORDS
        ]

        candidates = [raw]
        if signal_tokens:
            candidates.append(" ".join(signal_tokens[:4]))
        if content_tokens:
            candidates.append(" ".join(content_tokens[:6]))
        if len(content_tokens) >= 2:
            candidates.append(" ".join(content_tokens[-2:]))

        result: List[str] = []
        seen_queries: set[str] = set()
        for candidate in candidates:
            normalized = candidate.strip().lower()
            if not normalized or normalized in seen_queries:
                continue
            seen_queries.add(normalized)
            result.append(candidate.strip())

        return result

    def _score_knowledge_item(
        self,
        item: LocalKnowledgeItem,
        query_tokens: List[str],
        source_score: float,
        workspace_focused: bool
    ) -> float:
        title = (item.title or "").strip()
        summary = (item.summary or "").strip()
        body = (item.content or "")[:1200]
        combined_text = f"{title} {summary} {body}"

        overlap = self._compute_overlap_score(combined_text, query_tokens)
        title_overlap = self._compute_overlap_score(title, query_tokens)
        recency = self._recency_score(item.updated_at, item.created_at)
        importance = max(0.0, min(1.0, float(item.importance_score or 0.0)))
        access_score = min(1.0, max(0, int(item.access_count or 0)) / 8.0)
        favorite_boost = 0.05 if bool(item.is_favorite) else 0.0
        pinned_boost = 0.08 if bool(item.is_pinned) else 0.0
        scope_boost = 0.04 if (workspace_focused and item.workspace_id) else 0.0

        score = (
            (0.42 * max(0.0, min(1.0, source_score)))
            + (0.24 * overlap)
            + (0.12 * title_overlap)
            + (0.08 * recency)
            + (0.06 * importance)
            + (0.03 * access_score)
            + favorite_boost
            + pinned_boost
            + scope_boost
        )
        return score

    def _build_knowledge_snippet(
        self,
        content: str,
        query_tokens: List[str],
        max_chars: int
    ) -> str:
        """Extract a focused snippet around matched query terms."""
        text = (content or "").strip()
        if not text:
            return ""
        if len(text) <= max_chars:
            return text

        lowered = text.lower()
        anchor = -1
        for token in query_tokens:
            if len(token) < 4:
                continue
            idx = lowered.find(token.lower())
            if idx != -1:
                anchor = idx
                break

        if anchor == -1:
            return f"{text[:max_chars].rstrip()}..."

        half = max_chars // 2
        start = max(0, anchor - half)
        end = min(len(text), start + max_chars)
        if end - start < max_chars and start > 0:
            start = max(0, end - max_chars)

        if start > 0:
            nearest_space = text.rfind(" ", start, min(len(text), start + 40))
            if nearest_space != -1:
                start = nearest_space + 1

        snippet = text[start:end].strip()
        if start > 0:
            snippet = f"...{snippet}"
        if end < len(text):
            snippet = f"{snippet}..."
        return snippet

    def _touch_knowledge_items(self, items: List[LocalKnowledgeItem]) -> None:
        """Best-effort mark selected knowledge items as recently used."""
        if not items:
            return

        for item in items:
            try:
                self.local.touch_knowledge_item(item.id)
            except Exception as e:
                logger.debug("knowledge touch skipped id=%s error=%s", item.id, e)
    
    def search_knowledge(
        self,
        query: str,
        top_k: int = 5,
        use_vector: bool = True,
        workspace_id: Optional[str] = None
    ) -> List[LocalKnowledgeItem]:
        """Search knowledge items using hybrid retrieval + reranking."""
        search_queries = self._build_knowledge_search_queries(query)
        if not search_queries:
            return []

        query_tokens = self._tokenize_for_memory(query)
        workspace_focused = workspace_id is not None and workspace_id != ""
        candidate_limit_vector = max(top_k * self.KNOWLEDGE_VECTOR_CANDIDATE_MULTIPLIER, top_k + 4)
        candidate_limit_fts = max(top_k * self.KNOWLEDGE_FTS_CANDIDATE_MULTIPLIER, top_k + 6)

        # id -> (item, best_source_score)
        candidates: Dict[str, Tuple[LocalKnowledgeItem, float]] = {}

        if use_vector and EMBEDDINGS_AVAILABLE and self._embeddings_enabled:
            try:
                service = self._get_embedding_service()
                query_embedding = service.embed(search_queries[0])
                vector_results = self.local.search_knowledge_vector(
                    query_embedding,
                    limit=candidate_limit_vector,
                    min_similarity=self.KNOWLEDGE_VECTOR_MIN_SIMILARITY,
                    workspace_id=workspace_id
                )
                for item, similarity in vector_results:
                    previous = candidates.get(item.id)
                    if previous is None or similarity > previous[1]:
                        candidates[item.id] = (item, similarity)
            except Exception as e:
                logger.warning("Vector search failed; continuing with FTS: %s", e)

        # FTS retrieval with progressively focused fallback queries.
        for query_index, candidate_query in enumerate(search_queries):
            fts_results = self.local.search_knowledge_fts(
                candidate_query,
                limit=candidate_limit_fts,
                workspace_id=workspace_id
            )
            base_source_score = 0.44 if query_index == 0 else 0.34
            for item in fts_results:
                previous = candidates.get(item.id)
                if previous is None or base_source_score > previous[1]:
                    candidates[item.id] = (item, base_source_score)

        if not candidates:
            return []

        scored_items: List[Tuple[float, LocalKnowledgeItem]] = []
        for item, source_score in candidates.values():
            score = self._score_knowledge_item(
                item=item,
                query_tokens=query_tokens,
                source_score=source_score,
                workspace_focused=workspace_focused
            )
            scored_items.append((score, item))

        scored_items.sort(
            key=lambda row: (
                row[0],
                row[1].updated_at or row[1].created_at or datetime.min
            ),
            reverse=True
        )

        return [item for _, item in scored_items[:top_k]]
    
    def search_messages_semantic(
        self,
        query: str,
        conversation_id: Optional[str] = None,
        top_k: int = 5,
        min_similarity: float = 0.4
    ) -> List[LocalMessage]:
        """
        Semantic search through past messages.
        Useful for finding relevant context from conversation history.
        """
        if not EMBEDDINGS_AVAILABLE:
            return []
        
        try:
            service = self._get_embedding_service()
            query_embedding = service.embed(query)
            
            results = self.local.search_messages_vector(
                query_embedding,
                conversation_id=conversation_id,
                limit=top_k,
                min_similarity=min_similarity
            )
            return [msg for msg, score in results]
        except Exception as e:
            logger.warning("Message search failed: %s", e)
            return []
    
    def get_user_facts(
        self,
        subject: str = "User",
        workspace_id: Optional[str] = None,
        include_system: bool = True
    ) -> List[LocalFact]:
        """
        Get facts about a subject with scope control.

        include_system=True:
        - workspace_id=None  -> only system facts
        - workspace_id=<id>  -> system facts + that workspace's facts
        """
        facts: List[LocalFact] = []

        if include_system:
            facts.extend(
                self.local.get_facts(
                    subject=subject,
                    workspace_id=None,
                    limit=5000,
                )
            )

        if workspace_id:
            facts.extend(
                self.local.get_facts(
                    subject=subject,
                    workspace_id=workspace_id,
                    limit=5000,
                )
            )

        # De-duplicate by id in case callers pass overlapping scopes.
        unique: Dict[str, LocalFact] = {f.id: f for f in facts}
        facts = list(unique.values())
        facts.sort(
            key=lambda f: (f.updated_at or f.created_at or datetime.min),
            reverse=True
        )
        return facts
    
    def get_user_preferences(self, category: Optional[str] = None) -> Dict[str, str]:
        """Get user preferences as dict"""
        prefs = self.local.get_preferences(category)
        prefs.sort(
            key=lambda p: (p.updated_at or p.created_at or datetime.min),
            reverse=True
        )
        result: Dict[str, str] = {}
        for pref in prefs:
            if pref.key not in result:
                result[pref.key] = pref.value
        return result

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimate used for memory budget allocation."""
        return int(len((text or "").split()) * 1.3)

    def _tokenize_for_memory(self, text: str) -> List[str]:
        normalized_text = (text or "").replace("_", " ")
        raw_tokens = re.findall(r"\w+", normalized_text.lower(), flags=re.UNICODE)
        if not raw_tokens:
            return []
        return [
            token for token in raw_tokens
            if len(token) >= 3 and token not in self.KNOWLEDGE_SEARCH_STOPWORDS
        ]

    def _build_query_tokens(
        self,
        user_message: str,
        intent: Optional[str],
        keywords: Optional[List[str]]
    ) -> List[str]:
        """
        Build a richer query token set for retrieval/scoring.
        Combines user message, intent label, and extracted keywords.
        """
        merged: List[str] = []
        seen: set[str] = set()

        sources: List[str] = [user_message or ""]
        if intent:
            sources.append(intent.replace("_", " "))
        if keywords:
            sources.extend(str(keyword) for keyword in keywords if keyword)

        for source in sources:
            for token in self._tokenize_for_memory(source):
                if token in seen:
                    continue
                seen.add(token)
                merged.append(token)
                if len(merged) >= self.MAX_QUERY_TOKENS:
                    return merged

        return merged

    def _is_smalltalk_turn(
        self,
        message: str,
        intent: Optional[str] = None
    ) -> bool:
        text = (message or "").strip().lower()
        if not text:
            return False

        if intent in {"debug", "code_generation", "code_review", "analysis", "planning", "learning", "research"}:
            return False

        explicit_memory_markers = ("remember", "ghi nho", "save this", "my name", "toi ten", "project", "workspace")
        if any(marker in text for marker in explicit_memory_markers):
            return False

        if re.fullmatch(
            r"(hi|hello|hey|yo|alo|chào|xin chào|ok+|okay+|oke+|thanks?|cảm ơn|cam on|hmm+|hmmm+|hello there)[!. ]*",
            text
        ):
            return True

        raw_tokens = re.findall(r"\w+", text, flags=re.UNICODE)
        if not raw_tokens or len(raw_tokens) > self.SMALLTALK_TOKEN_LIMIT:
            return False

        smalltalk_tokens = {
            "hi", "hello", "hey", "yo", "alo", "chào", "xin", "ok", "okay", "oke",
            "thanks", "thank", "cảm", "ơn", "cam", "on", "hmm", "hmmm",
        }
        return all(token in smalltalk_tokens for token in raw_tokens)

    def _is_workspace_focused_query(
        self,
        message: str,
        tokens: List[str],
        intent: Optional[str] = None
    ) -> bool:
        if self.working.workspace_id is None:
            return False

        lowered = (message or "").lower()
        workspace_signals = {
            "project", "workspace", "repo", "branch", "commit", "deploy",
            "file", "folder", "directory", "codebase", "readme", "architecture",
            "convention", "tech", "stack", "module", "bug", "fix",
            "du", "an", "thu", "muc", "tep", "ma", "nguon", "kien", "truc",
        }

        if intent in {"debug", "code_generation", "code_review"}:
            return True

        if any(token in workspace_signals for token in tokens):
            return True

        if any(marker in lowered for marker in ("./", "../", "\\", "src/", "backend/", "frontend/")):
            return True

        return False

    def _compute_overlap_score(self, source_text: str, query_tokens: List[str]) -> float:
        if not query_tokens:
            return 0.0

        source_tokens = set(self._tokenize_for_memory(source_text))
        if not source_tokens:
            return 0.0

        overlap = sum(1 for token in query_tokens if token in source_tokens)
        return min(1.0, overlap / max(1, min(6, len(query_tokens))))

    def _recency_score(self, updated_at: Optional[datetime], created_at: Optional[datetime]) -> float:
        timestamp = updated_at or created_at
        if not timestamp:
            return 0.0

        age_seconds = max(0.0, (datetime.now() - timestamp).total_seconds())
        age_days = age_seconds / 86400.0
        # 0 day -> 1.0, 30 days -> ~0.5, 90 days -> ~0.25
        return 1.0 / (1.0 + (age_days / 30.0))

    def _memory_repeat_penalty(self, fingerprint: str) -> float:
        turns_since = self.working.turns_since_memory_injection(fingerprint)
        if turns_since is None:
            return 0.0
        if turns_since <= self.MEMORY_REPEAT_HARD_COOLDOWN_TURNS:
            return 1.0
        if turns_since <= self.MEMORY_REPEAT_SOFT_COOLDOWN_TURNS:
            return 0.35
        return 0.0

    def _memory_fingerprint_for_preference(self, pref: LocalUserPreference) -> str:
        return "|".join([
            "pref",
            (pref.category or "").strip().lower(),
            (pref.key or "").strip().lower(),
            (pref.value or "").strip().lower(),
        ])

    def _memory_fingerprint_for_fact(self, fact: LocalFact) -> str:
        return "|".join([
            "fact",
            (fact.scope or "").strip().lower(),
            (fact.workspace_id or "").strip().lower(),
            (fact.subject or "").strip().lower(),
            (fact.predicate or "").strip().lower(),
            (fact.object or "").strip().lower(),
        ])

    def _memory_fingerprint_for_assertion(self, assertion: LocalMemoryAssertion) -> str:
        return "|".join([
            "assertion",
            (assertion.type or "").strip().lower(),
            (assertion.scope or "").strip().lower(),
            (assertion.workspace_id or "").strip().lower(),
            (assertion.conversation_id or "").strip().lower(),
            (assertion.key or "").strip().lower(),
            (assertion.value or "").strip().lower(),
        ])

    def _score_memory_assertion(
        self,
        assertion: LocalMemoryAssertion,
        query_tokens: List[str],
        workspace_focused: bool
    ) -> float:
        text = f"{assertion.key} {assertion.value} {assertion.category or ''}"
        overlap = self._compute_overlap_score(text, query_tokens)
        confidence = max(0.0, min(1.0, float(assertion.confidence or 0.0)))
        recency = self._recency_score(assertion.updated_at, assertion.created_at)
        access = max(0, int(assertion.access_count or 0))
        access_score = min(1.0, access / 6.0)
        repeat_penalty = self._memory_repeat_penalty(self._memory_fingerprint_for_assertion(assertion))

        scope = (assertion.scope or "user").strip().lower()
        scope_boost = 0.0
        if scope == "workspace":
            scope_boost = 0.12 if workspace_focused else 0.04
        elif scope == "conversation":
            scope_boost = 0.08
        else:
            scope_boost = 0.07 if not workspace_focused else 0.03

        assertion_type = (assertion.type or "fact").strip().lower()
        sticky_keys = {
            "response_language",
            "response_style",
            "tone_preference",
            "primary_language",
            "emoji_usage",
        }
        if assertion_type == "identity":
            type_boost = 0.20
        elif assertion_type == "preference":
            type_boost = 0.14 if assertion.key in sticky_keys else 0.07
        elif assertion_type == "temporary":
            type_boost = -0.03
        else:
            type_boost = 0.04

        return (
            (0.48 * overlap)
            + (0.22 * confidence)
            + (0.14 * recency)
            + (0.06 * access_score)
            + scope_boost
            + type_boost
            - repeat_penalty
        )

    def _format_assertion_for_context(self, assertion: LocalMemoryAssertion) -> str:
        key_label = assertion.key.replace("_", " ")
        value = assertion.value
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{4,63}", value or ""):
            line = f"exact {key_label} value: {value}"
        else:
            line = f"{key_label}: {value}"
        scope = (assertion.scope or "").strip().lower()
        if scope == "workspace":
            return f"{line} (workspace)"
        if scope == "conversation":
            return f"{line} (current conversation)"
        return line

    def _select_relevant_assertions(
        self,
        query_tokens: List[str],
        workspace_focused: bool,
        assertions: Optional[List[LocalMemoryAssertion]] = None,
    ) -> Dict[str, List[str]]:
        picked: Dict[str, List[str]] = {
            "identity": [],
            "preference": [],
            "fact": [],
        }
        if not assertions:
            return picked

        active_assertions = [
            assertion
            for assertion in assertions
            if (assertion.status or "active") == "active" and not assertion.is_expired
        ]
        if not active_assertions:
            return picked

        token_budget = self.MAX_USER_CONTEXT_TOKENS
        used_tokens = 0

        def _pick_for_type(target_type: str, limit: int) -> List[str]:
            nonlocal used_tokens
            scored: List[Tuple[float, LocalMemoryAssertion]] = []

            for assertion in active_assertions:
                assertion_type = (assertion.type or "fact").strip().lower()
                if target_type == "fact":
                    if assertion_type not in {"fact", "temporary"}:
                        continue
                elif assertion_type != target_type:
                    continue

                min_conf = self.MIN_ASSERTION_CONFIDENCE_BY_TYPE.get(assertion_type, 0.85)
                if float(assertion.confidence or 0.0) < min_conf:
                    continue

                text = f"{assertion.key} {assertion.value} {assertion.category or ''}"
                overlap = self._compute_overlap_score(text, query_tokens)

                if target_type == "preference":
                    sticky = assertion.key in {
                        "response_language",
                        "response_style",
                        "tone_preference",
                        "primary_language",
                        "emoji_usage",
                    }
                    if query_tokens and overlap < 0.05 and not sticky:
                        continue
                    if not query_tokens and not sticky:
                        continue
                elif target_type == "fact":
                    allow_scope_fallback = workspace_focused and (assertion.scope == "workspace")
                    if query_tokens and overlap < 0.08 and not allow_scope_fallback:
                        continue
                    if not query_tokens and not allow_scope_fallback:
                        continue

                score = self._score_memory_assertion(assertion, query_tokens, workspace_focused)
                if target_type == "preference" and score <= 0.22:
                    continue
                if target_type == "fact" and score <= 0.24:
                    continue
                scored.append((score, assertion))

            scored.sort(
                key=lambda item: (
                    item[0],
                    item[1].updated_at or item[1].created_at or datetime.min
                ),
                reverse=True,
            )

            selected: List[str] = []
            seen_single_value_keys: set[str] = set()
            for _, assertion in scored[:60]:
                if len(selected) >= limit:
                    break

                key = (assertion.key or "").strip().lower()
                if key in self.KEY_SINGLE_VALUE and key in seen_single_value_keys:
                    continue

                line = self._format_assertion_for_context(assertion)
                line_tokens = self._estimate_tokens(line)
                if used_tokens + line_tokens > token_budget:
                    continue

                selected.append(line)
                used_tokens += line_tokens
                if key in self.KEY_SINGLE_VALUE:
                    seen_single_value_keys.add(key)

                self.working.remember_injected_memory(self._memory_fingerprint_for_assertion(assertion))
                try:
                    self.local.touch_memory_assertion(assertion.id)
                except Exception as e:
                    logger.debug("assertion touch skipped id=%s error=%s", assertion.id, e)

            return selected

        picked["identity"] = _pick_for_type("identity", self.MAX_CONTEXT_IDENTITY_ASSERTIONS)
        picked["preference"] = _pick_for_type("preference", self.MAX_CONTEXT_PREFERENCE_ASSERTIONS)
        picked["fact"] = _pick_for_type("fact", self.MAX_CONTEXT_FACT_ASSERTIONS)
        return picked

    def _backfill_unified_memory_from_legacy(self) -> None:
        """One-time backfill from legacy facts/preferences when assertions are empty."""
        if self._legacy_unified_backfill_attempted:
            return
        self._legacy_unified_backfill_attempted = True

        try:
            if self.local.get_memory_assertions(limit=1):
                return

            legacy_facts = self.local.get_facts(limit=500)
            legacy_preferences = self.local.get_preferences()
            if not legacy_facts and not legacy_preferences:
                return

            self._upsert_unified_memory(
                facts=legacy_facts,
                preferences=legacy_preferences,
                content="",
                source_id=self.working.conversation_id,
                workspace_id=self.working.workspace_id,
            )
            logger.info(
                "Unified memory backfill completed user_id=%s facts=%s preferences=%s",
                self.user_id,
                len(legacy_facts),
                len(legacy_preferences),
            )
        except Exception as e:
            logger.warning("Unified memory backfill skipped: %s", e)

    def _select_history_messages(
        self,
        query_tokens: List[str],
        low_latency_mode: bool
    ) -> List[Dict[str, str]]:
        """
        Select history with recency + relevance balance.
        Keeps latest turns and optionally recalls older relevant turns.
        """
        buffered = list(self.working.message_buffer)
        if not buffered:
            return []

        recent_keep = (
            self.HISTORY_RECENT_MESSAGES_LOW_LATENCY
            if low_latency_mode else
            self.HISTORY_RECENT_MESSAGES_BALANCED
        )

        if len(buffered) <= recent_keep:
            return [
                {"role": str(m.get("role", "user")), "content": str(m.get("content", ""))}
                for m in buffered
            ]

        recent = buffered[-recent_keep:]
        if low_latency_mode or not query_tokens:
            selected = recent
        else:
            older_pool = buffered[:-recent_keep][-self.HISTORY_MAX_SCOUT_MESSAGES:]
            older_scored: List[Tuple[float, int]] = []
            for idx, message in enumerate(older_pool):
                content = str(message.get("content", ""))
                overlap = self._compute_overlap_score(content, query_tokens)
                if overlap < self.HISTORY_MIN_OVERLAP:
                    continue

                recency = (idx + 1) / max(1, len(older_pool))
                role_boost = 0.06 if str(message.get("role", "")) == "user" else 0.0
                score = (0.72 * overlap) + (0.22 * recency) + role_boost
                if score < 0.24:
                    continue
                older_scored.append((score, idx))

            older_scored.sort(key=lambda row: row[0], reverse=True)
            picked_indexes = sorted(idx for _, idx in older_scored[:self.HISTORY_MAX_RELEVANT_PICKS])
            relevant_older = [older_pool[idx] for idx in picked_indexes]
            selected = [*relevant_older, *recent]

        # Hard-stop by token budget (drop oldest first).
        budget = max(1200, int(self.working.max_tokens or 0))
        total_tokens = sum(int(m.get("tokens", 0) or 0) for m in selected)
        while total_tokens > budget and len(selected) > 2:
            evicted = selected.pop(0)
            total_tokens -= int(evicted.get("tokens", 0) or 0)

        return [
            {"role": str(m.get("role", "user")), "content": str(m.get("content", ""))}
            for m in selected
        ]

    def _score_preference(
        self,
        pref: LocalUserPreference,
        query_tokens: List[str]
    ) -> float:
        text = f"{pref.category} {pref.key} {pref.value}"
        overlap = self._compute_overlap_score(text, query_tokens)
        confidence = max(0.0, min(1.0, float(pref.confidence or 0.0)))
        recency = self._recency_score(pref.updated_at, pref.created_at)
        repeat_penalty = self._memory_repeat_penalty(self._memory_fingerprint_for_preference(pref))

        sticky_keys = {
            "response_language",
            "response_style",
            "tone_preference",
            "primary_language",
        }
        sticky_boost = 0.12 if pref.key.strip().lower() in sticky_keys else 0.0
        score = (0.68 * overlap) + (0.20 * confidence) + (0.12 * recency) + sticky_boost - repeat_penalty
        return score

    def _score_fact(
        self,
        fact: LocalFact,
        query_tokens: List[str],
        workspace_focused: bool
    ) -> float:
        sentence = fact.to_sentence()
        overlap = self._compute_overlap_score(f"{sentence} {fact.category}", query_tokens)
        confidence = max(0.0, min(1.0, float(fact.confidence or 0.0)))
        recency = self._recency_score(fact.updated_at, fact.created_at)
        repeat_penalty = self._memory_repeat_penalty(self._memory_fingerprint_for_fact(fact))

        access = max(0, int(fact.access_count or 0))
        access_score = min(1.0, access / 5.0)

        scope_boost = 0.0
        if fact.scope == "workspace":
            scope_boost = 0.14 if workspace_focused else 0.03
        elif fact.scope == "system":
            scope_boost = 0.08 if not workspace_focused else 0.02

        score = (
            (0.66 * overlap)
            + (0.18 * confidence)
            + (0.10 * recency)
            + (0.06 * access_score)
            + scope_boost
            - repeat_penalty
        )
        return score

    def _deduplicate_facts_for_context(self, facts: List[LocalFact]) -> List[LocalFact]:
        """Deduplicate facts before scoring to reduce repetitive memory injection."""
        deduped: List[LocalFact] = []
        seen_exact: set[Tuple[str, str, str, str]] = set()
        seen_single: Dict[Tuple[str, str, str], int] = {}

        for fact in facts:
            exact_key = self._fact_exact_key(fact)
            if exact_key in seen_exact:
                continue

            single_key = self._fact_single_key(fact)
            if fact.predicate in self.SINGLE_VALUE_PREDICATES and single_key in seen_single:
                continue

            seen_exact.add(exact_key)
            seen_single[single_key] = 1
            deduped.append(fact)

        return deduped

    def _select_relevant_preferences(
        self,
        query_tokens: List[str],
        preferences: Optional[List[LocalUserPreference]] = None
    ) -> List[str]:
        preferences = preferences if preferences is not None else self.local.get_preferences()
        if not preferences:
            return []

        scored: List[Tuple[float, LocalUserPreference]] = []
        for pref in preferences:
            pref_text = f"{pref.category} {pref.key} {pref.value}"
            overlap = self._compute_overlap_score(pref_text, query_tokens)
            is_sticky = pref.key.strip().lower() in {
                "response_language",
                "response_style",
                "tone_preference",
                "primary_language",
            }
            if query_tokens and overlap < 0.08 and not is_sticky:
                continue
            if not query_tokens and not is_sticky:
                continue

            score = self._score_preference(pref, query_tokens)
            if score <= (0.22 if is_sticky else 0.26):
                continue
            scored.append((score, pref))

        if not scored:
            return []

        scored.sort(
            key=lambda item: (
                item[0],
                item[1].updated_at or item[1].created_at or datetime.min
            ),
            reverse=True
        )

        selected: List[str] = []
        token_budget = self.MAX_PREFERENCE_CONTEXT_TOKENS
        used_tokens = 0

        for _, pref in scored[:30]:
            if len(selected) >= self.MAX_PREFERENCES_PER_TURN:
                break

            line = f"{pref.key.replace('_', ' ')}: {pref.value}"
            line_tokens = self._estimate_tokens(line)
            if used_tokens + line_tokens > token_budget:
                continue

            selected.append(line)
            used_tokens += line_tokens
            self.working.remember_injected_memory(self._memory_fingerprint_for_preference(pref))

        return selected

    def _select_relevant_facts(
        self,
        user_message: str,
        query_tokens: List[str],
        workspace_focused: bool,
        fact_limit: int = 500,
        system_facts: Optional[List[LocalFact]] = None,
        workspace_facts: Optional[List[LocalFact]] = None,
    ) -> Tuple[List[str], List[str]]:
        system_facts = (
            system_facts
            if system_facts is not None
            else self.local.get_facts(workspace_id=None, limit=fact_limit)
        )
        workspace_facts = (
            workspace_facts
            if workspace_facts is not None
            else (
                self.local.get_facts(workspace_id=self.working.workspace_id, limit=fact_limit)
                if self.working.workspace_id else []
            )
        )

        system_facts = self._deduplicate_facts_for_context(system_facts)
        workspace_facts = self._deduplicate_facts_for_context(workspace_facts)

        total_budget = min(
            self.MAX_FACT_CONTEXT_TOKENS,
            max(0, self.MAX_USER_CONTEXT_TOKENS - self.MAX_PREFERENCE_CONTEXT_TOKENS)
        )
        if self.working.workspace_id:
            workspace_ratio = 0.65 if workspace_focused else 0.45
            workspace_budget = int(total_budget * workspace_ratio)
            system_budget = total_budget - workspace_budget
        else:
            workspace_budget = 0
            system_budget = total_budget

        system_lines = self._pick_scoped_facts_with_budget(
            facts=system_facts,
            query_tokens=query_tokens,
            workspace_focused=workspace_focused,
            budget_tokens=system_budget,
            max_items=self.MAX_SYSTEM_FACTS_PER_TURN
        )
        workspace_lines = self._pick_scoped_facts_with_budget(
            facts=workspace_facts,
            query_tokens=query_tokens,
            workspace_focused=workspace_focused,
            budget_tokens=workspace_budget,
            max_items=self.MAX_WORKSPACE_FACTS_PER_TURN
        )

        return system_lines, workspace_lines

    def _pick_scoped_facts_with_budget(
        self,
        facts: List[LocalFact],
        query_tokens: List[str],
        workspace_focused: bool,
        budget_tokens: int,
        max_items: int
    ) -> List[str]:
        if budget_tokens <= 0 or max_items <= 0 or not facts:
            return []

        # Phase 1: Always include identity facts (name, age, gender, etc.)
        # These are injected regardless of query relevance
        identity_lines: List[str] = []
        identity_ids: set = set()
        identity_tokens_used = 0
        max_identity_tokens = min(budget_tokens // 3, 120)  # Reserve up to 1/3 of budget

        for fact in facts:
            if fact.predicate not in self.IDENTITY_PREDICATES:
                continue
            if fact.scope == "workspace":
                continue  # Identity facts are system-level only
            if float(fact.confidence or 0.0) < 0.7:
                continue

            fingerprint = self._memory_fingerprint_for_fact(fact)
            sentence = fact.to_sentence().strip()
            if not sentence:
                continue

            sentence_tokens = self._estimate_tokens(sentence)
            if identity_tokens_used + sentence_tokens > max_identity_tokens:
                continue

            # Dedup check
            dedup_key = (fact.subject.lower(), fact.predicate.lower())
            if dedup_key in identity_ids:
                continue

            identity_lines.append(sentence)
            identity_ids.add(dedup_key)
            identity_tokens_used += sentence_tokens
            self.working.remember_injected_memory(fingerprint)
            try:
                self.local.touch_fact(fact.id)
            except Exception as e:
                logger.debug("fact touch skipped id=%s error=%s", fact.id, e)

        # Phase 2: Score and select remaining facts by relevance
        remaining_budget = budget_tokens - identity_tokens_used
        remaining_max = max_items - len(identity_lines)

        scored: List[Tuple[float, LocalFact]] = []
        for fact in facts:
            # Skip already-included identity facts
            dedup_key = (fact.subject.lower(), fact.predicate.lower())
            if dedup_key in identity_ids:
                continue

            fact_text = f"{fact.to_sentence()} {fact.category}"
            overlap = self._compute_overlap_score(fact_text, query_tokens)
            allow_scope_fallback = (
                workspace_focused
                and fact.scope == "workspace"
                and fact.category in {"project_context", "tech_stack", "architecture", "convention", "rule"}
            )
            if query_tokens and overlap < 0.08 and not allow_scope_fallback:
                continue
            if not query_tokens and not allow_scope_fallback:
                continue

            if (
                fact.predicate in {"name", "occupation", "company"}
                and float(fact.confidence or 0.0) < 0.86
            ):
                continue

            score = self._score_fact(fact, query_tokens, workspace_focused)
            if score <= (0.20 if allow_scope_fallback else 0.27):
                continue
            scored.append((score, fact))

        if scored:
            scored.sort(
                key=lambda item: (
                    item[0],
                    item[1].updated_at or item[1].created_at or datetime.min
                ),
                reverse=True
            )

        relevance_lines: List[str] = []
        used_tokens = 0
        for _, fact in scored[:60]:
            if len(relevance_lines) >= remaining_max:
                break

            sentence = fact.to_sentence().strip()
            if not sentence:
                continue

            sentence_tokens = self._estimate_tokens(sentence)
            if used_tokens + sentence_tokens > remaining_budget:
                continue

            relevance_lines.append(sentence)
            used_tokens += sentence_tokens
            self.working.remember_injected_memory(self._memory_fingerprint_for_fact(fact))
            try:
                self.local.touch_fact(fact.id)
            except Exception as e:
                logger.debug("fact touch skipped id=%s error=%s", fact.id, e)

        return identity_lines + relevance_lines
    
    # =========================================================================
    # Context Assembly (Combines Server + Local)
    # =========================================================================
    
    async def build_context(
        self,
        user_message: str,
        model: Optional[str] = None,
        user_tier: str = "free",
        include_knowledge: bool = True,
        include_facts: bool = True,
        include_history: bool = True,
        latency_mode: str = "balanced",
    ) -> AssembledContext:
        """
        Build complete context for AI request.
        Combines:
        - System prompt (from SERVER)
        - Skills (from SERVER)
        - Conversation history (from LOCAL)
        - Knowledge items (from LOCAL)
        - User facts/preferences (from LOCAL)
        """
        context = AssembledContext()
        
        # 1. Detect intent and extract keywords
        intent = self.prompt_injector.detect_intent(user_message)
        keywords = self.prompt_injector.extract_keywords(user_message)
        self.working.current_intent = intent
        self.working.begin_turn()
        query_tokens = self._build_query_tokens(user_message, intent, keywords)
        workspace_focused = self._is_workspace_focused_query(
            message=user_message,
            tokens=query_tokens,
            intent=intent
        )
        smalltalk_turn = self._is_smalltalk_turn(user_message, intent=intent)
        low_latency_mode = (latency_mode or "").strip().lower() in {"low", "low_latency", "latency", "fast"}

        # Avoid maintenance work on hot streaming path.
        if (
            self._context_maintenance_enabled
            and include_facts
            and not low_latency_mode
            and (self.working.turn_index == 1 or self.working.turn_index % 6 == 0)
        ):
            try:
                self._cleanup_noisy_facts(limit=1500)
            except Exception as e:
                logger.warning("Memory cleanup skipped: %s", e)
        if (
            self._context_maintenance_enabled
            and include_facts
            and not low_latency_mode
            and self.working.turn_index == 1
        ):
            self._backfill_unified_memory_from_legacy()

        # 1.5 Load local rules (global + workspace)
        context.rules_context = self.get_rules_context(self.working.workspace_id)
        
        # 2. Build user context from local memory
        user_context_parts = []
        fact_limit = 220 if low_latency_mode else 500
        knowledge_top_k = 1 if low_latency_mode else 3

        # Hybrid pipeline:
        # - parallel preload (DB/embedding heavy reads)
        # - sequential scoring/assembly to keep deterministic output shape.
        preloaded_user_assertions: Optional[List[LocalMemoryAssertion]] = None
        preloaded_workspace_assertions: Optional[List[LocalMemoryAssertion]] = None
        preloaded_conversation_assertions: Optional[List[LocalMemoryAssertion]] = None
        prefetched_knowledge_items: Optional[List[LocalKnowledgeItem]] = None
        knowledge_prefetch_started = False
        knowledge_prefetch_timed_out = False

        preload_labels: List[str] = []
        preload_jobs: List[Any] = []

        if include_facts and not smalltalk_turn:
            preload_labels.append("user_assertions")
            preload_jobs.append(
                asyncio.to_thread(
                    self.local.get_memory_assertions,
                    scope="user",
                    workspace_id=None,
                    include_expired=False,
                    status="active",
                    limit=fact_limit,
                )
            )
            if self.working.workspace_id:
                preload_labels.append("workspace_assertions")
                preload_jobs.append(
                    asyncio.to_thread(
                        self.local.get_memory_assertions,
                        scope="workspace",
                        workspace_id=self.working.workspace_id,
                        include_expired=False,
                        status="active",
                        limit=fact_limit,
                    )
                )
            if self.working.conversation_id:
                preload_labels.append("conversation_assertions")
                preload_jobs.append(
                    asyncio.to_thread(
                        self.local.get_memory_assertions,
                        scope="conversation",
                        conversation_id=self.working.conversation_id,
                        include_expired=False,
                        status="active",
                        limit=max(80, fact_limit // 2),
                    )
                )

        if include_knowledge:
            knowledge_prefetch_started = True
            preload_labels.append("knowledge_items")
            preload_jobs.append(
                asyncio.to_thread(
                    self.search_knowledge,
                    user_message,
                    knowledge_top_k,
                    not low_latency_mode,
                    self.working.workspace_id,
                )
            )

        if preload_jobs:
            preload_tasks = [asyncio.create_task(job) for job in preload_jobs]
            task_to_label = {task: preload_labels[idx] for idx, task in enumerate(preload_tasks)}
            timeout_ms = (
                self._stream_context_preload_timeout_ms
                if low_latency_mode
                else self._context_preload_timeout_ms
            )
            timeout_seconds: Optional[float] = (
                timeout_ms / 1000.0 if timeout_ms > 0 else None
            )

            done, pending = await asyncio.wait(preload_tasks, timeout=timeout_seconds)
            preload_map: Dict[str, Any] = {}
            if pending:
                logger.info(
                    "Context preload timeout user_id=%s pending=%s timeout_ms=%s low_latency=%s",
                    self.user_id,
                    len(pending),
                    timeout_ms,
                    low_latency_mode,
                )
            for task in pending:
                if task_to_label.get(task) == "knowledge_items":
                    knowledge_prefetch_timed_out = True
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

            for task in done:
                label = task_to_label.get(task, "unknown")
                try:
                    result = task.result()
                except Exception as e:
                    logger.warning("Context preload failed label=%s: %s", label, e)
                    continue
                preload_map[label] = result

            preloaded_user_assertions = preload_map.get("user_assertions")
            preloaded_workspace_assertions = preload_map.get("workspace_assertions")
            preloaded_conversation_assertions = preload_map.get("conversation_assertions")
            prefetched_knowledge_items = preload_map.get("knowledge_items")

        # ─── v2: Inject User Profile (ALWAYS) ───
        v2_has_identity = False
        try:
            profile_context = self._profile_store.format_for_context()
            if profile_context:
                user_context_parts.append(profile_context)
                v2_has_identity = True
        except Exception as e:
            logger.warning("v2 profile injection failed: %s", e)

        # ─── v2: Inject Context Memory (when relevant) ───
        try:
            context_memory_text = self._context_store.format_for_context(
                workspace_id=self.working.workspace_id,
                conversation_id=self.working.conversation_id,
            )
            if context_memory_text:
                user_context_parts.append(context_memory_text)
        except Exception as e:
            logger.warning("v2 context memory injection failed: %s", e)

        # ─── Legacy assertion pipeline (fallback) ───
        if include_facts and not smalltalk_turn:
            all_assertions: List[LocalMemoryAssertion] = []
            if preloaded_user_assertions:
                all_assertions.extend(preloaded_user_assertions)
            if preloaded_workspace_assertions:
                all_assertions.extend(preloaded_workspace_assertions)
            if preloaded_conversation_assertions:
                all_assertions.extend(preloaded_conversation_assertions)

            selected_assertions = self._select_relevant_assertions(
                query_tokens=query_tokens,
                workspace_focused=workspace_focused,
                assertions=all_assertions,
            )

            # Skip legacy identity if v2 already provided it
            if not v2_has_identity and selected_assertions["identity"]:
                identity_lines = "\n".join(f"- {line}" for line in selected_assertions["identity"])
                user_context_parts.append("### User Identity\n" + identity_lines)
            if selected_assertions["preference"]:
                pref_lines = "\n".join(f"- {line}" for line in selected_assertions["preference"])
                user_context_parts.append("### User Preferences\n" + pref_lines)
            if selected_assertions["fact"]:
                fact_lines = "\n".join(f"- {line}" for line in selected_assertions["fact"])
                user_context_parts.append("### User Facts\n" + fact_lines)

        user_context = "\n".join(user_context_parts).strip() if user_context_parts else None
        context.facts_context = user_context or ""
        
        # 3. Get injected system prompt (from SERVER - "bơm ngầm")
        system_prompt = self.prompt_injector.build_injected_prompt(
            user_message=user_message,
            model=model,
            user_tier=user_tier,
            detected_intent=intent,
            keywords=keywords,
            user_context=user_context
        )
        context.system_prompt = system_prompt
        
        # 4. Get relevant knowledge (from LOCAL)
        if include_knowledge:
            knowledge_snippet_chars = 220 if low_latency_mode else 500
            knowledge_items: List[LocalKnowledgeItem]
            if prefetched_knowledge_items is not None:
                knowledge_items = prefetched_knowledge_items
            elif knowledge_prefetch_started and knowledge_prefetch_timed_out:
                logger.info(
                    "Knowledge search skipped after preload timeout user_id=%s workspace_id=%s low_latency=%s",
                    self.user_id,
                    self.working.workspace_id,
                    low_latency_mode,
                )
                knowledge_items = []
            else:
                try:
                    knowledge_items = await asyncio.to_thread(
                        self.search_knowledge,
                        user_message,
                        knowledge_top_k,
                        not low_latency_mode,
                        self.working.workspace_id,
                    )
                except Exception as e:
                    logger.warning("Knowledge search fallback failed: %s", e)
                    knowledge_items = []
            if knowledge_items:
                ki_texts = []
                for i, item in enumerate(knowledge_items, 1):
                    content = self._build_knowledge_snippet(
                        content=item.content or "",
                        query_tokens=query_tokens,
                        max_chars=knowledge_snippet_chars
                    )
                    ki_texts.append(f"[{i}] {item.title}\n{content}")
                    context.sources.append({
                        "index": i,
                        "id": item.id,
                        "title": item.title,
                        "type": item.content_type
                    })
                context.knowledge_context = "\n\n".join(ki_texts)
                self._touch_knowledge_items(knowledge_items)
        
        # 5. Get conversation history (from LOCAL working memory)
        if include_history:
            context.messages = self._select_history_messages(
                query_tokens=query_tokens,
                low_latency_mode=low_latency_mode
            )
        else:
            context.messages = []

        context.total_tokens = (
            self._estimate_tokens(context.rules_context)
            + self._estimate_tokens(context.system_prompt)
            + self._estimate_tokens(context.knowledge_context)
            + sum(self._estimate_tokens(str(m.get("content", ""))) for m in context.messages)
        )

        return context
    
    def format_context_for_api(
        self,
        context: AssembledContext,
        new_message: str,
        append_user_message: bool = True
    ) -> List[Dict[str, str]]:
        """
        Format assembled context into OpenAI-compatible messages format.
        """
        messages = []

        # System message with all injected content
        system_sections: List[str] = []
        if context.rules_context:
            system_sections.append(context.rules_context)
        if context.system_prompt:
            system_sections.append(context.system_prompt)
        if context.knowledge_context:
            system_sections.append(f"## Relevant Knowledge\n{context.knowledge_context}")

        system_content = "\n\n---\n\n".join(section for section in system_sections if section.strip())
        if not system_content:
            system_content = "You are a helpful AI assistant."

        messages.append({
            "role": "system",
            "content": system_content
        })
        
        # Conversation history
        messages.extend(context.messages)
        
        # New user message
        if append_user_message:
            messages.append({
                "role": "user",
                "content": new_message
            })
        
        return messages
    
    # =========================================================================
    # Usage Tracking (SERVER)
    # =========================================================================
    
    def track_usage(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost: float = 0.0
    ):
        """Track usage in server database"""
        from ..models import UsageRecord
        
        record = UsageRecord(
            user_id=self.user_id,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cost=cost,
            endpoint="/chat/completions",
            status="success"
        )
        self.server_db.add(record)
        self.server_db.commit()

def get_memory_coordinator(
    server_db: Session,
    user_id: str
) -> MemoryCoordinator:
    """Factory function to create memory coordinator"""
    return MemoryCoordinator(server_db, user_id)
