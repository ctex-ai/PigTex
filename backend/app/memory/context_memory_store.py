"""
Context Memory Store - Stream 2: Scoped contextual memory.

Stores workspace/conversation/temporary context.
This is for project notes, tech stack info, conventions, etc.
NEVER stores user profile data (name, age, gender).

Design principles:
- Scoped: workspace, conversation, or temporary
- Flexible keys: not restricted to a whitelist
- TTL support: temporary items expire after N days
- No identity data: enforced by PROFILE_VALID_KEYS rejection
"""

import re
import uuid
import unicodedata
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from ..local_storage import LocalDatabase
from ..local_storage.local_models import (
    ContextMemoryItem,
    CONTEXT_SCOPES,
    PROFILE_VALID_KEYS,
)

logger = logging.getLogger(__name__)

# Default TTL for temporary items
TEMPORARY_TTL_DAYS = 7


@dataclass
class ContextPattern:
    """Pattern for extracting context memory items."""
    category: str     # "project", "tech_stack", "convention", etc.
    key: str          # The key for this item
    pattern: str      # Regex
    confidence: float = 0.85
    scope: str = "workspace"   # Default scope


# Patterns for workspace/project context extraction
CONTEXT_PATTERNS: List[ContextPattern] = [
    # ── Project tech stack ──
    ContextPattern("tech_stack", "framework", r"\b(?:project uses|we use|using|dự án dùng|đang dùng)\s+(react|vue|angular|next\.?js|nuxt|svelte|django|flask|fastapi|express|spring|laravel)\b", 0.88),
    ContextPattern("tech_stack", "language", r"\b(?:project is in|written in|coded in|viết bằng)\s+(python|javascript|typescript|java|c\+\+|c#|go|rust|php|ruby|swift|kotlin)\b", 0.88),
    ContextPattern("tech_stack", "database", r"\b(?:database is|using|we use|dùng)\s+(mysql|postgresql|postgres|mongodb|redis|sqlite|dynamodb|firestore)\b", 0.86),

    # ── Project conventions ──
    ContextPattern("convention", "naming", r"\b(?:use|follow|convention is|quy ước)\s+(camelCase|snake_case|PascalCase|kebab-case)\b", 0.88),
    ContextPattern("convention", "indent", r"\b(?:indent|tab)\s+(?:with\s+)?(\d+)\s*(?:spaces?|tabs?)\b", 0.86),

    # ── Project goals/deadlines ──
    ContextPattern("project", "goal", r"\b(?:project goal is|mục tiêu dự án là|mục tiêu là)\s+(.{10,100})", 0.82),
    ContextPattern("project", "deadline", r"\b(?:deadline is|hạn chót là|deadline là)\s+(.{5,50})", 0.85),

    # ── Explicit memory commands ──
    ContextPattern("note", "explicit", r"\b(?:remember(?: that)?|ghi nhớ|nhớ rằng|hay nhớ|đừng quên|please remember)\s+(.{5,200})", 0.92, scope="workspace"),
]


