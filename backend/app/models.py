from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

from .database import Base


def generate_uuid():
    return str(uuid.uuid4())


class User(Base):
    """User account for PigTex"""
    __tablename__ = "users"
    
    id = Column(String(36), primary_key=True, default=generate_uuid)
    email = Column(String(255), unique=True, nullable=False, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    name = Column(String(191), nullable=False, default="PigTex User")
    hashed_password = Column(String(255), nullable=True)
    role = Column(String(32), nullable=False, default="user", server_default="user")
    
    # Subscription info
    plan = Column(String(50), default="free")  # free, sync, sync_plus
    is_active = Column(Boolean, default=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_login = Column(DateTime(timezone=True))
    
    # Relationships
    usage_records = relationship("UsageRecord", back_populates="user")
    api_keys = relationship("ApiKey", back_populates="user")
    oauth_accounts = relationship("OAuthAccount", back_populates="user")
    admin_audit_events = relationship("AdminAuditEvent", back_populates="actor")


class AdminAuditEvent(Base):
    """Append-only audit trail for privileged admin actions."""
    __tablename__ = "admin_audit_events"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    actor_user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    action = Column(String(64), nullable=False, index=True)
    resource_type = Column(String(64), nullable=False, index=True)
    resource_id = Column(String(191), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="success", server_default="success")
    summary = Column(String(255), nullable=True)
    before_json = Column(Text, nullable=True)
    after_json = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    actor = relationship("User", back_populates="admin_audit_events")


class OAuthAccount(Base):
    """OAuth account linkage for third-party sign-in providers."""
    __tablename__ = "oauth_accounts"
    __table_args__ = (
        UniqueConstraint("provider", "provider_account_id", name="uq_oauth_accounts_provider_account"),
    )

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    provider = Column(String(32), nullable=False)
    provider_account_id = Column(String(191), nullable=False)
    email = Column(String(255), nullable=True)
    avatar_url = Column(String(512), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="oauth_accounts")


class UsageRecord(Base):
    """Track API usage per user"""
    __tablename__ = "usage_records"
    
    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    
    # Usage metrics
    model = Column(String(100))  # gpt-4o, gemini-pro, etc.
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    
    # Cost tracking (for pay-as-you-go)
    cost = Column(Float, default=0.0)
    
    # Request info
    endpoint = Column(String(100))  # /chat/completions, etc.
    status = Column(String(20))  # success, error
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    user = relationship("User", back_populates="usage_records")


class ApiKey(Base):
    """API Keys for users to access PigTex API"""
    __tablename__ = "api_keys"
    
    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    
    key = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(100))
    is_active = Column(Boolean, default=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_used = Column(DateTime(timezone=True))
    
    # Relationships
    user = relationship("User", back_populates="api_keys")

# =============================================================================
# Super Context Memory Models
# =============================================================================

class Workspace(Base):
    __tablename__ = "workspaces"
    
    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    name = Column(String(100), nullable=False)
    icon = Column(String(50), default="📁")
    color = Column(String(20), default="#6366f1")
    parent_id = Column(String(36), ForeignKey("workspaces.id"), nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    user = relationship("User", backref="workspaces")
    parent = relationship("Workspace", remote_side=[id], backref="children")
    knowledge_items = relationship("KnowledgeItem", back_populates="workspace")
    conversations = relationship("Conversation", back_populates="workspace")


class KnowledgeItem(Base):
    """Knowledge item - document, note, code snippet, etc."""
    __tablename__ = "knowledge_items"
    
    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id"), nullable=False)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    
    title = Column(String(200), nullable=False)
    content = Column(Text)
    content_type = Column(String(50), default="note")  # note, code, doc, link, file
    
    # Metadata as JSON
    metadata_json = Column(Text)  # JSON string for flexibility
    
    # Vector embedding (stored as JSON array for portability)
    # In production, use pgvector's VECTOR type
    embedding_json = Column(Text)  # JSON array of floats
    
    # Favorites and pinning
    is_favorite = Column(Boolean, default=False)
    is_pinned = Column(Boolean, default=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    workspace = relationship("Workspace", back_populates="knowledge_items")
    user = relationship("User", backref="knowledge_items")


class Conversation(Base):
    """Conversation in episodic memory"""
    __tablename__ = "conversations"
    
    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    workspace_id = Column(String(36), ForeignKey("workspaces.id"), nullable=True)
    
    title = Column(String(200))
    summary = Column(Text)
    
    # Context tracking
    total_messages = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    user = relationship("User", backref="conversations")
    workspace = relationship("Workspace", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", order_by="Message.created_at")


class Message(Base):
    """Chat message in a conversation"""
    __tablename__ = "messages"
    
    id = Column(String(36), primary_key=True, default=generate_uuid)
    conversation_id = Column(String(36), ForeignKey("conversations.id"), nullable=False)
    
    role = Column(String(20), nullable=False)  # user, assistant, system
    content = Column(Text, nullable=False)
    
    # Token count for context management
    token_count = Column(Integer, default=0)
    
    # Model used for generation (if assistant)
    model = Column(String(100))
    
    # Vector embedding for semantic search
    embedding_json = Column(Text)
    
    # Source citations (JSON array of knowledge item IDs)
    sources_json = Column(Text)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    conversation = relationship("Conversation", back_populates="messages")


# =============================================================================
# "BƠM NGẦM" Models - Core AI Enhancement (Server-side, hidden from users)
# =============================================================================

class SystemPrompt(Base):
    """
    Master system prompts injected into every AI request.
    These are the "secret sauce" that makes PigTex AI better.
    """
    __tablename__ = "system_prompts"
    
    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(100), nullable=False, unique=True)  # 'default_assistant', 'code_expert'
    version = Column(Integer, default=1)
    
    # The actual prompt content
    prompt_content = Column(Text, nullable=False)
    
    # Targeting - which models/tiers should use this
    target_models = Column(Text)  # JSON: ['gpt-4o', 'gemini-pro']
    target_tiers = Column(Text)   # JSON: ['free', 'pro', 'unlimited']
    
    # A/B Testing support
    is_active = Column(Boolean, default=True)
    weight = Column(Integer, default=100)  # % chance of being selected
    
    # Metadata
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Skill(Base):
    """
    Specific AI techniques/skills (Chain of Thought, RAG instructions, etc.)
    Auto-injected based on user intent detection.
    """
    __tablename__ = "skills"
    
    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(100), nullable=False, unique=True)  # 'chain_of_thought', 'code_review'
    category = Column(String(50))  # 'reasoning', 'coding', 'writing', 'analysis'
    
    # Skill content
    instruction = Column(Text, nullable=False)  # The actual skill instructions
    examples = Column(Text)  # JSON: Few-shot examples
    
    # Auto-trigger conditions
    trigger_keywords = Column(Text)  # JSON: keywords that activate this skill
    trigger_intent = Column(String(50))  # 'code_generation', 'analysis', 'summarize'
    
    # Settings
    priority = Column(Integer, default=50)  # Higher = more important
    is_active = Column(Boolean, default=True)
    
    # Metadata
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PromptTemplate(Base):
    """
    Reusable prompt templates for specific tasks.
    Used internally to structure AI requests.
    """
    __tablename__ = "prompt_templates"
    
    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(100), nullable=False, unique=True)
    category = Column(String(50))  # 'summarize', 'translate', 'code_review', 'explain'
    
    # Template content with placeholders
    template = Column(Text, nullable=False)  # "Summarize this in {{language}}: {{content}}"
    
    # Metadata
    required_vars = Column(Text)  # JSON: ['content', 'language']
    output_format = Column(String(50))  # 'markdown', 'json', 'code', 'plain'
    
    # Settings
    is_active = Column(Boolean, default=True)
    
    # Metadata
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# =============================================================================
# Cloud Backup Models
# =============================================================================

class UserDevice(Base):
    """Registered desktop device for cloud backup and restore flows."""
    __tablename__ = "user_devices"
    __table_args__ = (
        UniqueConstraint("user_id", "device_key", name="uq_user_devices_user_device_key"),
    )

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    device_key = Column(String(191), nullable=False)
    device_name = Column(String(100), nullable=False)
    platform = Column(String(32), nullable=False)
    app_version = Column(String(50), nullable=True)

    last_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_backup_at = Column(DateTime(timezone=True), nullable=True)
    last_sync_push_at = Column(DateTime(timezone=True), nullable=True)
    last_sync_pull_at = Column(DateTime(timezone=True), nullable=True)
    last_restore_at = Column(DateTime(timezone=True), nullable=True)
    auto_sync_enabled = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", backref="cloud_devices")


class CloudStorageQuota(Base):
    """Per-user cloud backup quota and retention settings."""
    __tablename__ = "cloud_storage_quotas"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, unique=True, index=True)
    plan_code = Column(String(64), nullable=False, default="cloud_default")
    quota_bytes = Column(BigInteger, nullable=False, default=0)
    retention_days = Column(Integer, nullable=False, default=30)
    max_devices = Column(Integer, nullable=False, default=5)
    max_snapshots = Column(Integer, nullable=False, default=30)
    usage_bytes_cached = Column(BigInteger, nullable=False, default=0)
    sync_enabled = Column(Boolean, nullable=False, default=False, server_default="0")
    device_transfer_enabled = Column(Boolean, nullable=False, default=False, server_default="0")
    priority_level = Column(Integer, nullable=False, default=0, server_default="0")
    quota_source = Column(String(32), nullable=False, default="system_default", server_default="system_default")
    frozen_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", backref="cloud_storage_quota")


class CloudSnapshotManifest(Base):
    """Metadata for one immutable cloud backup snapshot."""
    __tablename__ = "cloud_snapshot_manifests"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    device_id = Column(String(36), ForeignKey("user_devices.id"), nullable=False, index=True)
    scope_type = Column(String(32), nullable=False, default="account")
    scope_id = Column(String(64), nullable=True)
    snapshot_kind = Column(String(32), nullable=False, default="full")
    status = Column(String(32), nullable=False, default="upload_requested")
    manifest_version = Column(Integer, nullable=False, default=1)
    base_snapshot_id = Column(String(36), nullable=True, index=True)

    bucket_name = Column(String(191), nullable=False)
    manifest_object_key = Column(String(512), nullable=False)
    payload_object_key = Column(String(512), nullable=False)
    storage_class = Column(String(32), nullable=False, default="STANDARD")
    payload_size_bytes = Column(BigInteger, nullable=False, default=0)
    payload_sha256 = Column(String(64), nullable=False, default="")
    encrypted = Column(Boolean, nullable=False, default=False)
    encryption_scheme = Column(String(64), nullable=True)
    trigger_reason = Column(String(32), nullable=False, default="manual", server_default="manual")
    is_counted_for_quota = Column(Boolean, nullable=False, default=True, server_default="1")
    counts_json = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    failed_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", backref="cloud_snapshot_manifests")
    device = relationship("UserDevice", backref="cloud_snapshot_manifests")


class CloudRestoreJob(Base):
    """One restore request from a ready snapshot onto a target device."""
    __tablename__ = "cloud_restore_jobs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    snapshot_id = Column(String(36), ForeignKey("cloud_snapshot_manifests.id"), nullable=False, index=True)
    target_device_id = Column(String(36), ForeignKey("user_devices.id"), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="requested")
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", backref="cloud_restore_jobs")
    snapshot = relationship("CloudSnapshotManifest", backref="restore_jobs")
    target_device = relationship("UserDevice", backref="restore_jobs")


class SyncBillingCustomer(Base):
    """Billing customer record for PigTex Sync subscriptions."""
    __tablename__ = "sync_billing_customers"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, unique=True, index=True)
    provider = Column(String(32), nullable=False, default="mock", server_default="mock")
    provider_customer_id = Column(String(191), nullable=True, unique=True, index=True)
    email = Column(String(255), nullable=True)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User", backref="sync_billing_customer")


