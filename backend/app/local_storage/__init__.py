"""
Local Storage Module - SQLite-based personal memory for desktop app.
Stores conversations, knowledge items, and user preferences locally.
Supports optional SQLCipher encryption.
"""

from .local_db import LocalDatabase
from .encryption import (
    LocalDatabaseEncryptionUnavailableError,
    LocalDatabaseLockedError,
    LocalStorageEncryptionError,
)
from .local_models import (
    LocalWorkspace,
    LocalConversation,
    LocalMessage,
    LocalKnowledgeItem,
    LocalFact,
    LocalUserPreference,
    LocalMemoryAssertion,
    LocalMemoryEvidence,
    LocalMemoryPendingChange,
    LocalMemoryIndexRow,
    UserProfileField,
    ContextMemoryItem,
    MEMORY_TYPES,
    MEMORY_SCOPES,
    PROFILE_VALID_KEYS,
    CONTEXT_SCOPES,
)

# Lazy import for embedding service (heavy dependency)
def get_embedding_service(model_name: str = "mini"):
    from .embedding_service import get_embedding_service as _get_service
    return _get_service(model_name)

# Lazy import for encryption (requires cryptography)
def get_encryption_manager(user_id: str):
    from .encryption import get_encryption_manager as _get_manager
    return _get_manager(user_id)

def check_encryption_available():
    from .encryption import check_sqlcipher_available
    return check_sqlcipher_available()

__all__ = [
    "LocalDatabase",
    "LocalDatabaseEncryptionUnavailableError",
    "LocalDatabaseLockedError",
    "LocalStorageEncryptionError",
    "LocalWorkspace",
    "LocalConversation",
    "LocalMessage",
    "LocalKnowledgeItem",
    "LocalFact",
    "LocalUserPreference",
    "LocalMemoryAssertion",
    "LocalMemoryEvidence",
    "LocalMemoryPendingChange",
    "LocalMemoryIndexRow",
    "UserProfileField",
    "ContextMemoryItem",
    "MEMORY_TYPES",
    "MEMORY_SCOPES",
    "PROFILE_VALID_KEYS",
    "CONTEXT_SCOPES",
    "get_embedding_service",
    "get_encryption_manager",
    "check_encryption_available",
]