class ContextMemoryStore:
    """
    Stream 2: Scoped context memory storage.

    - workspace scope  → tied to a workspace/project
    - conversation scope → tied to a conversation
    - temporary scope  → expires after TTL days
    - NEVER stores user profile data
    """

    def __init__(self, local_db: LocalDatabase, user_id: str):
        self.local = local_db
        self.user_id = local_db.user_id
        self._ensure_table()

    def _ensure_table(self):
        """Create context_memories table if not exists."""
        with self.local._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS context_memories (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    category TEXT DEFAULT 'general',
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    workspace_id TEXT,
                    conversation_id TEXT,
                    confidence REAL DEFAULT 0.8,
                    source TEXT DEFAULT 'pattern',
                    access_count INTEGER DEFAULT 0,
                    expires_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_ctxmem_user
                    ON context_memories(user_id);
                CREATE INDEX IF NOT EXISTS idx_ctxmem_scope
                    ON context_memories(user_id, scope);
                CREATE INDEX IF NOT EXISTS idx_ctxmem_workspace
                    ON context_memories(user_id, workspace_id);
                CREATE INDEX IF NOT EXISTS idx_ctxmem_conversation
                    ON context_memories(user_id, conversation_id);
                CREATE INDEX IF NOT EXISTS idx_ctxmem_expires
                    ON context_memories(expires_at);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_ctxmem_unique
                    ON context_memories(
                        user_id,
                        scope,
                        COALESCE(workspace_id, ''),
                        COALESCE(conversation_id, ''),
                        key
                    );
            """)

    # ─────────────────────────────────────────────────────────
    # CRUD
    # ─────────────────────────────────────────────────────────

    def get_all(
        self,
        scope: Optional[str] = None,
        workspace_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        include_expired: bool = False,
    ) -> List[ContextMemoryItem]:
        """Get context memories with optional filters."""
        with self.local._get_connection() as conn:
            conditions = ["user_id = ?"]
            params: list = [self.user_id]

            if scope:
                conditions.append("scope = ?")
                params.append(scope)
            if workspace_id:
                conditions.append("workspace_id = ?")
                params.append(workspace_id)
            if conversation_id:
                conditions.append("conversation_id = ?")
                params.append(conversation_id)
            if not include_expired:
                conditions.append("(expires_at IS NULL OR expires_at > ?)")
                params.append(datetime.now().isoformat())

            where = " AND ".join(conditions)
            rows = conn.execute(
                f"SELECT * FROM context_memories WHERE {where} ORDER BY updated_at DESC",
                params
            ).fetchall()
            return [self._row_to_item(row) for row in rows]

    def get(self, item_id: str) -> Optional[ContextMemoryItem]:
        """Get a single context memory by ID."""
        with self.local._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM context_memories WHERE id = ? AND user_id = ?",
                (item_id, self.user_id)
            ).fetchone()
            return self._row_to_item(row) if row else None

    def get_by_key(
        self,
        key: str,
        scope: str = "workspace",
        workspace_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> Optional[ContextMemoryItem]:
        """Get a context memory by key and scope."""
        with self.local._get_connection() as conn:
            row = conn.execute("""
                SELECT * FROM context_memories
                WHERE user_id = ?
                  AND key = ?
                  AND scope = ?
                  AND COALESCE(workspace_id, '') = COALESCE(?, '')
                  AND COALESCE(conversation_id, '') = COALESCE(?, '')
                  AND (expires_at IS NULL OR expires_at > ?)
            """, (
                self.user_id, key, scope,
                workspace_id or "", conversation_id or "",
                datetime.now().isoformat()
            )).fetchone()
            return self._row_to_item(row) if row else None

    def set(
        self,
        key: str,
        value: str,
        scope: str = "workspace",
        category: str = "general",
        workspace_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        confidence: float = 0.85,
        source: str = "pattern",
        ttl_days: Optional[int] = None,
        force: bool = False,
    ) -> Optional[ContextMemoryItem]:
        """
        Set a context memory item. Returns saved item or None if rejected.

        Rejection:
        - Key is a profile key (belongs in UserProfileStore)
        - Scope is invalid
        """
        # GUARD: reject profile keys
        if key.strip().lower() in PROFILE_VALID_KEYS:
            logger.debug("Context memory rejected (profile key): %s", key)
            return None

        if scope not in CONTEXT_SCOPES:
            logger.debug("Context memory rejected (invalid scope): %s", scope)
            return None

        value = value.strip()
        if not value:
            return None

        # Determine TTL
        expires_at = None
        if scope == "temporary" or ttl_days:
            days = ttl_days or TEMPORARY_TTL_DAYS
            expires_at = datetime.now() + timedelta(days=days)

        existing = self.get_by_key(key, scope, workspace_id, conversation_id)

        if existing and not force:
            # Same value → bump confidence
            if self._normalize(existing.value) == self._normalize(value):
                if confidence > existing.confidence:
                    existing.confidence = confidence
                    existing.updated_at = datetime.now()
                    self._save(existing)
                return existing
            # Lower confidence → skip
            if existing.confidence > confidence + 0.05:
                return existing

        now = datetime.now()
        item = ContextMemoryItem(
            id=existing.id if existing else str(uuid.uuid4()),
            user_id=self.user_id,
            scope=scope,
            category=category,
            key=key,
            value=value,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            confidence=confidence,
            source=source,
            access_count=existing.access_count if existing else 0,
            expires_at=expires_at,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        self._save(item)
        logger.info(
            "Context memory saved: scope=%s key=%s value=%.60s",
            scope, key, value
        )
        return item

    def delete(self, item_id: str) -> bool:
        """Delete a context memory by ID."""
        with self.local._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM context_memories WHERE id = ? AND user_id = ?",
                (item_id, self.user_id)
            )
            return cursor.rowcount > 0

    def cleanup_expired(self) -> int:
        """Remove expired context memories. Returns count deleted."""
        with self.local._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM context_memories WHERE user_id = ? AND expires_at IS NOT NULL AND expires_at <= ?",
                (self.user_id, datetime.now().isoformat())
            )
            return cursor.rowcount

    # ─────────────────────────────────────────────────────────
    # Extraction
    # ─────────────────────────────────────────────────────────

    def extract_from_message(
        self,
        message: str,
        workspace_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> List[ContextMemoryItem]:
        """
        Extract context memories from a user message.
        Returns list of newly saved/updated items.
        """
        if not message or len(message.strip()) < 10:
            return []

        text = message.strip()
        saved: List[ContextMemoryItem] = []

        for pattern in CONTEXT_PATTERNS:
            match = re.search(pattern.pattern, text, re.IGNORECASE)
            if not match:
                continue

            raw_value = match.group(1).strip()
            value = re.sub(r"[.,;:!?]+$", "", raw_value).strip()
            if not value or len(value) < 2:
                continue

            scope = pattern.scope
            actual_workspace_id = workspace_id if scope == "workspace" else None
            actual_conversation_id = conversation_id if scope == "conversation" else None

            result = self.set(
                key=pattern.key,
                value=value,
                scope=scope,
                category=pattern.category,
                workspace_id=actual_workspace_id,
                conversation_id=actual_conversation_id,
                confidence=pattern.confidence,
                source="pattern",
            )
            if result:
                saved.append(result)

        return saved

    # ─────────────────────────────────────────────────────────
    # Context injection
    # ─────────────────────────────────────────────────────────

    def format_for_context(
        self,
        workspace_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        max_items: int = 10,
    ) -> str:
        """
        Format context memories for injection into system prompt.
        Only injected when relevant.
        """
        items: List[ContextMemoryItem] = []

        if workspace_id:
            ws_items = self.get_all(scope="workspace", workspace_id=workspace_id)
            items.extend(ws_items)

        if conversation_id:
            conv_items = self.get_all(scope="conversation", conversation_id=conversation_id)
            items.extend(conv_items)

        # Include non-expired temporary items
        temp_items = self.get_all(scope="temporary")
        items.extend(temp_items)

        if not items:
            return ""

        # Sort by confidence desc, then recency
        items.sort(key=lambda x: (x.confidence, x.updated_at or x.created_at), reverse=True)
        items = items[:max_items]

        lines = [f"- {item.to_display()}" for item in items]
        return "### Context\n" + "\n".join(lines)

    # ─────────────────────────────────────────────────────────
    # Migration from legacy
    # ─────────────────────────────────────────────────────────

    def migrate_from_legacy(self, workspace_id: Optional[str] = None):
        """
        One-time migration from legacy facts/assertions
        for workspace-scoped context data.
        """
        existing = self.get_all()
        if existing:
            logger.info("Context memories already exist, skipping migration")
            return

        migrated = 0

        # Migrate workspace-scoped assertions
        try:
            with self.local._get_connection() as conn:
                rows = conn.execute("""
                    SELECT key, value, confidence, category, scope,
                           workspace_id, conversation_id, type, expires_at
                    FROM memory_assertions
                    WHERE user_id = ?
                      AND status = 'active'
                      AND type NOT IN ('identity')
                      AND key NOT IN ({})
                """.format(",".join(f"'{k}'" for k in PROFILE_VALID_KEYS)),
                    (self.user_id,)
                ).fetchall()

                for row in rows:
                    key = str(row["key"]).strip()
                    value = str(row["value"]).strip()
                    if not key or not value:
                        continue
                    if key.lower() in PROFILE_VALID_KEYS:
                        continue

                    scope = str(row["scope"] or "workspace")
                    if scope == "user":
                        scope = "workspace"

                    result = self.set(
                        key=key,
                        value=value,
                        scope=scope,
                        category=str(row["category"] or "general"),
                        workspace_id=row["workspace_id"],
                        conversation_id=row["conversation_id"],
                        confidence=float(row["confidence"] or 0.8),
                        source="migration",
                    )
                    if result:
                        migrated += 1
        except Exception as e:
            logger.warning("Context migration from assertions failed: %s", e)

        # Migrate workspace-scoped facts
        try:
            with self.local._get_connection() as conn:
                rows = conn.execute("""
                    SELECT predicate, object, confidence, category, workspace_id
                    FROM facts
                    WHERE workspace_id IS NOT NULL
                """).fetchall()

                for row in rows:
                    key = str(row["predicate"]).strip()
                    value = str(row["object"]).strip()
                    if not key or not value:
                        continue
                    if key.lower() in PROFILE_VALID_KEYS:
                        continue

                    result = self.set(
                        key=key,
                        value=value,
                        scope="workspace",
                        category=str(row["category"] or "general"),
                        workspace_id=row["workspace_id"],
                        confidence=float(row["confidence"] or 0.7),
                        source="migration",
                    )
                    if result:
                        migrated += 1
        except Exception as e:
            logger.warning("Context migration from facts failed: %s", e)

        if migrated:
            logger.info("Context migration completed: %d items migrated", migrated)

    # ─────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────

    def _save(self, item: ContextMemoryItem):
        """Upsert a context memory item into DB."""
        with self.local._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO context_memories
                    (id, user_id, scope, category, key, value,
                     workspace_id, conversation_id, confidence, source,
                     access_count, expires_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.id,
                item.user_id,
                item.scope,
                item.category,
                item.key,
                item.value,
                item.workspace_id,
                item.conversation_id,
                item.confidence,
                item.source,
                item.access_count,
                item.expires_at.isoformat() if isinstance(item.expires_at, datetime) else item.expires_at,
                item.created_at.isoformat() if isinstance(item.created_at, datetime) else item.created_at,
                item.updated_at.isoformat() if isinstance(item.updated_at, datetime) else item.updated_at,
            ))

    def _row_to_item(self, row) -> ContextMemoryItem:
        """Convert DB row to ContextMemoryItem."""
        return ContextMemoryItem(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            scope=str(row["scope"]),
            category=str(row["category"] or "general"),
            key=str(row["key"]),
            value=str(row["value"]),
            workspace_id=row["workspace_id"],
            conversation_id=row["conversation_id"],
            confidence=float(row["confidence"] or 0.8),
            source=str(row["source"] or "pattern"),
            access_count=int(row["access_count"] or 0),
            expires_at=self._parse_datetime(row["expires_at"]),
            created_at=self._parse_datetime(row["created_at"]) or datetime.now(),
            updated_at=self._parse_datetime(row["updated_at"]) or datetime.now(),
        )

    def _parse_datetime(self, value) -> Optional[datetime]:
        if isinstance(value, datetime):
            return value
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except (ValueError, TypeError):
            return None

    def _normalize(self, text: str) -> str:
        """Normalize text for comparison."""
        lowered = (text or "").strip().lower()
        if not lowered:
            return ""
        normalized = unicodedata.normalize("NFD", lowered)
        normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
        normalized = normalized.replace("đ", "d")
        return re.sub(r"\s+", " ", normalized).strip()
