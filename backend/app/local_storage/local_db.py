"""
Local Database - SQLite storage for personal memory.
Supports sqlite-vec for vector embeddings.
"""

import sqlite3
import os
import json
import struct
import re
import logging
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Tuple, Dict, Any
from contextlib import contextmanager

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
)
from .encryption import (
    EncryptedDatabase,
    LocalDatabaseEncryptionUnavailableError,
    LocalDatabaseLockedError,
)
from .request_scope import get_request_legacy_account_ids
from .scope import get_storage_root, resolve_local_owner_id, iter_legacy_storage_dirs

logger = logging.getLogger(__name__)


def get_storage_dir(user_id: str) -> Path:
    """Get platform-specific storage directory"""
    storage_dir = get_storage_root() / resolve_local_owner_id(user_id)
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir


def serialize_vector(vector: List[float]) -> bytes:
    """Serialize float vector to bytes for sqlite-vec"""
    return struct.pack(f'{len(vector)}f', *vector)


def deserialize_vector(data: bytes) -> List[float]:
    """Deserialize bytes back to float vector"""
    n = len(data) // 4  # 4 bytes per float
    return list(struct.unpack(f'{n}f', data))


class LocalDatabase:
    """
    SQLite database for local storage.
    Handles conversations, messages, knowledge items, and facts.
    Supports optional SQLCipher encryption.
    """
    
    SCHEMA_VERSION = 6  # v6 adds unified memory architecture tables
    _SAFE_LOG_STEM_RE = re.compile(r"[^a-z0-9._-]+")
    _SAFE_ARTIFACT_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
    _SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    
    def __init__(
        self, 
        user_id: str, 
        storage_dir: Optional[Path] = None,
        encryption_password: Optional[str] = None,
        normalize_user_scope: Optional[bool] = None,
    ):
        self.original_user_id = user_id
        self._machine_scoped = (storage_dir is None) if normalize_user_scope is None else bool(normalize_user_scope)
        self.user_id = resolve_local_owner_id(user_id) if self._machine_scoped else user_id
        self._legacy_candidate_owner_ids = {
            candidate
            for candidate in get_request_legacy_account_ids()
            if candidate and candidate != self.user_id
        }
        if self.original_user_id and self.original_user_id != self.user_id:
            self._legacy_candidate_owner_ids.add(self.original_user_id)
        self.storage_dir = storage_dir or get_storage_dir(self.user_id)
        self.db_path = self.storage_dir / "local.db"
        self.brain_dir = self.storage_dir / "brain"
        
        # Encryption support
        self._encryption_key: Optional[str] = None
        self._encryption_manager = None
        self._encryption_enabled = False
        self._sqlcipher_available = False
        self._db_initialized = False
        
        # Try to setup encryption
        self._setup_encryption(encryption_password)
        
        # Create directories
        self.brain_dir.mkdir(parents=True, exist_ok=True)
        (self.brain_dir / "conversations").mkdir(exist_ok=True)
        (self.brain_dir / "knowledge").mkdir(exist_ok=True)
        
        # Initialize database
        if not self.is_encrypted:
            self._init_db()
            self._db_initialized = True
        elif self.is_unlocked and self.encryption_available:
            self._init_db()
            self._db_initialized = True
        elif self.is_locked:
            logger.info("Encrypted local database is locked for user_id=%s; skipping auto-init", self.user_id)
        else:
            logger.warning(
                "Encrypted local database for user_id=%s cannot be opened because SQLCipher is unavailable",
                self.user_id,
            )

        if self._machine_scoped and self._db_initialized:
            self._migrate_legacy_account_storage_if_needed()
    
    def _setup_encryption(self, password: Optional[str] = None):
        """Setup encryption if available and configured"""
        self._encryption_enabled = (self.storage_dir / ".encryption_key").exists()
        try:
            from .encryption import EncryptionManager, check_sqlcipher_available
            
            status = check_sqlcipher_available()
            self._sqlcipher_available = status["sqlcipher_available"]
            
            self._encryption_manager = EncryptionManager(self.user_id, self.storage_dir)
            self._encryption_enabled = self._encryption_manager.is_encrypted
            
            if self._encryption_enabled and password and self._sqlcipher_available:
                # Unlock with provided password
                if self._encryption_manager.unlock(password):
                    self._encryption_key = self._encryption_manager.get_db_key()
            elif self._encryption_enabled and password and not self._sqlcipher_available:
                logger.warning(
                    "Encrypted local database for user_id=%s cannot be unlocked because SQLCipher is unavailable",
                    self.user_id,
                )
            
        except ImportError:
            self._sqlcipher_available = False
            self._encryption_manager = None
    
    @property
    def is_encrypted(self) -> bool:
        """Check if database is encrypted"""
        return self._encryption_enabled

    @property
    def is_unlocked(self) -> bool:
        """Check if encrypted database is currently unlocked for access."""
        return bool(self._encryption_key)

    @property
    def is_locked(self) -> bool:
        """Check if the database is encrypted at rest but not yet unlocked."""
        return self.is_encrypted and not self.is_unlocked
    
    @property  
    def encryption_available(self) -> bool:
        """Check if encryption is available (SQLCipher installed)"""
        return getattr(self, '_sqlcipher_available', False)

    def _configure_connection(self, conn: sqlite3.Connection) -> None:
        """Apply pragmatic defaults for concurrent local reads/writes."""
        for sql in (
            "PRAGMA foreign_keys = ON",
            "PRAGMA busy_timeout = 20000",
            "PRAGMA journal_mode = WAL",
            "PRAGMA synchronous = NORMAL",
        ):
            try:
                conn.execute(sql)
            except Exception as exc:
                logger.debug("SQLite pragma skipped sql=%s error=%s", sql, exc)

    def _legacy_migration_marker_path(self) -> Path:
        return self.storage_dir / ".legacy_account_storage_merged_v1.json"

    def _load_legacy_migration_state(self) -> dict[str, Any]:
        marker_path = self._legacy_migration_marker_path()
        if not marker_path.exists():
            return {
                "scope_id": self.user_id,
                "merged_dirs": [],
                "skipped_dirs": [],
            }

        try:
            loaded = json.loads(marker_path.read_text(encoding="utf-8"))
        except Exception:
            return {
                "scope_id": self.user_id,
                "merged_dirs": [],
                "skipped_dirs": [],
            }

        if not isinstance(loaded, dict):
            return {
                "scope_id": self.user_id,
                "merged_dirs": [],
                "skipped_dirs": [],
            }

        merged_dirs = loaded.get("merged_dirs")
        skipped_dirs = loaded.get("skipped_dirs")
        return {
            **loaded,
            "scope_id": self.user_id,
            "merged_dirs": list(merged_dirs) if isinstance(merged_dirs, list) else [],
            "skipped_dirs": list(skipped_dirs) if isinstance(skipped_dirs, list) else [],
        }

    def _save_legacy_migration_state(self, state: dict[str, Any]) -> None:
        marker_path = self._legacy_migration_marker_path()
        marker_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def _migrate_legacy_account_storage_if_needed(self) -> None:
        if not self._legacy_candidate_owner_ids:
            return

        migration_report = self._load_legacy_migration_state()
        merged_dirs = {
            str(item).strip()
            for item in migration_report.get("merged_dirs", [])
            if str(item).strip()
        }
        skipped_dirs: list[dict[str, Any]] = []

        for legacy_dir in iter_legacy_storage_dirs(
            self.user_id,
            allowed_owner_ids=self._legacy_candidate_owner_ids,
        ):
            if legacy_dir.name in merged_dirs:
                continue
            try:
                self._merge_legacy_storage_dir(legacy_dir)
                merged_dirs.add(legacy_dir.name)
            except LocalDatabaseLockedError:
                skipped_dirs.append({
                    "dir": legacy_dir.name,
                    "reason": "locked",
                })
            except Exception as exc:
                logger.warning("Failed to merge legacy local storage dir=%s error=%s", legacy_dir, exc)
                skipped_dirs.append({
                    "dir": legacy_dir.name,
                    "reason": str(exc),
                })

        migration_report["scope_id"] = self.user_id
        migration_report["candidate_owner_ids"] = sorted(self._legacy_candidate_owner_ids)
        migration_report["merged_dirs"] = sorted(merged_dirs)
        migration_report["skipped_dirs"] = skipped_dirs
        migration_report["migrated_at"] = datetime.now().isoformat()
        self._save_legacy_migration_state(migration_report)

    def _merge_legacy_storage_dir(self, legacy_dir: Path) -> None:
        legacy_db_path = legacy_dir / "local.db"
        if legacy_db_path.exists():
            legacy_db = LocalDatabase(
                legacy_dir.name,
                storage_dir=legacy_dir,
                normalize_user_scope=False,
            )
            payload = legacy_db.export_all_data()
            self.import_from_data(payload, merge=True)

        legacy_brain_dir = legacy_dir / "brain"
        if legacy_brain_dir.exists():
            self._merge_directory_tree(legacy_brain_dir, self.brain_dir)

    def _merge_directory_tree(self, source_dir: Path, target_dir: Path) -> None:
        for source_path in source_dir.rglob("*"):
            if source_path.is_dir():
                continue

            relative_path = source_path.relative_to(source_dir)
            target_path = target_dir / relative_path
            target_path.parent.mkdir(parents=True, exist_ok=True)

            if target_path.exists():
                source_stat = source_path.stat()
                target_stat = target_path.stat()
                if target_stat.st_mtime >= source_stat.st_mtime:
                    continue

            shutil.copy2(source_path, target_path)
    
    def enable_encryption(self, password: str) -> dict:
        """
        Enable encryption for the database.
        
        Args:
            password: Password to protect the database
            
        Returns:
            dict with status and message
        """
        if not self._encryption_manager:
            return {"ok": False, "message": "Encryption module not available"}
        
        if not self._sqlcipher_available:
            return {
                "ok": False, 
                "message": "SQLCipher not available. Install with: pip install sqlcipher3-binary"
            }
        
        if self.is_encrypted:
            return {"ok": False, "message": "Database is already encrypted"}
        
        try:
            # Enable encryption in manager
            if not self._encryption_manager.enable_encryption(password):
                return {"ok": False, "message": "Failed to generate encryption key"}
            
            self._encryption_key = self._encryption_manager.get_db_key()
            self._encryption_enabled = True
            
            # Encrypt existing database
            enc_db = EncryptedDatabase(self.db_path)
            
            if not enc_db.encrypt_existing_database(self._encryption_key):
                # Rollback
                self._encryption_manager.disable_encryption(password)
                self._encryption_key = None
                self._encryption_enabled = False
                return {"ok": False, "message": "Failed to encrypt database file"}
            
            return {"ok": True, "message": "Database encrypted successfully"}
            
        except Exception as e:
            return {"ok": False, "message": str(e)}
    
    def unlock(self, password: str) -> dict:
        """
        Unlock encrypted database.
        
        Args:
            password: Password to unlock
            
        Returns:
            dict with status
        """
        if not self._encryption_manager:
            return {"ok": True, "message": "Encryption not configured"}
        
        if not self.is_encrypted:
            return {"ok": True, "message": "Database is not encrypted"}

        if not self._sqlcipher_available:
            return {
                "ok": False,
                "message": "Encrypted database requires SQLCipher support on this system",
            }
        
        if self._encryption_manager.unlock(password):
            self._encryption_key = self._encryption_manager.get_db_key()
            try:
                if not self._db_initialized:
                    self._init_db()
                    self._db_initialized = True
            except Exception as exc:
                self._encryption_key = None
                return {"ok": False, "message": str(exc)}
            return {"ok": True, "message": "Database unlocked"}
        else:
            return {"ok": False, "message": "Invalid password"}
    
    def change_password(self, old_password: str, new_password: str) -> dict:
        """Change encryption password"""
        if not self._encryption_manager or not self.is_encrypted:
            return {"ok": False, "message": "Database is not encrypted"}
        
        if self._encryption_manager.change_password(old_password, new_password):
            return {"ok": True, "message": "Password changed successfully"}
        else:
            return {"ok": False, "message": "Failed to change password"}
    
    def disable_encryption(self, password: str) -> dict:
        """
        Disable encryption and decrypt database.
        WARNING: This removes encryption protection.
        """
        if not self._encryption_manager or not self.is_encrypted:
            return {"ok": False, "message": "Database is not encrypted"}

        if not self._sqlcipher_available:
            return {
                "ok": False,
                "message": "Encrypted database requires SQLCipher support on this system",
            }

        if not self.is_unlocked:
            unlock_result = self.unlock(password)
            if not unlock_result["ok"]:
                return unlock_result
        
        try:
            # Decrypt database file
            enc_db = EncryptedDatabase(self.db_path, self._encryption_key)
            
            if not enc_db.decrypt_database():
                return {"ok": False, "message": "Failed to decrypt database file"}
            
            # Disable encryption in manager
            if not self._encryption_manager.disable_encryption(password):
                return {"ok": False, "message": "Failed to remove encryption keys"}
            
            self._encryption_key = None
            self._encryption_enabled = False
            return {"ok": True, "message": "Encryption disabled, database decrypted"}
            
        except Exception as e:
            return {"ok": False, "message": str(e)}
    
    def get_encryption_status(self) -> dict:
        """Get current encryption status"""
        return {
            "encrypted": self.is_encrypted,
            "sqlcipher_available": self.encryption_available,
            "locked": self.is_locked,
            "unlocked": self.is_unlocked,
        }
    
    def _init_db(self):
        """Initialize database and create tables"""
        with self._get_connection() as conn:
            # Try to load sqlite-vec extension
            self._try_load_vec_extension(conn)
            
            # Ensure schema_info table exists first (needed by migration check)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_info (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            
            # Run migrations BEFORE creating tables/indexes,
            # so that new columns exist before we reference them in indexes.
            self._check_schema_version(conn)

            # Some legacy snapshots can report a newer schema version while
            # still missing columns (e.g. conversations.workspace_id). Run a
            # self-heal pass before executescript() creates indexes.
            self._ensure_schema_compatibility(conn)
            
            # Create tables (IF NOT EXISTS – safe for existing DBs)
            self._create_tables(conn)

            # Final compatibility pass for legacy databases that may have
            # inconsistent schema_info or partially-migrated tables.
            self._ensure_schema_compatibility(conn)
    
    def _try_load_vec_extension(self, conn: sqlite3.Connection):
        """Try to load sqlite-vec extension for vector operations"""
        try:
            conn.enable_load_extension(True)
            # Try common extension paths
            for ext_name in ['vec0', 'sqlite_vec', 'vector0']:
                try:
                    conn.load_extension(ext_name)
                    self.vec_enabled = True
                    logger.info("Loaded sqlite-vec extension: %s", ext_name)
                    return
                except Exception:
                    continue
            self.vec_enabled = False
        except Exception as e:
            self.vec_enabled = False
            logger.info("sqlite-vec not available; using JSON fallback: %s", e)
    
    def _create_tables(self, conn: sqlite3.Connection):
        """Create all required tables"""
        conn.executescript("""
            -- Schema version tracking
            CREATE TABLE IF NOT EXISTS schema_info (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            
            -- Workspaces (LOCAL - for organizing knowledge items)
            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                icon TEXT DEFAULT '📁',
                color TEXT DEFAULT '#6366f1',
                parent_id TEXT,
                item_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (parent_id) REFERENCES workspaces(id)
            );
            CREATE INDEX IF NOT EXISTS idx_ws_user ON workspaces(user_id);
            CREATE INDEX IF NOT EXISTS idx_ws_parent ON workspaces(parent_id);
            
            -- Conversations
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                workspace_id TEXT,
                title TEXT DEFAULT 'New Conversation',
                summary TEXT,
                total_messages INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                is_archived INTEGER DEFAULT 0,
                importance_score REAL DEFAULT 0.5,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_accessed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id);
            CREATE INDEX IF NOT EXISTS idx_conv_workspace ON conversations(workspace_id);
            
            -- Messages
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                token_count INTEGER DEFAULT 0,
                model TEXT,
                sources_json TEXT,
                embedding BLOB,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            );
            CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id);
            
            -- Knowledge Items
            CREATE TABLE IF NOT EXISTS knowledge_items (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                workspace_id TEXT,
                title TEXT,
                content TEXT,
                content_type TEXT DEFAULT 'note',
                metadata_json TEXT,
                summary TEXT,
                tags TEXT,
                is_favorite INTEGER DEFAULT 0,
                is_pinned INTEGER DEFAULT 0,
                importance_score REAL DEFAULT 0.5,
                access_count INTEGER DEFAULT 0,
                embedding BLOB,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_accessed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_ki_user ON knowledge_items(user_id);
            CREATE INDEX IF NOT EXISTS idx_ki_workspace ON knowledge_items(workspace_id);
            
            -- Facts (Semantic Memory)
            CREATE TABLE IF NOT EXISTS facts (
                id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_id TEXT,
                workspace_id TEXT,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                confidence REAL DEFAULT 1.0,
                access_count INTEGER DEFAULT 0,
                embedding BLOB,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                confirmed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject);
            CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category);
            CREATE INDEX IF NOT EXISTS idx_facts_workspace ON facts(workspace_id);
            
            -- User Preferences
            CREATE TABLE IF NOT EXISTS user_preferences (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                confidence REAL DEFAULT 0.5,
                source_conversation_id TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_pref_category ON user_preferences(category);

            -- Unified Memory Assertions (canonical memory)
            CREATE TABLE IF NOT EXISTS memory_assertions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                type TEXT NOT NULL,
                scope TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                workspace_id TEXT,
                conversation_id TEXT,
                category TEXT DEFAULT 'general',
                confidence REAL DEFAULT 0.8,
                access_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                expires_at TEXT,
                confirmed_at TEXT,
                source_evidence_id TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_assertion_user ON memory_assertions(user_id);
            CREATE INDEX IF NOT EXISTS idx_assertion_scope ON memory_assertions(scope);
            CREATE INDEX IF NOT EXISTS idx_assertion_type ON memory_assertions(type);
            CREATE INDEX IF NOT EXISTS idx_assertion_key ON memory_assertions(key);
            CREATE INDEX IF NOT EXISTS idx_assertion_workspace ON memory_assertions(workspace_id);
            CREATE INDEX IF NOT EXISTS idx_assertion_expires ON memory_assertions(expires_at);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_assertion_active_unique
                ON memory_assertions(
                    user_id,
                    scope,
                    COALESCE(workspace_id, ''),
                    COALESCE(conversation_id, ''),
                    key
                )
                WHERE status = 'active';

            -- Unified Memory Evidence (append-only extraction evidence)
            CREATE TABLE IF NOT EXISTS memory_evidence (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                assertion_id TEXT,
                type TEXT NOT NULL,
                scope TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                normalized_value TEXT NOT NULL,
                confidence REAL NOT NULL,
                source_type TEXT NOT NULL,
                source_id TEXT,
                workspace_id TEXT,
                conversation_id TEXT,
                category TEXT DEFAULT 'general',
                raw_snippet TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (assertion_id) REFERENCES memory_assertions(id)
            );
            CREATE INDEX IF NOT EXISTS idx_evidence_user ON memory_evidence(user_id);
            CREATE INDEX IF NOT EXISTS idx_evidence_assertion ON memory_evidence(assertion_id);
            CREATE INDEX IF NOT EXISTS idx_evidence_key ON memory_evidence(key);
            CREATE INDEX IF NOT EXISTS idx_evidence_created ON memory_evidence(created_at);

            -- Vector index for canonical memory assertions
            CREATE TABLE IF NOT EXISTS memory_index (
                assertion_id TEXT PRIMARY KEY,
                embedding BLOB,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (assertion_id) REFERENCES memory_assertions(id)
            );

            -- Pending memory changes requiring confirmation
            CREATE TABLE IF NOT EXISTS memory_pending_changes (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                assertion_id TEXT NOT NULL,
                key TEXT NOT NULL,
                old_value TEXT NOT NULL,
                proposed_value TEXT NOT NULL,
                proposed_confidence REAL NOT NULL,
                type TEXT NOT NULL,
                scope TEXT NOT NULL,
                workspace_id TEXT,
                conversation_id TEXT,
                source_evidence_id TEXT,
                reason TEXT DEFAULT 'requires_confirmation',
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                resolved_at TEXT,
                FOREIGN KEY (assertion_id) REFERENCES memory_assertions(id)
            );
            CREATE INDEX IF NOT EXISTS idx_pending_user_status ON memory_pending_changes(user_id, status);
            CREATE INDEX IF NOT EXISTS idx_pending_assertion ON memory_pending_changes(assertion_id);
             
            -- Full-text search for knowledge items
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                title, content, tags,
                content=knowledge_items,
                content_rowid=rowid
            );
            
            -- Triggers to keep FTS in sync
            CREATE TRIGGER IF NOT EXISTS knowledge_ai AFTER INSERT ON knowledge_items BEGIN
                INSERT INTO knowledge_fts(rowid, title, content, tags) 
                VALUES (NEW.rowid, NEW.title, NEW.content, NEW.tags);
            END;
            
            CREATE TRIGGER IF NOT EXISTS knowledge_ad AFTER DELETE ON knowledge_items BEGIN
                INSERT INTO knowledge_fts(knowledge_fts, rowid, title, content, tags) 
                VALUES('delete', OLD.rowid, OLD.title, OLD.content, OLD.tags);
            END;
            
            CREATE TRIGGER IF NOT EXISTS knowledge_au AFTER UPDATE ON knowledge_items BEGIN
                INSERT INTO knowledge_fts(knowledge_fts, rowid, title, content, tags) 
                VALUES('delete', OLD.rowid, OLD.title, OLD.content, OLD.tags);
                INSERT INTO knowledge_fts(rowid, title, content, tags) 
                VALUES (NEW.rowid, NEW.title, NEW.content, NEW.tags);
            END;
        """)

    def _check_schema_version(self, conn: sqlite3.Connection):
        """Check and migrate schema if needed"""
        cursor = conn.execute(
            "SELECT value FROM schema_info WHERE key = 'version'"
        )
        row = cursor.fetchone()
        
        if row is None:
            # Legacy DBs can exist without schema_info. Start from v0 and
            # run migration steps instead of jumping straight to latest.
            conn.execute(
                "INSERT INTO schema_info (key, value) VALUES ('version', '0')"
            )
            self._migrate_schema(conn, 0)
        else:
            current_version = int(row[0])
            if current_version < self.SCHEMA_VERSION:
                self._migrate_schema(conn, current_version)
    
    def _migrate_schema(self, conn: sqlite3.Connection, from_version: int):
        """Migrate schema from older version"""
        if from_version < 2:
            # Migration v1 -> v2: Add category and updated_at to facts
            try:
                conn.execute("ALTER TABLE facts ADD COLUMN category TEXT DEFAULT 'general'")
            except sqlite3.OperationalError:
                pass  # Column already exists
            
            try:
                conn.execute("ALTER TABLE facts ADD COLUMN updated_at TEXT DEFAULT CURRENT_TIMESTAMP")
            except sqlite3.OperationalError:
                pass  # Column already exists
            
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category)")
            except sqlite3.OperationalError:
                pass

        if from_version < 4:
            # Migration v3 -> v4: workspace_id on facts for scoped memory
            try:
                conn.execute("ALTER TABLE facts ADD COLUMN workspace_id TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_workspace ON facts(workspace_id)")
            except sqlite3.OperationalError:
                pass

        if from_version < 5:
            # Migration v4 -> v5: workspace_id on conversations for scoped chats
            try:
                conn.execute("ALTER TABLE conversations ADD COLUMN workspace_id TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_workspace ON conversations(workspace_id)")
            except sqlite3.OperationalError:
                pass

        if from_version < 6:
            # Migration v5 -> v6: unified memory architecture tables.
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS memory_assertions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    workspace_id TEXT,
                    conversation_id TEXT,
                    category TEXT DEFAULT 'general',
                    confidence REAL DEFAULT 0.8,
                    access_count INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'active',
                    expires_at TEXT,
                    confirmed_at TEXT,
                    source_evidence_id TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_assertion_user ON memory_assertions(user_id);
                CREATE INDEX IF NOT EXISTS idx_assertion_scope ON memory_assertions(scope);
                CREATE INDEX IF NOT EXISTS idx_assertion_type ON memory_assertions(type);
                CREATE INDEX IF NOT EXISTS idx_assertion_key ON memory_assertions(key);
                CREATE INDEX IF NOT EXISTS idx_assertion_workspace ON memory_assertions(workspace_id);
                CREATE INDEX IF NOT EXISTS idx_assertion_expires ON memory_assertions(expires_at);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_assertion_active_unique
                    ON memory_assertions(
                        user_id,
                        scope,
                        COALESCE(workspace_id, ''),
                        COALESCE(conversation_id, ''),
                        key
                    )
                    WHERE status = 'active';

                CREATE TABLE IF NOT EXISTS memory_evidence (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    assertion_id TEXT,
                    type TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    normalized_value TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT,
                    workspace_id TEXT,
                    conversation_id TEXT,
                    category TEXT DEFAULT 'general',
                    raw_snippet TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (assertion_id) REFERENCES memory_assertions(id)
                );
                CREATE INDEX IF NOT EXISTS idx_evidence_user ON memory_evidence(user_id);
                CREATE INDEX IF NOT EXISTS idx_evidence_assertion ON memory_evidence(assertion_id);
                CREATE INDEX IF NOT EXISTS idx_evidence_key ON memory_evidence(key);
                CREATE INDEX IF NOT EXISTS idx_evidence_created ON memory_evidence(created_at);

                CREATE TABLE IF NOT EXISTS memory_index (
                    assertion_id TEXT PRIMARY KEY,
                    embedding BLOB,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (assertion_id) REFERENCES memory_assertions(id)
                );

                CREATE TABLE IF NOT EXISTS memory_pending_changes (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    assertion_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    old_value TEXT NOT NULL,
                    proposed_value TEXT NOT NULL,
                    proposed_confidence REAL NOT NULL,
                    type TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    workspace_id TEXT,
                    conversation_id TEXT,
                    source_evidence_id TEXT,
                    reason TEXT DEFAULT 'requires_confirmation',
                    status TEXT DEFAULT 'pending',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    resolved_at TEXT,
                    FOREIGN KEY (assertion_id) REFERENCES memory_assertions(id)
                );
                CREATE INDEX IF NOT EXISTS idx_pending_user_status ON memory_pending_changes(user_id, status);
                CREATE INDEX IF NOT EXISTS idx_pending_assertion ON memory_pending_changes(assertion_id);
            """)

        conn.execute(
            "UPDATE schema_info SET value = ? WHERE key = 'version'",
            (str(self.SCHEMA_VERSION),)
        )
        logger.info(
            "Migrated database schema from v%s to v%s",
            from_version,
            self.SCHEMA_VERSION,
        )

    def _column_exists(self, conn: sqlite3.Connection, table: str, column: str) -> bool:
        """Check if a column exists in a table."""
        try:
            cursor = conn.execute(f"PRAGMA table_info({table})")
            return any(row["name"] == column for row in cursor.fetchall())
        except Exception:
            return False

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, definition: str):
        """Best-effort add column if missing (idempotent)."""
        if self._column_exists(conn, table, column):
            return
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        except sqlite3.OperationalError:
            pass

    def _ensure_schema_compatibility(self, conn: sqlite3.Connection):
        """Self-heal required columns/indexes for legacy DB snapshots."""
        # Conversations
        self._ensure_column(conn, "conversations", "workspace_id", "TEXT")

        # Facts
        self._ensure_column(conn, "facts", "workspace_id", "TEXT")
        self._ensure_column(conn, "facts", "category", "TEXT DEFAULT 'general'")
        self._ensure_column(conn, "facts", "updated_at", "TEXT DEFAULT CURRENT_TIMESTAMP")

        # Indexes (safe if columns now exist)
        for sql in (
            "CREATE INDEX IF NOT EXISTS idx_conv_workspace ON conversations(workspace_id)",
            "CREATE INDEX IF NOT EXISTS idx_facts_workspace ON facts(workspace_id)",
            "CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category)",
        ):
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass

        # Unified memory tables (idempotent self-heal for legacy snapshots).
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS memory_assertions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                type TEXT NOT NULL,
                scope TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                workspace_id TEXT,
                conversation_id TEXT,
                category TEXT DEFAULT 'general',
                confidence REAL DEFAULT 0.8,
                access_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                expires_at TEXT,
                confirmed_at TEXT,
                source_evidence_id TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS memory_evidence (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                assertion_id TEXT,
                type TEXT NOT NULL,
                scope TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                normalized_value TEXT NOT NULL,
                confidence REAL NOT NULL,
                source_type TEXT NOT NULL,
                source_id TEXT,
                workspace_id TEXT,
                conversation_id TEXT,
                category TEXT DEFAULT 'general',
                raw_snippet TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS memory_index (
                assertion_id TEXT PRIMARY KEY,
                embedding BLOB,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS memory_pending_changes (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                assertion_id TEXT NOT NULL,
                key TEXT NOT NULL,
                old_value TEXT NOT NULL,
                proposed_value TEXT NOT NULL,
                proposed_confidence REAL NOT NULL,
                type TEXT NOT NULL,
                scope TEXT NOT NULL,
                workspace_id TEXT,
                conversation_id TEXT,
                source_evidence_id TEXT,
                reason TEXT DEFAULT 'requires_confirmation',
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                resolved_at TEXT
            );
        """)
        for sql in (
            "CREATE INDEX IF NOT EXISTS idx_assertion_user ON memory_assertions(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_assertion_scope ON memory_assertions(scope)",
            "CREATE INDEX IF NOT EXISTS idx_assertion_type ON memory_assertions(type)",
            "CREATE INDEX IF NOT EXISTS idx_assertion_key ON memory_assertions(key)",
            "CREATE INDEX IF NOT EXISTS idx_assertion_workspace ON memory_assertions(workspace_id)",
            "CREATE INDEX IF NOT EXISTS idx_assertion_expires ON memory_assertions(expires_at)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_assertion_active_unique ON memory_assertions(user_id, scope, COALESCE(workspace_id, ''), COALESCE(conversation_id, ''), key) WHERE status = 'active'",
            "CREATE INDEX IF NOT EXISTS idx_evidence_user ON memory_evidence(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_evidence_assertion ON memory_evidence(assertion_id)",
            "CREATE INDEX IF NOT EXISTS idx_evidence_key ON memory_evidence(key)",
            "CREATE INDEX IF NOT EXISTS idx_evidence_created ON memory_evidence(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_pending_user_status ON memory_pending_changes(user_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_pending_assertion ON memory_pending_changes(assertion_id)",
        ):
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass

        # Keep schema version synchronized to latest after self-heal.
        # Avoid unnecessary writes on every startup, and don't hard-fail
        # initialization if disk is full while updating this metadata key.
        try:
            cursor = conn.execute("SELECT value FROM schema_info WHERE key = 'version'")
            row = cursor.fetchone()
            current_version = row["value"] if row else None
            if str(current_version) != str(self.SCHEMA_VERSION):
                conn.execute(
                    "INSERT OR REPLACE INTO schema_info (key, value) VALUES ('version', ?)",
                    (str(self.SCHEMA_VERSION),)
                )
        except sqlite3.OperationalError as exc:
            if "database or disk is full" in str(exc).lower():
                logger.warning(
                    "Skipping schema version sync because disk is full: %s",
                    exc
                )
            else:
                raise
    
    @contextmanager
    def _get_connection(self):
        """Get database connection with context manager"""
        if self.is_encrypted:
            if self.is_locked:
                raise LocalDatabaseLockedError(
                    "Local database is encrypted and locked. Unlock it before accessing memory."
                )
            if not self.encryption_available:
                raise LocalDatabaseEncryptionUnavailableError(
                    "Encrypted local database requires SQLCipher support on this system"
                )
            conn = EncryptedDatabase(self.db_path, self._encryption_key).connect()
        else:
            conn = sqlite3.connect(
                self.db_path,
                timeout=20.0,
                check_same_thread=False,
                detect_types=sqlite3.PARSE_DECLTYPES
            )
            conn.row_factory = sqlite3.Row
        self._configure_connection(conn)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    # =========================================================================
    # Workspace Operations (LOCAL)
    # =========================================================================
    
    def save_workspace(self, ws: LocalWorkspace) -> bool:
        """Save or update a workspace"""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO workspaces 
                (id, user_id, name, icon, color, parent_id, item_count, 
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ws.id, ws.user_id, ws.name, ws.icon, ws.color,
                ws.parent_id, ws.item_count,
                ws.created_at.isoformat(), ws.updated_at.isoformat()
            ))
        return True
    
    def get_workspace(self, workspace_id: str) -> Optional[LocalWorkspace]:
        """Get a workspace by ID"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM workspaces WHERE id = ?", 
                (workspace_id,)
            )
            row = cursor.fetchone()
            if row:
                return self._row_to_workspace(row)
        return None
    
    def get_workspaces(
        self, 
        parent_id: Optional[str] = None,
        limit: int = 100
    ) -> List[LocalWorkspace]:
        """Get workspaces for user, optionally filtered by parent"""
        with self._get_connection() as conn:
            if parent_id is None:
                # Get root workspaces (no parent)
                cursor = conn.execute("""
                    SELECT * FROM workspaces 
                    WHERE user_id = ? AND parent_id IS NULL
                    ORDER BY name
                    LIMIT ?
                """, (self.user_id, limit))
            else:
                cursor = conn.execute("""
                    SELECT * FROM workspaces 
                    WHERE user_id = ? AND parent_id = ?
                    ORDER BY name
                    LIMIT ?
                """, (self.user_id, parent_id, limit))
            return [self._row_to_workspace(row) for row in cursor]
    
    def update_workspace(
        self,
        workspace_id: str,
        name: Optional[str] = None,
        icon: Optional[str] = None,
        color: Optional[str] = None
    ) -> Optional[LocalWorkspace]:
        """Update a workspace"""
        ws = self.get_workspace(workspace_id)
        if not ws:
            return None
        
        if name is not None:
            ws.name = name
        if icon is not None:
            ws.icon = icon
        if color is not None:
            ws.color = color
        
        ws.updated_at = datetime.now()
        self.save_workspace(ws)
        return ws
    
    def delete_workspace(self, workspace_id: str) -> bool:
        """Delete a workspace (soft delete - archive related items)"""
        with self._get_connection() as conn:
            # Check if exists
            cursor = conn.execute(
                "SELECT id FROM workspaces WHERE id = ? AND user_id = ?",
                (workspace_id, self.user_id)
            )
            if not cursor.fetchone():
                return False
            
            # Delete workspace
            conn.execute(
                "DELETE FROM workspaces WHERE id = ?",
                (workspace_id,)
            )
            
            # Orphan knowledge items (set workspace_id to NULL)
            conn.execute(
                "UPDATE knowledge_items SET workspace_id = NULL WHERE workspace_id = ?",
                (workspace_id,)
            )

            # Orphan conversations so existing chats remain visible as standalone.
            conn.execute(
                "UPDATE conversations SET workspace_id = NULL WHERE workspace_id = ?",
                (workspace_id,)
            )
        return True
    
    def update_workspace_item_count(self, workspace_id: str) -> int:
        """Update and return the item count for a workspace"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM knowledge_items WHERE workspace_id = ?",
                (workspace_id,)
            )
            count = cursor.fetchone()[0]
            
            conn.execute(
                "UPDATE workspaces SET item_count = ? WHERE id = ?",
                (count, workspace_id)
            )
            return count
    
    def _row_to_workspace(self, row) -> LocalWorkspace:
        """Convert database row to LocalWorkspace"""
        return LocalWorkspace(
            id=row['id'],
            user_id=row['user_id'],
            name=row['name'],
            icon=row['icon'],
            color=row['color'],
            parent_id=row['parent_id'],
            item_count=row['item_count'],
            created_at=datetime.fromisoformat(row['created_at']),
            updated_at=datetime.fromisoformat(row['updated_at'])
        )
    
    # =========================================================================
    # Conversation Operations
    # =========================================================================
    
    def save_conversation(self, conv: LocalConversation) -> bool:
        """Save or update a conversation"""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO conversations 
                (id, user_id, workspace_id, title, summary, total_messages, 
                 total_tokens, is_archived, importance_score, created_at, 
                 updated_at, last_accessed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                conv.id, conv.user_id, conv.workspace_id, conv.title,
                conv.summary, conv.total_messages, conv.total_tokens,
                int(conv.is_archived), conv.importance_score,
                conv.created_at.isoformat(), conv.updated_at.isoformat(),
                conv.last_accessed_at.isoformat() if conv.last_accessed_at else None
            ))
        return True
    
    def get_conversation(self, conv_id: str) -> Optional[LocalConversation]:
        """Get a conversation by ID"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM conversations WHERE id = ? AND user_id = ?",
                (conv_id, self.user_id)
            )
            row = cursor.fetchone()
            if row:
                return self._row_to_conversation(row)
        return None
    
    def get_conversations(
        self, 
        workspace_id: Optional[str] = None,
        limit: int = 50,
        include_archived: bool = False
    ) -> List[LocalConversation]:
        """Get conversations for user"""
        with self._get_connection() as conn:
            query = "SELECT * FROM conversations WHERE user_id = ?"
            params = [self.user_id]
            
            # Filter behavior:
            # - workspace_id is None  -> all conversations
            # - workspace_id is ""    -> standalone conversations (workspace_id IS NULL)
            # - workspace_id is value -> only that workspace
            if workspace_id is not None:
                normalized_workspace_id = workspace_id.strip()
                if normalized_workspace_id == "":
                    query += " AND workspace_id IS NULL"
                else:
                    query += " AND workspace_id = ?"
                    params.append(normalized_workspace_id)

            if not include_archived:
                query += " AND is_archived = 0"
            
            query += " ORDER BY updated_at DESC LIMIT ?"
            params.append(limit)
            
            cursor = conn.execute(query, params)
            return [self._row_to_conversation(row) for row in cursor]

    def archive_empty_conversations(
        self,
        workspace_id: Optional[str] = None,
        keep_conversation_id: Optional[str] = None
    ) -> int:
        """
        Archive empty conversations for current user.

        "Empty" means conversation has no persisted messages yet
        (based on total_messages and messages table existence).
        """
        with self._get_connection() as conn:
            sql = """
                UPDATE conversations
                SET is_archived = 1,
                    updated_at = ?
                WHERE user_id = ?
                  AND is_archived = 0
                  AND (
                    COALESCE(total_messages, 0) <= 0
                    OR NOT EXISTS (
                        SELECT 1
                        FROM messages
                        WHERE messages.conversation_id = conversations.id
                    )
                  )
            """
            params: list = [datetime.now().isoformat(), self.user_id]

            if keep_conversation_id:
                sql += " AND id != ?"
                params.append(keep_conversation_id)

            # Workspace filter semantics mirror get_conversations:
            # - None  -> all workspaces
            # - ""    -> standalone only
            # - value -> specific workspace only
            if workspace_id is not None:
                normalized_workspace_id = workspace_id.strip()
                if normalized_workspace_id == "":
                    sql += " AND workspace_id IS NULL"
                else:
                    sql += " AND workspace_id = ?"
                    params.append(normalized_workspace_id)

            before_changes = conn.total_changes
            conn.execute(sql, params)
            return max(conn.total_changes - before_changes, 0)
    
    def _row_to_conversation(self, row) -> LocalConversation:
        """Convert database row to LocalConversation"""
        def _optional(col: str):
            try:
                return row[col]
            except (KeyError, IndexError):
                return None

        return LocalConversation(
            id=row['id'],
            user_id=row['user_id'],
            workspace_id=_optional('workspace_id'),
            title=row['title'],
            summary=row['summary'],
            total_messages=row['total_messages'],
            total_tokens=row['total_tokens'],
            is_archived=bool(row['is_archived']),
            importance_score=row['importance_score'],
            created_at=datetime.fromisoformat(row['created_at']),
            updated_at=datetime.fromisoformat(row['updated_at']),
            last_accessed_at=datetime.fromisoformat(row['last_accessed_at']) if row['last_accessed_at'] else None
        )
    
    # =========================================================================
    # Message Operations
    # =========================================================================
    
    def save_message(self, msg: LocalMessage, *, update_conversation_stats: bool = True) -> bool:
        """Save a message"""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO messages 
                (id, conversation_id, role, content, token_count, model, 
                 sources_json, embedding, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                msg.id, msg.conversation_id, msg.role, msg.content,
                msg.token_count, msg.model, msg.sources_json,
                msg.embedding, msg.created_at.isoformat()
            ))
            
            if update_conversation_stats:
                # Default write path keeps denormalized conversation counters in sync.
                conn.execute("""
                    UPDATE conversations 
                    SET total_messages = total_messages + 1,
                        total_tokens = total_tokens + ?,
                        updated_at = ?
                    WHERE id = ?
                """, (msg.token_count, datetime.now().isoformat(), msg.conversation_id))
        
        return True
    
    def get_messages(
        self, 
        conversation_id: str,
        limit: Optional[int] = None
    ) -> List[LocalMessage]:
        """Get messages for a conversation"""
        with self._get_connection() as conn:
            query = "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at"
            params = [conversation_id]
            
            if limit:
                query += " DESC LIMIT ?"
                params.append(limit)
                # Re-reverse to get chronological order
                cursor = conn.execute(query, params)
                rows = list(cursor)
                rows.reverse()
            else:
                cursor = conn.execute(query, params)
                rows = list(cursor)
            
            return [self._row_to_message(row) for row in rows]

    def get_message(self, message_id: str) -> Optional[LocalMessage]:
        """Get a single message by ID"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM messages WHERE id = ?",
                (message_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_message(row)

    def update_message(self, msg: LocalMessage) -> bool:
        """Update an existing message and keep conversation token totals in sync."""
        existing = self.get_message(msg.id)
        if not existing:
            return False

        token_delta = (msg.token_count or 0) - (existing.token_count or 0)
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE messages
                SET role = ?,
                    content = ?,
                    token_count = ?,
                    model = ?,
                    sources_json = ?,
                    embedding = ?
                WHERE id = ?
                """,
                (
                    msg.role,
                    msg.content,
                    msg.token_count,
                    msg.model,
                    msg.sources_json,
                    msg.embedding,
                    msg.id,
                ),
            )
            conn.execute(
                """
                UPDATE conversations
                SET total_tokens = total_tokens + ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    token_delta,
                    datetime.now().isoformat(),
                    msg.conversation_id,
                ),
            )

        return True
    
    def get_recent_messages(
        self,
        conversation_id: str,
        max_tokens: int = 8000
    ) -> List[LocalMessage]:
        """Get recent messages up to token limit"""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT * FROM messages 
                WHERE conversation_id = ? 
                ORDER BY created_at DESC
            """, (conversation_id,))
            
            messages = []
            total_tokens = 0
            
            for row in cursor:
                msg = self._row_to_message(row)
                if total_tokens + msg.token_count > max_tokens:
                    break
                messages.append(msg)
                total_tokens += msg.token_count
            
            messages.reverse()  # Return in chronological order
            return messages
    
    def _row_to_message(self, row) -> LocalMessage:
        """Convert database row to LocalMessage"""
        return LocalMessage(
            id=row['id'],
            conversation_id=row['conversation_id'],
            role=row['role'],
            content=row['content'],
            token_count=row['token_count'],
            model=row['model'],
            sources_json=row['sources_json'],
            embedding=row['embedding'],
            created_at=datetime.fromisoformat(row['created_at'])
        )
    
    def search_messages_vector(
        self,
        query_embedding: List[float],
        conversation_id: Optional[str] = None,
        limit: int = 10,
        min_similarity: float = 0.4
    ) -> List[Tuple[LocalMessage, float]]:
        """
        Vector similarity search in messages.
        
        Args:
            query_embedding: Query vector
            conversation_id: Optional - limit to specific conversation
            limit: Max results
            min_similarity: Minimum similarity threshold
            
        Returns:
            List of (message, similarity) tuples
        """
        with self._get_connection() as conn:
            if conversation_id:
                cursor = conn.execute("""
                    SELECT * FROM messages 
                    WHERE conversation_id = ? AND embedding IS NOT NULL
                """, (conversation_id,))
            else:
                cursor = conn.execute("""
                    SELECT * FROM messages 
                    WHERE embedding IS NOT NULL
                    ORDER BY created_at DESC
                    LIMIT 500
                """)
            
            results = []
            
            for row in cursor:
                if row['embedding']:
                    msg_vec = deserialize_vector(row['embedding'])
                    similarity = self._cosine_similarity(query_embedding, msg_vec)
                    
                    if similarity >= min_similarity:
                        msg = self._row_to_message(row)
                        results.append((msg, similarity))
            
            results.sort(key=lambda x: x[1], reverse=True)
            return results[:limit]
    
    # =========================================================================
    # Knowledge Item Operations
    # =========================================================================
    
    def save_knowledge_item(self, item: LocalKnowledgeItem) -> bool:
        """Save or update a knowledge item"""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO knowledge_items 
                (id, user_id, workspace_id, title, content, content_type,
                 metadata_json, summary, tags, is_favorite, is_pinned,
                 importance_score, access_count, embedding, created_at,
                 updated_at, last_accessed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.id, item.user_id, item.workspace_id, item.title,
                item.content, item.content_type, item.metadata_json,
                item.summary, item.tags, int(item.is_favorite),
                int(item.is_pinned), item.importance_score, item.access_count,
                item.embedding, item.created_at.isoformat(),
                item.updated_at.isoformat(),
                item.last_accessed_at.isoformat() if item.last_accessed_at else None
            ))
        return True

    def get_knowledge_item(self, item_id: str) -> Optional[LocalKnowledgeItem]:
        """Get one knowledge item by id."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_items WHERE id = ? AND user_id = ?",
                (item_id, self.user_id),
            ).fetchone()
            if row:
                return self._row_to_knowledge_item(row)
        return None

    def touch_knowledge_item(self, item_id: str) -> bool:
        """Increment access stats for a knowledge item."""
        if not item_id:
            return False

        now_iso = datetime.now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE knowledge_items
                SET access_count = COALESCE(access_count, 0) + 1,
                    last_accessed_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (now_iso, item_id, self.user_id)
            )
            return cursor.rowcount > 0
    
    def search_knowledge_fts(
        self, 
        query: str, 
        limit: int = 10,
        workspace_id: Optional[str] = None
    ) -> List[LocalKnowledgeItem]:
        """Full-text search in knowledge items"""
        # Keep only word tokens to avoid FTS5 parser errors (e.g. "main.py", paths, punctuation).
        import re
        tokens = re.findall(r"\w+", query, flags=re.UNICODE)
        sanitized = " ".join(tokens)
        
        if not sanitized.strip():
            return []
        
        try:
            with self._get_connection() as conn:
                # FTS5 query with escaped terms
                sql = """
                    SELECT k.* FROM knowledge_items k
                    JOIN knowledge_fts f ON k.rowid = f.rowid
                    WHERE knowledge_fts MATCH ? AND k.user_id = ?
                """
                params: list = [sanitized, self.user_id]

                if workspace_id is not None:
                    normalized_workspace_id = workspace_id.strip()
                    if normalized_workspace_id == "":
                        sql += " AND k.workspace_id IS NULL"
                    else:
                        sql += " AND k.workspace_id = ?"
                        params.append(normalized_workspace_id)

                sql += " ORDER BY rank LIMIT ?"
                params.append(limit)

                cursor = conn.execute(sql, params)
                return [self._row_to_knowledge_item(row) for row in cursor]
        except Exception as e:
            # If FTS fails, return empty - no knowledge items found
            logger.warning("FTS search error for user_id=%s: %s", self.user_id, e)
            return []
    
    def search_knowledge_vector(
        self,
        query_embedding: List[float],
        limit: int = 10,
        min_similarity: float = 0.5,
        workspace_id: Optional[str] = None
    ) -> List[Tuple[LocalKnowledgeItem, float]]:
        """Vector similarity search in knowledge items"""
        with self._get_connection() as conn:
            sql = """
                SELECT *, embedding FROM knowledge_items
                WHERE user_id = ? AND embedding IS NOT NULL
            """
            params: list = [self.user_id]

            if workspace_id is not None:
                normalized_workspace_id = workspace_id.strip()
                if normalized_workspace_id == "":
                    sql += " AND workspace_id IS NULL"
                else:
                    sql += " AND workspace_id = ?"
                    params.append(normalized_workspace_id)

            cursor = conn.execute(sql, params)
            
            results = []
            query_vec = query_embedding
            
            for row in cursor:
                if row['embedding']:
                    item_vec = deserialize_vector(row['embedding'])
                    similarity = self._cosine_similarity(query_vec, item_vec)
                    
                    if similarity >= min_similarity:
                        item = self._row_to_knowledge_item(row)
                        results.append((item, similarity))
            
            results.sort(key=lambda x: x[1], reverse=True)
            return results[:limit]
    
    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors"""
        if len(vec1) != len(vec2):
            return 0.0
        
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = sum(a * a for a in vec1) ** 0.5
        norm2 = sum(b * b for b in vec2) ** 0.5
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return dot_product / (norm1 * norm2)
    
    def _row_to_knowledge_item(self, row) -> LocalKnowledgeItem:
        """Convert database row to LocalKnowledgeItem"""
        return LocalKnowledgeItem(
            id=row['id'],
            user_id=row['user_id'],
            workspace_id=row['workspace_id'],
            title=row['title'],
            content=row['content'],
            content_type=row['content_type'],
            metadata_json=row['metadata_json'],
            summary=row['summary'],
            tags=row['tags'],
            is_favorite=bool(row['is_favorite']),
            is_pinned=bool(row['is_pinned']),
            importance_score=row['importance_score'],
            access_count=row['access_count'],
            embedding=row['embedding'],
            created_at=datetime.fromisoformat(row['created_at']),
            updated_at=datetime.fromisoformat(row['updated_at']),
            last_accessed_at=datetime.fromisoformat(row['last_accessed_at']) if row['last_accessed_at'] else None
        )
    
    # =========================================================================
    # Facts Operations (Semantic Memory)
    # =========================================================================
    
    def save_fact(self, fact: LocalFact) -> bool:
        """Save or update a fact"""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO facts 
                (id, source_type, source_id, workspace_id, subject, predicate, object,
                 category, confidence, access_count, embedding, created_at, 
                 updated_at, confirmed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                fact.id, fact.source_type, fact.source_id, fact.workspace_id,
                fact.subject, fact.predicate, fact.object,
                fact.category, fact.confidence, fact.access_count, fact.embedding,
                fact.created_at.isoformat(),
                fact.updated_at.isoformat() if fact.updated_at else datetime.now().isoformat(),
                fact.confirmed_at.isoformat() if fact.confirmed_at else None
            ))
        return True

    def touch_fact(self, fact_id: str) -> bool:
        """Increment access count for a fact."""
        if not fact_id:
            return False

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE facts
                SET access_count = COALESCE(access_count, 0) + 1
                WHERE id = ?
                """,
                (fact_id,)
            )
            return cursor.rowcount > 0
    
    def get_facts_by_subject(self, subject: str) -> List[LocalFact]:
        """Get all facts about a subject"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM facts WHERE subject = ? ORDER BY updated_at DESC, created_at DESC",
                (subject,)
            )
            return [self._row_to_fact(row) for row in cursor]
    
    def get_facts_by_category(self, category: str) -> List[LocalFact]:
        """Get all facts in a category"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM facts WHERE category = ? ORDER BY updated_at DESC, created_at DESC",
                (category,)
            )
            return [self._row_to_fact(row) for row in cursor]

    def get_facts(
        self,
        subject: Optional[str] = None,
        category: Optional[str] = None,
        workspace_id: object = ...,  # sentinel: ... = no filter, None = system only, str = workspace
        limit: int = 100
    ) -> List[LocalFact]:
        """Get facts with optional filters.
        
        workspace_id semantics:
          - ... (Ellipsis, default) -> return ALL facts regardless of scope
          - None  -> system-only facts (workspace_id IS NULL)
          - <id>  -> workspace-specific facts
        """
        with self._get_connection() as conn:
            query = "SELECT * FROM facts WHERE 1=1"
            params: list = []

            if subject is not None:
                query += " AND subject = ?"
                params.append(subject)

            if category is not None:
                query += " AND category = ?"
                params.append(category)

            if workspace_id is not ...:
                if workspace_id is None:
                    query += " AND workspace_id IS NULL"
                else:
                    query += " AND workspace_id = ?"
                    params.append(workspace_id)

            query += " ORDER BY updated_at DESC, created_at DESC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(query, params)
            return [self._row_to_fact(row) for row in cursor]

    def search_facts(
        self,
        query: str,
        workspace_id: object = ...,  # sentinel: ... = all, None = system, str = workspace
        limit: int = 20
    ) -> List[LocalFact]:
        """Search facts by text in subject/predicate/object."""
        normalized = (query or "").strip()
        if not normalized:
            return []

        like = f"%{normalized}%"
        with self._get_connection() as conn:
            sql = """
                SELECT * FROM facts
                WHERE (subject LIKE ? OR predicate LIKE ? OR object LIKE ?)
            """
            params: list = [like, like, like]

            if workspace_id is not ...:
                if workspace_id is None:
                    sql += " AND workspace_id IS NULL"
                else:
                    sql += " AND workspace_id = ?"
                    params.append(workspace_id)

            sql += " ORDER BY confidence DESC, updated_at DESC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(sql, params)
            return [self._row_to_fact(row) for row in cursor]

    def delete_fact(self, fact_id: str) -> bool:
        """Delete a fact by ID."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM facts WHERE id = ?",
                (fact_id,)
            )
            return cursor.rowcount > 0

    def get_fact(self, fact_id: str) -> Optional[LocalFact]:
        """Get one fact by id."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM facts WHERE id = ?",
                (fact_id,),
            ).fetchone()
            if row:
                return self._row_to_fact(row)
        return None
    
    def _row_to_fact(self, row) -> LocalFact:
        """Convert database row to LocalFact"""
        # Helper to safely get optional columns
        def get_col(name, default=None):
            try:
                return row[name]
            except (KeyError, IndexError):
                return default
        
        return LocalFact(
            id=row['id'],
            source_type=row['source_type'],
            source_id=row['source_id'],
            workspace_id=get_col('workspace_id', None),
            subject=row['subject'],
            predicate=row['predicate'],
            object=row['object'],
            category=get_col('category', 'general'),
            confidence=row['confidence'],
            access_count=row['access_count'],
            embedding=row['embedding'],
            created_at=datetime.fromisoformat(row['created_at']),
            updated_at=datetime.fromisoformat(get_col('updated_at')) if get_col('updated_at') else datetime.now(),
            confirmed_at=datetime.fromisoformat(get_col('confirmed_at')) if get_col('confirmed_at') else None
        )

    def search_conversations(
        self,
        query: str,
        workspace_id: Optional[str] = None,
        limit: int = 20,
        include_archived: bool = False
    ) -> List[LocalConversation]:
        """Search conversations by title/summary for the current user."""
        normalized = (query or "").strip()
        if not normalized:
            return []

        like = f"%{normalized}%"
        with self._get_connection() as conn:
            sql = """
                SELECT * FROM conversations
                WHERE user_id = ?
                AND (title LIKE ? OR IFNULL(summary, '') LIKE ?)
            """
            params: list = [self.user_id, like, like]

            if workspace_id is not None:
                normalized_workspace_id = workspace_id.strip()
                if normalized_workspace_id == "":
                    sql += " AND workspace_id IS NULL"
                else:
                    sql += " AND workspace_id = ?"
                    params.append(normalized_workspace_id)

            if not include_archived:
                sql += " AND is_archived = 0"

            sql += " ORDER BY updated_at DESC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(sql, params)
            return [self._row_to_conversation(row) for row in cursor]

    def search_messages_text(
        self,
        query: str,
        conversation_id: Optional[str] = None,
        limit: int = 50
    ) -> List[LocalMessage]:
        """Search message contents for the current user."""
        normalized = (query or "").strip()
        if not normalized:
            return []

        like = f"%{normalized}%"
        with self._get_connection() as conn:
            sql = """
                SELECT m.*
                FROM messages m
                JOIN conversations c ON c.id = m.conversation_id
                WHERE c.user_id = ? AND m.content LIKE ?
            """
            params: list = [self.user_id, like]

            if conversation_id:
                sql += " AND m.conversation_id = ?"
                params.append(conversation_id)

            sql += " ORDER BY m.created_at DESC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(sql, params)
            return [self._row_to_message(row) for row in cursor]
    
    # =========================================================================
    # User Preferences Operations
    # =========================================================================
    
    def save_preference(self, pref: LocalUserPreference) -> bool:
        """Save or update a user preference"""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO user_preferences 
                (id, category, key, value, confidence, source_conversation_id,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                pref.id, pref.category, pref.key, pref.value,
                pref.confidence, pref.source_conversation_id,
                pref.created_at.isoformat(), pref.updated_at.isoformat()
            ))
        return True
    
    def get_preferences(self, category: Optional[str] = None) -> List[LocalUserPreference]:
        """Get user preferences"""
        with self._get_connection() as conn:
            if category:
                cursor = conn.execute(
                    "SELECT * FROM user_preferences WHERE category = ? ORDER BY updated_at DESC, created_at DESC",
                    (category,)
                )
            else:
                cursor = conn.execute("SELECT * FROM user_preferences ORDER BY updated_at DESC, created_at DESC")
                
            return [
                LocalUserPreference(
                    id=row['id'],
                    category=row['category'],
                    key=row['key'],
                    value=row['value'],
                    confidence=row['confidence'],
                    source_conversation_id=row['source_conversation_id'],
                    created_at=datetime.fromisoformat(row['created_at']),
                    updated_at=datetime.fromisoformat(row['updated_at'])
                )
                for row in cursor
            ]

    def delete_preference(self, pref_id: str) -> bool:
        """Delete a preference by ID."""
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM user_preferences WHERE id = ?", (pref_id,))
            return cursor.rowcount > 0

    def get_preference(self, pref_id: str) -> Optional[LocalUserPreference]:
        """Get one preference by id."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM user_preferences WHERE id = ?",
                (pref_id,),
            ).fetchone()
            if row:
                return LocalUserPreference(
                    id=row['id'],
                    category=row['category'],
                    key=row['key'],
                    value=row['value'],
                    confidence=row['confidence'],
                    source_conversation_id=row['source_conversation_id'],
                    created_at=datetime.fromisoformat(row['created_at']),
                    updated_at=datetime.fromisoformat(row['updated_at'])
                )
        return None

    # =========================================================================
    # Unified Memory Operations (Evidence -> Assertion -> Index)
    # =========================================================================

    def save_memory_assertion(self, assertion: LocalMemoryAssertion) -> bool:
        """Save or update a canonical memory assertion."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_assertions
                (id, user_id, type, scope, key, value, workspace_id, conversation_id,
                 category, confidence, access_count, status, expires_at, confirmed_at,
                 source_evidence_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assertion.id,
                    assertion.user_id,
                    assertion.type,
                    assertion.scope,
                    assertion.key,
                    assertion.value,
                    assertion.workspace_id,
                    assertion.conversation_id,
                    assertion.category,
                    assertion.confidence,
                    assertion.access_count,
                    assertion.status,
                    assertion.expires_at.isoformat() if assertion.expires_at else None,
                    assertion.confirmed_at.isoformat() if assertion.confirmed_at else None,
                    assertion.source_evidence_id,
                    assertion.created_at.isoformat(),
                    assertion.updated_at.isoformat(),
                ),
            )
        return True

    def get_memory_assertion(self, assertion_id: str) -> Optional[LocalMemoryAssertion]:
        """Get one memory assertion by id."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM memory_assertions WHERE id = ?",
                (assertion_id,),
            ).fetchone()
            if row:
                return self._row_to_memory_assertion(row)
        return None

    def find_active_memory_assertion(
        self,
        key: str,
        scope: str = "user",
        workspace_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> Optional[LocalMemoryAssertion]:
        """
        Find active assertion by logical identity key.

        This is the canonical lookup path before deciding merge/overwrite.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM memory_assertions
                WHERE user_id = ?
                  AND status = 'active'
                  AND scope = ?
                  AND key = ?
                  AND COALESCE(workspace_id, '') = COALESCE(?, '')
                  AND COALESCE(conversation_id, '') = COALESCE(?, '')
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (
                    self.user_id,
                    (scope or "user").strip().lower(),
                    (key or "").strip().lower(),
                    workspace_id,
                    conversation_id,
                ),
            ).fetchone()
            if row:
                return self._row_to_memory_assertion(row)
        return None

    def get_memory_assertions(
        self,
        type: Optional[str] = None,
        scope: Optional[str] = None,
        workspace_id: object = ...,  # sentinel: ... = all scopes, None = null workspace, str = specific
        conversation_id: object = ...,  # sentinel: ... = all, None = null conversation, str = specific
        include_expired: bool = False,
        status: Optional[str] = "active",
        limit: int = 200,
    ) -> List[LocalMemoryAssertion]:
        """List canonical assertions for the current user."""
        with self._get_connection() as conn:
            sql = "SELECT * FROM memory_assertions WHERE user_id = ?"
            params: List[Any] = [self.user_id]

            if status:
                sql += " AND status = ?"
                params.append(status)

            if type:
                sql += " AND type = ?"
                params.append(type)

            if scope:
                sql += " AND scope = ?"
                params.append(scope)

            if workspace_id is not ...:
                if workspace_id is None:
                    sql += " AND workspace_id IS NULL"
                else:
                    sql += " AND workspace_id = ?"
                    params.append(workspace_id)

            if conversation_id is not ...:
                if conversation_id is None:
                    sql += " AND conversation_id IS NULL"
                else:
                    sql += " AND conversation_id = ?"
                    params.append(conversation_id)

            if not include_expired:
                sql += " AND (expires_at IS NULL OR expires_at > ?)"
                params.append(datetime.now().isoformat())

            sql += " ORDER BY updated_at DESC, created_at DESC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(sql, params)
            return [self._row_to_memory_assertion(row) for row in cursor]

    def touch_memory_assertion(self, assertion_id: str) -> bool:
        """Increment access counter for one assertion."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE memory_assertions
                SET access_count = COALESCE(access_count, 0) + 1,
                    updated_at = ?
                WHERE id = ?
                """,
                (datetime.now().isoformat(), assertion_id),
            )
            return cursor.rowcount > 0

    def search_memory_assertions(
        self,
        query: str,
        scope: Optional[str] = None,
        workspace_id: object = ...,
        conversation_id: object = ...,
        types: Optional[List[str]] = None,
        include_expired: bool = False,
        limit: int = 50,
    ) -> List[LocalMemoryAssertion]:
        """Search active canonical assertions by key/value/category text."""
        text = (query or "").strip()
        if not text:
            return []

        with self._get_connection() as conn:
            sql = "SELECT * FROM memory_assertions WHERE user_id = ? AND status = 'active'"
            params: List[Any] = [self.user_id]

            if scope:
                sql += " AND scope = ?"
                params.append(scope)

            if workspace_id is not ...:
                if workspace_id is None:
                    sql += " AND workspace_id IS NULL"
                else:
                    sql += " AND workspace_id = ?"
                    params.append(workspace_id)

            if conversation_id is not ...:
                if conversation_id is None:
                    sql += " AND conversation_id IS NULL"
                else:
                    sql += " AND conversation_id = ?"
                    params.append(conversation_id)

            if types:
                normalized_types = [str(item).strip().lower() for item in types if str(item).strip()]
                if normalized_types:
                    placeholders = ", ".join(["?"] * len(normalized_types))
                    sql += f" AND type IN ({placeholders})"
                    params.extend(normalized_types)

            if not include_expired:
                sql += " AND (expires_at IS NULL OR expires_at > ?)"
                params.append(datetime.now().isoformat())

            like_value = f"%{text}%"
            sql += " AND (key LIKE ? OR value LIKE ? OR category LIKE ?)"
            params.extend([like_value, like_value, like_value])

            sql += " ORDER BY updated_at DESC, confidence DESC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(sql, params)
            return [self._row_to_memory_assertion(row) for row in cursor]

    def delete_memory_assertion(self, assertion_id: str) -> bool:
        """Hard-delete an assertion and related rows."""
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM memory_index WHERE assertion_id = ?",
                (assertion_id,),
            )
            conn.execute(
                "DELETE FROM memory_pending_changes WHERE assertion_id = ?",
                (assertion_id,),
            )
            conn.execute(
                "UPDATE memory_evidence SET assertion_id = NULL WHERE assertion_id = ?",
                (assertion_id,),
            )
            cursor = conn.execute(
                "DELETE FROM memory_assertions WHERE id = ?",
                (assertion_id,),
            )
            return cursor.rowcount > 0

    def archive_memory_assertion(self, assertion_id: str) -> bool:
        """Soft-delete (archive) assertion without losing history evidence."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE memory_assertions
                SET status = 'archived', updated_at = ?
                WHERE id = ?
                """,
                (datetime.now().isoformat(), assertion_id),
            )
            return cursor.rowcount > 0

    def cleanup_expired_memory_assertions(self) -> int:
        """Archive expired temporary assertions."""
        with self._get_connection() as conn:
            before = conn.total_changes
            conn.execute(
                """
                UPDATE memory_assertions
                SET status = 'archived', updated_at = ?
                WHERE status = 'active'
                  AND expires_at IS NOT NULL
                  AND expires_at <= ?
                """,
                (datetime.now().isoformat(), datetime.now().isoformat()),
            )
            return max(conn.total_changes - before, 0)

    def save_memory_evidence(self, evidence: LocalMemoryEvidence) -> bool:
        """Persist append-only extraction evidence."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_evidence
                (id, user_id, assertion_id, type, scope, key, value, normalized_value,
                 confidence, source_type, source_id, workspace_id, conversation_id,
                 category, raw_snippet, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.id,
                    evidence.user_id,
                    evidence.assertion_id,
                    evidence.type,
                    evidence.scope,
                    evidence.key,
                    evidence.value,
                    evidence.normalized_value,
                    evidence.confidence,
                    evidence.source_type,
                    evidence.source_id,
                    evidence.workspace_id,
                    evidence.conversation_id,
                    evidence.category,
                    evidence.raw_snippet,
                    evidence.created_at.isoformat(),
                ),
            )
        return True

    def get_memory_evidence(
        self,
        assertion_id: Optional[str] = None,
        key: Optional[str] = None,
        limit: int = 200,
    ) -> List[LocalMemoryEvidence]:
        """List evidence rows for current user."""
        with self._get_connection() as conn:
            sql = "SELECT * FROM memory_evidence WHERE user_id = ?"
            params: List[Any] = [self.user_id]
            if assertion_id:
                sql += " AND assertion_id = ?"
                params.append(assertion_id)
            if key:
                sql += " AND key = ?"
                params.append(key)
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(sql, params)
            return [self._row_to_memory_evidence(row) for row in cursor]

    def save_memory_index(self, row: LocalMemoryIndexRow) -> bool:
        """Save vector index row for canonical assertion."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_index
                (assertion_id, embedding, updated_at)
                VALUES (?, ?, ?)
                """,
                (
                    row.assertion_id,
                    row.embedding,
                    row.updated_at.isoformat(),
                ),
            )
        return True

    def get_memory_index(self, assertion_id: str) -> Optional[LocalMemoryIndexRow]:
        """Fetch memory vector index row by assertion id."""
        with self._get_connection() as conn:
            found = conn.execute(
                "SELECT * FROM memory_index WHERE assertion_id = ?",
                (assertion_id,),
            ).fetchone()
            if found:
                return self._row_to_memory_index(found)
        return None

    def save_pending_memory_change(self, change: LocalMemoryPendingChange) -> bool:
        """Create or update pending change for confirmation workflow."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_pending_changes
                (id, user_id, assertion_id, key, old_value, proposed_value, proposed_confidence,
                 type, scope, workspace_id, conversation_id, source_evidence_id, reason, status,
                 created_at, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    change.id,
                    change.user_id,
                    change.assertion_id,
                    change.key,
                    change.old_value,
                    change.proposed_value,
                    change.proposed_confidence,
                    change.type,
                    change.scope,
                    change.workspace_id,
                    change.conversation_id,
                    change.source_evidence_id,
                    change.reason,
                    change.status,
                    change.created_at.isoformat(),
                    change.resolved_at.isoformat() if change.resolved_at else None,
                ),
            )
        return True

    def get_pending_memory_changes(
        self,
        status: str = "pending",
        limit: int = 100,
    ) -> List[LocalMemoryPendingChange]:
        """List pending/applied/rejected changes for current user."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT *
                FROM memory_pending_changes
                WHERE user_id = ? AND status = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (self.user_id, status, limit),
            )
            return [self._row_to_pending_change(row) for row in cursor]

    def resolve_pending_memory_change(self, change_id: str, status: str) -> bool:
        """Mark pending change as applied or rejected."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE memory_pending_changes
                SET status = ?, resolved_at = ?
                WHERE id = ?
                """,
                (status, datetime.now().isoformat(), change_id),
            )
            return cursor.rowcount > 0

    def _row_to_memory_assertion(self, row) -> LocalMemoryAssertion:
        """Convert DB row into LocalMemoryAssertion."""
        return LocalMemoryAssertion(
            id=row["id"],
            user_id=row["user_id"],
            type=row["type"],
            scope=row["scope"],
            key=row["key"],
            value=row["value"],
            workspace_id=row["workspace_id"],
            conversation_id=row["conversation_id"],
            category=row["category"],
            confidence=float(row["confidence"] or 0.0),
            access_count=int(row["access_count"] or 0),
            status=row["status"] or "active",
            expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
            confirmed_at=datetime.fromisoformat(row["confirmed_at"]) if row["confirmed_at"] else None,
            source_evidence_id=row["source_evidence_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _row_to_memory_evidence(self, row) -> LocalMemoryEvidence:
        """Convert DB row into LocalMemoryEvidence."""
        return LocalMemoryEvidence(
            id=row["id"],
            user_id=row["user_id"],
            assertion_id=row["assertion_id"],
            type=row["type"],
            scope=row["scope"],
            key=row["key"],
            value=row["value"],
            normalized_value=row["normalized_value"],
            confidence=float(row["confidence"] or 0.0),
            source_type=row["source_type"],
            source_id=row["source_id"],
            workspace_id=row["workspace_id"],
            conversation_id=row["conversation_id"],
            category=row["category"] or "general",
            raw_snippet=row["raw_snippet"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _row_to_pending_change(self, row) -> LocalMemoryPendingChange:
        """Convert DB row into LocalMemoryPendingChange."""
        return LocalMemoryPendingChange(
            id=row["id"],
            user_id=row["user_id"],
            assertion_id=row["assertion_id"],
            key=row["key"],
            old_value=row["old_value"],
            proposed_value=row["proposed_value"],
            proposed_confidence=float(row["proposed_confidence"] or 0.0),
            type=row["type"],
            scope=row["scope"],
            workspace_id=row["workspace_id"],
            conversation_id=row["conversation_id"],
            source_evidence_id=row["source_evidence_id"],
            reason=row["reason"] or "requires_confirmation",
            status=row["status"] or "pending",
            created_at=datetime.fromisoformat(row["created_at"]),
            resolved_at=datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
        )

    def _row_to_memory_index(self, row) -> LocalMemoryIndexRow:
        """Convert DB row into LocalMemoryIndexRow."""
        return LocalMemoryIndexRow(
            assertion_id=row["assertion_id"],
            embedding=row["embedding"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # =========================================================================
    # Conversation File Logs (Like Cursor/Antigravity)
    # =========================================================================
    def get_conversation_log_dir(self, conversation_id: str) -> Path:
        """Get the directory for a conversation's file-based logs"""
        conversation_segment = (conversation_id or "").strip()
        if not self._SAFE_ID_RE.fullmatch(conversation_segment):
            raise ValueError("Invalid conversation id")
        conv_dir = self.brain_dir / "conversations" / conversation_segment
        conv_dir.mkdir(parents=True, exist_ok=True)
        (conv_dir / "logs").mkdir(exist_ok=True)
        (conv_dir / "artifacts").mkdir(exist_ok=True)
        return conv_dir

    @staticmethod
    def _ensure_child_path(base_dir: Path, child_path: Path) -> Path:
        """Ensure the target path stays within the expected base directory."""
        base_resolved = base_dir.resolve()
        child_resolved = child_path.resolve()
        if child_resolved != base_resolved and base_resolved not in child_resolved.parents:
            raise ValueError("Unsafe path detected")
        return child_resolved

    def _sanitize_log_task_name(self, task_name: str) -> str:
        normalized = (task_name or "").strip().lower().replace(" ", "_")
        normalized = self._SAFE_LOG_STEM_RE.sub("_", normalized).strip("._")
        if not normalized:
            return "task"
        return normalized[:80]

    def _sanitize_artifact_filename(self, filename: str) -> str:
        candidate = (filename or "").strip().replace("\x00", "")
        if not candidate:
            raise ValueError("Filename is required")
        # Reject path traversal and nested path input explicitly.
        if candidate in {".", ".."}:
            raise ValueError("Invalid filename")
        if "/" in candidate or "\\" in candidate:
            raise ValueError("Filename must not contain path separators")
        sanitized = self._SAFE_ARTIFACT_NAME_RE.sub("_", candidate).strip()
        sanitized = sanitized.strip(" .")
        if not sanitized:
            raise ValueError("Invalid filename")
        return sanitized[:180]
    
    def write_conversation_overview(self, conversation_id: str, title: str, summary: str = ""):
        """Write overview.txt for a conversation"""
        conv_dir = self.get_conversation_log_dir(conversation_id)
        overview_file = conv_dir / "overview.txt"
        
        conv = self.get_conversation(conversation_id)
        created = conv.created_at.strftime("%Y-%m-%d %H:%M:%S") if conv else "Unknown"
        updated = conv.updated_at.strftime("%Y-%m-%d %H:%M:%S") if conv else "Unknown"
        
        content = f"""# Conversation: {title}
Created: {created}
Updated: {updated}
Messages: {conv.total_messages if conv else 0}
Tokens: {conv.total_tokens if conv else 0}

## Summary
{summary or 'No summary available.'}
"""
        with open(overview_file, "w", encoding="utf-8") as f:
            f.write(content)
    
    def write_conversation_log(
        self, 
        conversation_id: str, 
        task_name: str,
        content: str
    ) -> Path:
        """Write a task log file for a conversation"""
        conv_dir = self.get_conversation_log_dir(conversation_id)
        logs_dir = conv_dir / "logs"
        
        # Generate sequential log filename
        existing = list(logs_dir.glob("task_*.txt"))
        next_num = len(existing) + 1
        safe_task_name = self._sanitize_log_task_name(task_name)
        log_file = logs_dir / f"task_{next_num:03d}_{safe_task_name}.txt"
        self._ensure_child_path(logs_dir, log_file)
        
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(f"# Task: {task_name}\n")
            f.write(f"# Created: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(content)
        
        return log_file
    
    def export_conversation_to_markdown(self, conversation_id: str) -> str:
        """Export a conversation to markdown format"""
        conv = self.get_conversation(conversation_id)
        if not conv:
            return ""
        
        messages = self.get_messages(conversation_id)
        
        md = f"# {conv.title}\n\n"
        md += f"**Created:** {conv.created_at.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        md += "-" * 50 + "\n\n"
        
        for msg in messages:
            role_label = "👤 **User**" if msg.role == "user" else "🤖 **Assistant**"
            md += f"### {role_label}\n\n"
            md += f"{msg.content}\n\n"
            md += "-" * 30 + "\n\n"
        
        return md
    
    def save_artifact(
        self, 
        conversation_id: str, 
        filename: str, 
        content: str
    ) -> Path:
        """Save an artifact file for a conversation"""
        conv_dir = self.get_conversation_log_dir(conversation_id)
        artifacts_dir = conv_dir / "artifacts"
        safe_filename = self._sanitize_artifact_filename(filename)
        artifact_file = artifacts_dir / safe_filename
        self._ensure_child_path(artifacts_dir, artifact_file)
        
        with open(artifact_file, "w", encoding="utf-8") as f:
            f.write(content)
        
        return artifact_file
    
    def list_conversation_artifacts(self, conversation_id: str) -> List[str]:
        """List all artifacts for a conversation"""
        conversation_segment = (conversation_id or "").strip()
        if not self._SAFE_ID_RE.fullmatch(conversation_segment):
            raise ValueError("Invalid conversation id")
        conv_dir = self.brain_dir / "conversations" / conversation_segment / "artifacts"
        if not conv_dir.exists():
            return []
        return [f.name for f in conv_dir.iterdir() if f.is_file()]
    
    # =========================================================================
    # Export/Import Functionality (Backup & Restore)
    # =========================================================================
    
    def export_all_data(self) -> dict:
        """Export all user data to a dictionary (for backup)"""
        data = {
            "version": self.SCHEMA_VERSION,
            "user_id": self.user_id,
            "exported_at": datetime.now().isoformat(),
            "workspaces": [],
            "knowledge_items": [],
            "conversations": [],
            "messages": [],
            "facts": [],
            "preferences": [],
            "memory_assertions": [],
            "memory_evidence": [],
            "memory_pending_changes": [],
        }
        
        # Export workspaces
        workspaces = self.get_workspaces()
        data["workspaces"] = [
            {
                "id": w.id, "name": w.name, "icon": w.icon, "color": w.color,
                "parent_id": w.parent_id, "item_count": w.item_count,
                "created_at": w.created_at.isoformat(),
                "updated_at": w.updated_at.isoformat()
            } for w in workspaces
        ]
        
        # Export knowledge items
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM knowledge_items WHERE user_id = ?", (self.user_id,))
            for row in cursor:
                data["knowledge_items"].append({
                    "id": row["id"],
                    "workspace_id": row["workspace_id"],
                    "title": row["title"],
                    "content": row["content"],
                    "content_type": row["content_type"],
                    "metadata_json": row["metadata_json"],
                    "summary": row["summary"],
                    "tags": row["tags"],
                    "is_favorite": bool(row["is_favorite"]),
                    "is_pinned": bool(row["is_pinned"]),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"]
                })
        
        # Export conversations
        conversations = self.get_conversations(limit=1000)
        for conv in conversations:
            data["conversations"].append({
                "id": conv.id,
                "workspace_id": conv.workspace_id,
                "title": conv.title,
                "summary": conv.summary,
                "total_messages": conv.total_messages,
                "total_tokens": conv.total_tokens,
                "is_archived": conv.is_archived,
                "importance_score": conv.importance_score,
                "created_at": conv.created_at.isoformat(),
                "updated_at": conv.updated_at.isoformat()
            })
            
            # Export messages for this conversation
            messages = self.get_messages(conv.id)
            for msg in messages:
                data["messages"].append({
                    "id": msg.id,
                    "conversation_id": msg.conversation_id,
                    "role": msg.role,
                    "content": msg.content,
                    "token_count": msg.token_count,
                    "model": msg.model,
                    "sources_json": msg.sources_json,
                    "created_at": msg.created_at.isoformat()
                })
        
        # Export facts
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM facts")
            for row in cursor:
                # Handle column that might not exist
                try:
                    category = row["category"]
                except (IndexError, KeyError):
                    category = "general"
                try:
                    workspace_id = row["workspace_id"]
                except (IndexError, KeyError):
                    workspace_id = None
                try:
                    updated_at = row["updated_at"]
                except (IndexError, KeyError):
                    updated_at = row["created_at"]
                try:
                    confirmed_at = row["confirmed_at"]
                except (IndexError, KeyError):
                    confirmed_at = None
                
                data["facts"].append({
                    "id": row["id"],
                    "source_type": row["source_type"],
                    "source_id": row["source_id"],
                    "workspace_id": workspace_id,
                    "subject": row["subject"],
                    "predicate": row["predicate"],
                    "object": row["object"],
                    "category": category,
                    "confidence": row["confidence"],
                    "access_count": row["access_count"],
                    "created_at": row["created_at"],
                    "updated_at": updated_at,
                    "confirmed_at": confirmed_at,
                })
        
        # Export preferences
        prefs = self.get_preferences()
        data["preferences"] = [
            {
                "id": p.id,
                "category": p.category,
                "key": p.key,
                "value": p.value,
                "confidence": p.confidence,
                "source_conversation_id": p.source_conversation_id,
                "created_at": p.created_at.isoformat(),
                "updated_at": p.updated_at.isoformat()
            } for p in prefs
        ]

        # Export unified memory (canonical + evidence + pending changes)
        data["memory_assertions"] = [
            {
                "id": item.id,
                "user_id": item.user_id,
                "type": item.type,
                "scope": item.scope,
                "key": item.key,
                "value": item.value,
                "workspace_id": item.workspace_id,
                "conversation_id": item.conversation_id,
                "category": item.category,
                "confidence": item.confidence,
                "access_count": item.access_count,
                "status": item.status,
                "expires_at": item.expires_at.isoformat() if item.expires_at else None,
                "confirmed_at": item.confirmed_at.isoformat() if item.confirmed_at else None,
                "source_evidence_id": item.source_evidence_id,
                "created_at": item.created_at.isoformat(),
                "updated_at": item.updated_at.isoformat(),
            }
            for item in self.get_memory_assertions(status=None, include_expired=True, limit=20000)
        ]
        data["memory_evidence"] = [
            {
                "id": row.id,
                "user_id": row.user_id,
                "assertion_id": row.assertion_id,
                "type": row.type,
                "scope": row.scope,
                "key": row.key,
                "value": row.value,
                "normalized_value": row.normalized_value,
                "confidence": row.confidence,
                "source_type": row.source_type,
                "source_id": row.source_id,
                "workspace_id": row.workspace_id,
                "conversation_id": row.conversation_id,
                "category": row.category,
                "raw_snippet": row.raw_snippet,
                "created_at": row.created_at.isoformat(),
            }
            for row in self.get_memory_evidence(limit=50000)
        ]
        pending_rows: List[LocalMemoryPendingChange] = []
        pending_rows.extend(self.get_pending_memory_changes(status="pending", limit=20000))
        pending_rows.extend(self.get_pending_memory_changes(status="applied", limit=20000))
        pending_rows.extend(self.get_pending_memory_changes(status="rejected", limit=20000))
        unique_pending: Dict[str, LocalMemoryPendingChange] = {row.id: row for row in pending_rows}
        data["memory_pending_changes"] = [
            {
                "id": row.id,
                "user_id": row.user_id,
                "assertion_id": row.assertion_id,
                "key": row.key,
                "old_value": row.old_value,
                "proposed_value": row.proposed_value,
                "proposed_confidence": row.proposed_confidence,
                "type": row.type,
                "scope": row.scope,
                "workspace_id": row.workspace_id,
                "conversation_id": row.conversation_id,
                "source_evidence_id": row.source_evidence_id,
                "reason": row.reason,
                "status": row.status,
                "created_at": row.created_at.isoformat(),
                "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
            }
            for row in unique_pending.values()
        ]
        
        return data

    def get_latest_update_at(self) -> Optional[datetime]:
        """Return the most recent local content timestamp used by snapshot sync."""
        candidates: list[datetime] = []
        table_specs = (
            ("workspaces", "updated_at"),
            ("knowledge_items", "updated_at"),
            ("conversations", "updated_at"),
            ("messages", "created_at"),
            ("facts", "updated_at"),
            ("user_preferences", "updated_at"),
            ("memory_assertions", "updated_at"),
            ("memory_evidence", "created_at"),
            ("memory_pending_changes", "created_at"),
        )
        with self._get_connection() as conn:
            for table_name, column_name in table_specs:
                try:
                    row = conn.execute(
                        f"SELECT MAX({column_name}) AS latest_value FROM {table_name}"
                    ).fetchone()
                except sqlite3.OperationalError:
                    continue
                latest_value = row["latest_value"] if row else None
                if not latest_value:
                    continue
                try:
                    candidates.append(datetime.fromisoformat(latest_value))
                except ValueError:
                    continue
        if not candidates:
            return None
        return max(candidates)

    def recompute_conversation_stats(self, conversation_ids: Optional[set[str]] = None) -> None:
        """Rebuild denormalized conversation counters from persisted messages."""
        with self._get_connection() as conn:
            if conversation_ids:
                placeholders = ",".join("?" for _ in conversation_ids)
                params = list(conversation_ids)
                conn.execute(
                    f"""
                    UPDATE conversations
                    SET total_messages = COALESCE((
                            SELECT COUNT(*)
                            FROM messages
                            WHERE messages.conversation_id = conversations.id
                        ), 0),
                        total_tokens = COALESCE((
                            SELECT SUM(COALESCE(token_count, 0))
                            FROM messages
                            WHERE messages.conversation_id = conversations.id
                        ), 0),
                        updated_at = COALESCE(
                            (
                                SELECT MAX(created_at)
                                FROM messages
                                WHERE messages.conversation_id = conversations.id
                            ),
                            updated_at
                        )
                    WHERE id IN ({placeholders})
                    """,
                    params,
                )
                return

            conn.execute(
                """
                UPDATE conversations
                SET total_messages = COALESCE((
                        SELECT COUNT(*)
                        FROM messages
                        WHERE messages.conversation_id = conversations.id
                    ), 0),
                    total_tokens = COALESCE((
                        SELECT SUM(COALESCE(token_count, 0))
                        FROM messages
                        WHERE messages.conversation_id = conversations.id
                    ), 0),
                    updated_at = COALESCE(
                        (
                            SELECT MAX(created_at)
                            FROM messages
                            WHERE messages.conversation_id = conversations.id
                        ),
                        updated_at
                    )
                """
            )
    
    def export_to_file(self, filepath: Optional[Path] = None) -> Path:
        """Export all data to a JSON file"""
        import json
        
        if filepath is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = self.storage_dir / f"backup_{timestamp}.json"
        
        data = self.export_all_data()
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        return filepath

    def import_from_data(self, data: dict, merge: bool = False) -> dict:
        """Import data from an in-memory backup payload."""
        stats = {
            "workspaces": 0,
            "knowledge_items": 0,
            "conversations": 0,
            "messages": 0,
            "facts": 0,
            "preferences": 0,
            "memory_assertions": 0,
            "memory_evidence": 0,
            "memory_pending_changes": 0,
        }
        existing_message_ids: set[str] = set()
        existing_memory_evidence_ids: set[str] = set()
        existing_pending_change_ids: set[str] = set()
        conversation_ids_to_recompute: set[str] = set()
        workspace_ids_to_refresh: set[str] = set()

        def _parse_dt(value: Optional[str], fallback: Optional[datetime] = None) -> datetime:
            if value:
                return datetime.fromisoformat(value)
            return fallback or datetime.now()

        def _incoming_is_stale(
            existing_updated_at: Optional[datetime],
            incoming_updated_at: Optional[datetime],
        ) -> bool:
            if existing_updated_at is None or incoming_updated_at is None:
                return False
            return existing_updated_at > incoming_updated_at
        
        if not merge:
            # Clear existing data
            with self._get_connection() as conn:
                conn.execute("DELETE FROM messages")
                conn.execute("DELETE FROM conversations")
                conn.execute("DELETE FROM knowledge_items")
                conn.execute("DELETE FROM workspaces")
                conn.execute("DELETE FROM facts")
                conn.execute("DELETE FROM user_preferences")
                conn.execute("DELETE FROM memory_pending_changes")
                conn.execute("DELETE FROM memory_evidence")
                conn.execute("DELETE FROM memory_index")
                conn.execute("DELETE FROM memory_assertions")
        else:
            # Keep existing records and avoid duplicate primary-key crashes on merge import.
            with self._get_connection() as conn:
                existing_message_ids = {row["id"] for row in conn.execute("SELECT id FROM messages")}
                existing_memory_evidence_ids = {
                    row["id"] for row in conn.execute("SELECT id FROM memory_evidence")
                }
                existing_pending_change_ids = {
                    row["id"] for row in conn.execute("SELECT id FROM memory_pending_changes")
                }
        
        # Import workspaces
        for ws_data in data.get("workspaces", []):
            updated_at = _parse_dt(ws_data.get("updated_at"), _parse_dt(ws_data.get("created_at")))
            if merge:
                existing_ws = self.get_workspace(ws_data["id"])
                if existing_ws and _incoming_is_stale(existing_ws.updated_at, updated_at):
                    continue
            ws = LocalWorkspace(
                id=ws_data["id"],
                user_id=self.user_id,
                name=ws_data["name"],
                icon=ws_data.get("icon", "📁"),
                color=ws_data.get("color", "#6366f1"),
                parent_id=ws_data.get("parent_id"),
                item_count=ws_data.get("item_count", 0),
                created_at=_parse_dt(ws_data.get("created_at")),
                updated_at=updated_at,
            )
            self.save_workspace(ws)
            workspace_ids_to_refresh.add(ws.id)
            stats["workspaces"] += 1
        
        # Import knowledge items
        for ki_data in data.get("knowledge_items", []):
            updated_at = _parse_dt(ki_data.get("updated_at"), _parse_dt(ki_data.get("created_at")))
            if merge:
                existing_item = self.get_knowledge_item(ki_data["id"])
                if existing_item and _incoming_is_stale(existing_item.updated_at, updated_at):
                    continue
            ki = LocalKnowledgeItem(
                id=ki_data["id"],
                user_id=self.user_id,
                workspace_id=ki_data.get("workspace_id"),
                title=ki_data["title"],
                content=ki_data.get("content"),
                content_type=ki_data.get("content_type", "note"),
                metadata_json=ki_data.get("metadata_json"),
                summary=ki_data.get("summary"),
                tags=ki_data.get("tags"),
                is_favorite=ki_data.get("is_favorite", False),
                is_pinned=ki_data.get("is_pinned", False),
                created_at=_parse_dt(ki_data.get("created_at")),
                updated_at=updated_at,
            )
            self.save_knowledge_item(ki)
            if ki.workspace_id:
                workspace_ids_to_refresh.add(ki.workspace_id)
            stats["knowledge_items"] += 1
        
        # Import conversations
        for conv_data in data.get("conversations", []):
            updated_at = _parse_dt(conv_data.get("updated_at"), _parse_dt(conv_data.get("created_at")))
            if merge:
                existing_conv = self.get_conversation(conv_data["id"])
                if existing_conv and _incoming_is_stale(existing_conv.updated_at, updated_at):
                    continue
            conv = LocalConversation(
                id=conv_data["id"],
                user_id=self.user_id,
                workspace_id=conv_data.get("workspace_id"),
                title=conv_data["title"],
                summary=conv_data.get("summary"),
                total_messages=conv_data.get("total_messages", 0),
                total_tokens=conv_data.get("total_tokens", 0),
                is_archived=conv_data.get("is_archived", False),
                importance_score=conv_data.get("importance_score", 0.5),
                created_at=_parse_dt(conv_data.get("created_at")),
                updated_at=updated_at,
            )
            self.save_conversation(conv)
            conversation_ids_to_recompute.add(conv.id)
            stats["conversations"] += 1
        
        # Import messages
        for msg_data in data.get("messages", []):
            if merge and msg_data["id"] in existing_message_ids:
                continue

            msg = LocalMessage(
                id=msg_data["id"],
                conversation_id=msg_data["conversation_id"],
                role=msg_data["role"],
                content=msg_data["content"],
                token_count=msg_data.get("token_count", 0),
                model=msg_data.get("model"),
                sources_json=msg_data.get("sources_json"),
                created_at=_parse_dt(msg_data.get("created_at"))
            )
            self.save_message(msg, update_conversation_stats=False)
            existing_message_ids.add(msg.id)
            conversation_ids_to_recompute.add(msg.conversation_id)
            stats["messages"] += 1
        
        # Import facts
        for fact_data in data.get("facts", []):
            created_at = _parse_dt(fact_data.get("created_at"))
            updated_at = _parse_dt(fact_data.get("updated_at"), created_at)
            if merge:
                existing_fact = self.get_fact(fact_data["id"])
                if existing_fact and _incoming_is_stale(existing_fact.updated_at, updated_at):
                    continue
            confirmed_at = (
                _parse_dt(fact_data["confirmed_at"])
                if fact_data.get("confirmed_at")
                else None
            )
            fact = LocalFact(
                id=fact_data["id"],
                source_type=fact_data["source_type"],
                source_id=fact_data.get("source_id"),
                workspace_id=fact_data.get("workspace_id"),
                subject=fact_data["subject"],
                predicate=fact_data["predicate"],
                object=fact_data["object"],
                category=fact_data.get("category", "general"),
                confidence=fact_data.get("confidence", 1.0),
                access_count=fact_data.get("access_count", 0),
                created_at=created_at,
                updated_at=updated_at,
                confirmed_at=confirmed_at,
            )
            self.save_fact(fact)
            stats["facts"] += 1
        
        # Import preferences
        for pref_data in data.get("preferences", []):
            updated_at = _parse_dt(pref_data.get("updated_at"), _parse_dt(pref_data.get("created_at")))
            if merge:
                existing_pref = self.get_preference(pref_data["id"])
                if existing_pref and _incoming_is_stale(existing_pref.updated_at, updated_at):
                    continue
            pref = LocalUserPreference(
                id=pref_data["id"],
                category=pref_data["category"],
                key=pref_data["key"],
                value=pref_data["value"],
                confidence=pref_data.get("confidence", 0.5),
                source_conversation_id=pref_data.get("source_conversation_id"),
                created_at=_parse_dt(pref_data.get("created_at")),
                updated_at=updated_at,
            )
            self.save_preference(pref)
            stats["preferences"] += 1

        for item in data.get("memory_assertions", []):
            updated_at = _parse_dt(item.get("updated_at"), _parse_dt(item.get("created_at")))
            if merge:
                existing_assertion = self.get_memory_assertion(item["id"])
                if existing_assertion and _incoming_is_stale(existing_assertion.updated_at, updated_at):
                    continue
            assertion = LocalMemoryAssertion(
                id=item["id"],
                user_id=self.user_id,
                type=item.get("type", "fact"),
                scope=item.get("scope", "user"),
                key=item.get("key", ""),
                value=item.get("value", ""),
                workspace_id=item.get("workspace_id"),
                conversation_id=item.get("conversation_id"),
                category=item.get("category", "general"),
                confidence=float(item.get("confidence", 0.8) or 0.8),
                access_count=int(item.get("access_count", 0) or 0),
                status=item.get("status", "active") or "active",
                expires_at=_parse_dt(item["expires_at"]) if item.get("expires_at") else None,
                confirmed_at=_parse_dt(item["confirmed_at"]) if item.get("confirmed_at") else None,
                source_evidence_id=item.get("source_evidence_id"),
                created_at=_parse_dt(item.get("created_at")),
                updated_at=updated_at,
            )
            self.save_memory_assertion(assertion)
            stats["memory_assertions"] += 1

        for item in data.get("memory_evidence", []):
            if merge and item["id"] in existing_memory_evidence_ids:
                continue
            evidence = LocalMemoryEvidence(
                id=item["id"],
                user_id=self.user_id,
                assertion_id=item.get("assertion_id"),
                type=item.get("type", "fact"),
                scope=item.get("scope", "user"),
                key=item.get("key", ""),
                value=item.get("value", ""),
                normalized_value=item.get("normalized_value", ""),
                confidence=float(item.get("confidence", 0.0) or 0.0),
                source_type=item.get("source_type", "pattern_extraction"),
                source_id=item.get("source_id"),
                workspace_id=item.get("workspace_id"),
                conversation_id=item.get("conversation_id"),
                category=item.get("category", "general"),
                raw_snippet=item.get("raw_snippet"),
                created_at=_parse_dt(item.get("created_at")),
            )
            self.save_memory_evidence(evidence)
            existing_memory_evidence_ids.add(evidence.id)
            stats["memory_evidence"] += 1

        for item in data.get("memory_pending_changes", []):
            if merge and item["id"] in existing_pending_change_ids:
                continue
            change = LocalMemoryPendingChange(
                id=item["id"],
                user_id=self.user_id,
                assertion_id=item["assertion_id"],
                key=item.get("key", ""),
                old_value=item.get("old_value", ""),
                proposed_value=item.get("proposed_value", ""),
                proposed_confidence=float(item.get("proposed_confidence", 0.0) or 0.0),
                type=item.get("type", "fact"),
                scope=item.get("scope", "user"),
                workspace_id=item.get("workspace_id"),
                conversation_id=item.get("conversation_id"),
                source_evidence_id=item.get("source_evidence_id"),
                reason=item.get("reason", "requires_confirmation"),
                status=item.get("status", "pending"),
                created_at=_parse_dt(item.get("created_at")),
                resolved_at=_parse_dt(item["resolved_at"]) if item.get("resolved_at") else None,
            )
            self.save_pending_memory_change(change)
            existing_pending_change_ids.add(change.id)
            stats["memory_pending_changes"] += 1

        if conversation_ids_to_recompute:
            self.recompute_conversation_stats(conversation_ids_to_recompute)
        for workspace_id in workspace_ids_to_refresh:
            self.update_workspace_item_count(workspace_id)
        
        return stats
    
    def import_from_file(self, filepath: Path, merge: bool = False) -> dict:
        """Import data from a JSON backup file
        
        Args:
            filepath: Path to the backup file
            merge: If True, merge with existing data. If False, clear existing data first.
        
        Returns:
            dict with import statistics
        """
        import json
        
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return self.import_from_data(data, merge=merge)
    
    # =========================================================================
    # Memory Decay & Cleanup
    # =========================================================================
    
    def cleanup_old_data(
        self,
        days_threshold: int = 90,
        keep_favorites: bool = True,
        keep_important: bool = True
    ) -> dict:
        """Clean up old, unused data to save space
        
        Args:
            days_threshold: Delete items older than this many days
            keep_favorites: Don't delete favorited items
            keep_important: Don't delete high-importance items (score > 0.7)
        
        Returns:
            dict with cleanup statistics
        """
        from datetime import timedelta
        
        cutoff_date = (datetime.now() - timedelta(days=days_threshold)).isoformat()
        stats = {
            "conversations_archived": 0,
            "messages_deleted": 0,
            "knowledge_items_archived": 0,
            "facts_deleted": 0
        }
        
        with self._get_connection() as conn:
            # Archive old conversations
            if keep_important:
                archive_query = """
                    UPDATE conversations
                    SET is_archived = 1
                    WHERE updated_at < ? AND is_archived = 0 AND importance_score < 0.7
                """
            else:
                archive_query = """
                    UPDATE conversations
                    SET is_archived = 1
                    WHERE updated_at < ? AND is_archived = 0
                """

            conn.execute(archive_query, (cutoff_date,))
            stats["conversations_archived"] = conn.total_changes
            
            # Delete messages from archived conversations (keep last 10)
            cursor = conn.execute("""
                SELECT id FROM conversations WHERE is_archived = 1
            """)
            
            for row in cursor:
                conv_id = row[0]
                # Keep only the last 10 messages
                conn.execute("""
                    DELETE FROM messages WHERE conversation_id = ? 
                    AND id NOT IN (
                        SELECT id FROM messages WHERE conversation_id = ?
                        ORDER BY created_at DESC LIMIT 10
                    )
                """, (conv_id, conv_id))
                stats["messages_deleted"] += conn.total_changes
            
            # Count low-access facts to delete
            conn.execute(
                "DELETE FROM facts WHERE access_count = 0 AND created_at < ?",
                (cutoff_date,),
            )
            stats["facts_deleted"] = conn.total_changes
        
        return stats
    
    def get_storage_stats(self) -> dict:
        """Get storage statistics"""
        stats = {
            "db_size_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
            "brain_size_bytes": sum(
                f.stat().st_size for f in self.brain_dir.rglob("*") if f.is_file()
            ) if self.brain_dir.exists() else 0
        }
        
        with self._get_connection() as conn:
            stats["workspace_count"] = conn.execute(
                "SELECT COUNT(*) FROM workspaces WHERE user_id = ?", 
                (self.user_id,)
            ).fetchone()[0]
            
            stats["knowledge_item_count"] = conn.execute(
                "SELECT COUNT(*) FROM knowledge_items WHERE user_id = ?",
                (self.user_id,)
            ).fetchone()[0]
            
            stats["conversation_count"] = conn.execute(
                "SELECT COUNT(*) FROM conversations WHERE user_id = ?",
                (self.user_id,)
            ).fetchone()[0]
            
            stats["archived_conversation_count"] = conn.execute(
                "SELECT COUNT(*) FROM conversations WHERE user_id = ? AND is_archived = 1",
                (self.user_id,)
            ).fetchone()[0]
            
            stats["message_count"] = conn.execute(
                "SELECT COUNT(*) FROM messages"
            ).fetchone()[0]
            
            stats["legacy_fact_count"] = conn.execute(
                "SELECT COUNT(*) FROM facts"
            ).fetchone()[0]
            
            stats["legacy_preference_count"] = conn.execute(
                "SELECT COUNT(*) FROM user_preferences"
            ).fetchone()[0]

            assertion_fact_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM memory_assertions
                WHERE user_id = ?
                  AND status = 'active'
                  AND type IN ('identity', 'fact', 'temporary')
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (self.user_id, datetime.now().isoformat()),
            ).fetchone()[0]

            assertion_preference_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM memory_assertions
                WHERE user_id = ?
                  AND status = 'active'
                  AND type = 'preference'
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (self.user_id, datetime.now().isoformat()),
            ).fetchone()[0]

            stats["fact_count"] = max(int(assertion_fact_count or 0), int(stats["legacy_fact_count"] or 0))
            stats["preference_count"] = max(
                int(assertion_preference_count or 0),
                int(stats["legacy_preference_count"] or 0),
            )

            stats["memory_assertion_count"] = conn.execute(
                "SELECT COUNT(*) FROM memory_assertions WHERE user_id = ?",
                (self.user_id,),
            ).fetchone()[0]

            stats["memory_evidence_count"] = conn.execute(
                "SELECT COUNT(*) FROM memory_evidence WHERE user_id = ?",
                (self.user_id,),
            ).fetchone()[0]

            stats["memory_pending_change_count"] = conn.execute(
                "SELECT COUNT(*) FROM memory_pending_changes WHERE user_id = ? AND status = 'pending'",
                (self.user_id,),
            ).fetchone()[0]
        
        # Human readable size
        def format_size(bytes):
            for unit in ['B', 'KB', 'MB', 'GB']:
                if bytes < 1024:
                    return f"{bytes:.1f} {unit}"
                bytes /= 1024
            return f"{bytes:.1f} TB"
        
        stats["db_size_human"] = format_size(stats["db_size_bytes"])
        stats["brain_size_human"] = format_size(stats["brain_size_bytes"])
        stats["total_size_human"] = format_size(
            stats["db_size_bytes"] + stats["brain_size_bytes"]
        )
        
        return stats
    
    def vacuum_database(self):
        """Vacuum the database to reclaim space after deletions"""
        with self._get_connection() as conn:
            conn.execute("VACUUM")