class SyncBillingSubscription(Base):
    """Subscription lifecycle for PigTex Sync."""
    __tablename__ = "sync_billing_subscriptions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    customer_id = Column(String(36), ForeignKey("sync_billing_customers.id"), nullable=True, index=True)
    provider = Column(String(32), nullable=False, default="mock", server_default="mock")
    provider_subscription_id = Column(String(191), nullable=True, unique=True, index=True)
    provider_price_id = Column(String(191), nullable=True)
    plan_code = Column(String(32), nullable=False, default="sync", server_default="sync")
    billing_cycle = Column(String(16), nullable=False, default="monthly", server_default="monthly")
    status = Column(String(32), nullable=False, default="pending", server_default="pending")
    cancel_at_period_end = Column(Boolean, nullable=False, default=False, server_default="0")
    current_period_start = Column(DateTime(timezone=True), nullable=True)
    current_period_end = Column(DateTime(timezone=True), nullable=True)
    grace_ends_at = Column(DateTime(timezone=True), nullable=True)
    canceled_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User", backref="sync_billing_subscriptions")
    customer = relationship("SyncBillingCustomer", backref="subscriptions")


class SyncBillingEvent(Base):
    """Webhook or provider event log for PigTex Sync billing."""
    __tablename__ = "sync_billing_events"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    provider = Column(String(32), nullable=False, default="mock", server_default="mock")
    event_type = Column(String(64), nullable=False)
    provider_event_id = Column(String(191), nullable=True, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    subscription_id = Column(String(36), ForeignKey("sync_billing_subscriptions.id"), nullable=True, index=True)
    signature_valid = Column(Boolean, nullable=False, default=False, server_default="0")
    payload_json = Column(Text, nullable=False)
    processed = Column(Boolean, nullable=False, default=False, server_default="0")
    processed_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", backref="sync_billing_events")
    subscription = relationship("SyncBillingSubscription", backref="events")



