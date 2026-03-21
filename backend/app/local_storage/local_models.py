"""
Local SQLite Models - Dataclasses for local storage.
These mirror server models but are stored locally for privacy and speed.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, List
import json


@dataclass
class LocalWorkspace:
    """Workspace stored locally for organizing knowledge items"""
    id: str
    user_id: str
    name: str
    icon: str = "📁"
    color: str = "#6366f1"
    parent_id: Optional[str] = None
    item_count: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class LocalConversation:
    """Conversation stored locally"""
    id: str
    user_id: str
    workspace_id: Optional[str] = None
    title: str = "New Conversation"
    summary: Optional[str] = None
    total_messages: int = 0
    total_tokens: int = 0
    is_archived: bool = False
    importance_score: float = 0.5
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    last_accessed_at: Optional[datetime] = None


@dataclass
class LocalMessage:
    """Chat message stored locally"""
    id: str
    conversation_id: str
    role: str  # 'user', 'assistant', 'system'
    content: str
    token_count: int = 0
    model: Optional[str] = None
    sources_json: Optional[str] = None  # JSON array of knowledge item IDs
    embedding: Optional[bytes] = None  # Binary vector for sqlite-vec
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def source_payload(self) -> List[Any]:
        if not self.sources_json:
            return []
        try:
            parsed = json.loads(self.sources_json)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []

    @property
    def sources(self) -> List[str]:
        payload = self.source_payload
        if not payload:
            return []
        if all(isinstance(item, str) for item in payload):
            return [str(item) for item in payload]

        # Backward-compatible view: if stored payload is structured citations,
        # expose URL list as plain sources.
        urls: List[str] = []
        for item in payload:
            if isinstance(item, dict):
                raw_url = str(item.get("url") or "").strip()
                if raw_url:
                    urls.append(raw_url)
        return urls

    @property
    def citations(self) -> List[Dict[str, Any]]:
        payload = self.source_payload
        citations: List[Dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            if not title or not url:
                continue
            citations.append(item)
        return citations


@dataclass
class LocalKnowledgeItem:
    """Knowledge item stored locally"""
    id: str
    user_id: str
    workspace_id: Optional[str] = None
    title: str = ""
    content: Optional[str] = None
    content_type: str = "note"  # note, code, doc, link, file
    metadata_json: Optional[str] = None
    summary: Optional[str] = None
    tags: Optional[str] = None  # Comma-separated
    is_favorite: bool = False
    is_pinned: bool = False
    importance_score: float = 0.5
    access_count: int = 0
    embedding: Optional[bytes] = None  # Binary vector
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    last_accessed_at: Optional[datetime] = None
    
    @property
    def metadata(self) -> dict:
        if self.metadata_json:
            return json.loads(self.metadata_json)
        return {}
    
    @property
    def tag_list(self) -> List[str]:
        if self.tags:
            return [t.strip() for t in self.tags.split(",")]
        return []


# ─────────────────────────────────────────────────────────────
# Memory scope categories
# ─────────────────────────────────────────────────────────────

# System Memory — global facts about the user, shared across all workspaces
SYSTEM_MEMORY_CATEGORIES = [
    'personal',          # Tên, tuổi, giới tính, ngày sinh, quê quán
    'preference',        # Sở thích cá nhân (dark mode, ngôn ngữ, ...)
    'coding_style',      # Phong cách code (indent, naming, ...)
    'communication',     # Phong cách giao tiếp (formal/casual, emoji, ...)
    'work',              # Công việc, chức vụ, công ty
    'skill',             # Kỹ năng, ngôn ngữ lập trình, framework
    'personality',       # Tính cách, xu hướng
    'relationship',      # Mối quan hệ giữa user và AI
    'explicit_memory',   # User tự ghi nhớ
    'general',           # Chung
]

# Workspace Memory — per-project context, rules, conventions
WORKSPACE_MEMORY_CATEGORIES = [
    'project_context',   # Mô tả dự án, mục tiêu
    'tech_stack',        # Framework, thư viện, ngôn ngữ dự án dùng
    'architecture',      # Kiến trúc, pattern, cấu trúc thư mục
    'convention',        # Code convention, naming convention
    'domain',            # Kiến thức domain (y tế, tài chính, ...)
    'team',              # Thành viên, vai trò trong dự án
    'rule',              # Rules bắt buộc cho workspace
    'reference',         # Tài liệu, link tham khảo
    'note',              # Ghi chú ngữ cảnh workspace
    'general',           # Chung
]


# ─────────────────────────────────────────────────────────────
# Unified memory entities (production architecture)
# ─────────────────────────────────────────────────────────────

MEMORY_TYPES = ["identity", "preference", "fact", "temporary"]
MEMORY_SCOPES = ["user", "workspace", "conversation"]


@dataclass
class LocalFact:
    """Extracted fact for semantic memory.
    
    workspace_id controls the scope:
      - None  → System Memory (shared across all workspaces)
      - <id>  → Workspace Memory (scoped to a specific workspace/project)
    """
    id: str
    source_type: str  # 'conversation', 'knowledge', 'user_input', 'pattern_extraction'
    source_id: Optional[str] = None
    workspace_id: Optional[str] = None  # None = system, <id> = workspace
    subject: str = ""  # "User" | "Project X"
    predicate: str = ""  # "prefers" | "uses"
    object: str = ""  # "dark mode" | "Python"
    category: str = "general"
    confidence: float = 1.0
    access_count: int = 0
    embedding: Optional[bytes] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    confirmed_at: Optional[datetime] = None
    
    @property
    def scope(self) -> str:
        """Return 'system' or 'workspace' based on workspace_id."""
        return 'workspace' if self.workspace_id else 'system'
    
    def to_sentence(self) -> str:
        return f"{self.subject} {self.predicate} {self.object}"


@dataclass
class LocalMemoryAssertion:
    """Canonical memory assertion used for context injection."""
    id: str
    user_id: str
    type: str  # identity | preference | fact | temporary
    scope: str  # user | workspace | conversation
    key: str
    value: str
    workspace_id: Optional[str] = None
    conversation_id: Optional[str] = None
    category: str = "general"
    confidence: float = 0.8
    access_count: int = 0
    status: str = "active"  # active | archived
    expires_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    source_evidence_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        return self.expires_at <= datetime.now()

    def to_sentence(self) -> str:
        return f"{self.key.replace('_', ' ')}: {self.value}"


@dataclass
class LocalMemoryEvidence:
    """Append-only evidence records produced by extraction."""
    id: str
    user_id: str
    type: str  # identity | preference | fact | temporary
    scope: str  # user | workspace | conversation
    key: str
    value: str
    normalized_value: str
    confidence: float
    source_type: str  # pattern_extraction | ai_extraction | user_input | conversation
    assertion_id: Optional[str] = None
    source_id: Optional[str] = None
    workspace_id: Optional[str] = None
    conversation_id: Optional[str] = None
    category: str = "general"
    raw_snippet: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class LocalMemoryPendingChange:
    """Pending update that requires explicit user confirmation."""
    id: str
    user_id: str
    assertion_id: str
    key: str
    old_value: str
    proposed_value: str
    proposed_confidence: float
    type: str  # identity | preference | fact | temporary
    scope: str  # user | workspace | conversation
    workspace_id: Optional[str] = None
    conversation_id: Optional[str] = None
    source_evidence_id: Optional[str] = None
    reason: str = "requires_confirmation"
    status: str = "pending"  # pending | applied | rejected
    created_at: datetime = field(default_factory=datetime.now)
    resolved_at: Optional[datetime] = None


@dataclass
class LocalMemoryIndexRow:
    """Vector index row for canonical assertions."""
    assertion_id: str
    embedding: Optional[bytes] = None
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class LocalUserPreference:
    """User preference learned from interactions"""
    id: str
    category: str  # 'coding_style', 'communication', 'tools'
    key: str
    value: str
    confidence: float = 0.5
    source_conversation_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


# ─────────────────────────────────────────────────────────────
# Stream 1: User Profile Memory (strict, identity-only)
# ─────────────────────────────────────────────────────────────

# Whitelist of valid profile keys. Any key NOT in this set is rejected.
PROFILE_VALID_KEYS = {
    "user_name",
    "user_nickname",
    "user_age",
    "gender",
    "location",
    "occupation",
    "company",
    "education",
    "nationality",
    "birthday",
    "native_language",
    "marital_status",
    "response_style",
    "response_language",
    "tone_preference",
}


@dataclass
class UserProfileField:
    """
    A single field in the user's identity profile.

    Each key is single-value: only one row per (user_id, key) pair.
    Only keys listed in PROFILE_VALID_KEYS are accepted.
    """
    id: str
    user_id: str
    key: str            # Must be in PROFILE_VALID_KEYS
    value: str
    confidence: float = 0.8
    source: str = "pattern"  # "explicit" | "pattern" | "ai" | "manual"
    confirmed: bool = False
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def to_display(self) -> str:
        """Human-readable label for context injection."""
        label = self.key.replace("user_", "").replace("_", " ").title()
        return f"{label}: {self.value}"


# ─────────────────────────────────────────────────────────────
# Stream 2: Context Memory (scoped, flexible, with TTL)
# ─────────────────────────────────────────────────────────────

CONTEXT_SCOPES = {"workspace", "conversation", "temporary"}


@dataclass
class ContextMemoryItem:
    """
    Scoped contextual memory item.

    - workspace scope  → visible only within that workspace
    - conversation scope → relevant only to that conversation
    - temporary scope  → auto-expires after TTL days
    """
    id: str
    user_id: str
    scope: str              # "workspace" | "conversation" | "temporary"
    category: str = "general"  # "project", "preference", "note", "task", ...
    key: str = ""
    value: str = ""
    workspace_id: Optional[str] = None
    conversation_id: Optional[str] = None
    confidence: float = 0.8
    source: str = "pattern"  # "pattern" | "ai" | "manual" | "explicit"
    access_count: int = 0
    expires_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        return self.expires_at <= datetime.now()

    def to_display(self) -> str:
        """Human-readable string for context injection."""
        return f"{self.key.replace('_', ' ')}: {self.value}"
