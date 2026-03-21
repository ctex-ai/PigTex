"""
Encryption Module - Secure local database with SQLCipher.
Manages encryption keys and provides encrypted database connections.
"""

import os
import hashlib
import base64
import secrets
import logging
from pathlib import Path
from typing import Optional
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)


class LocalStorageEncryptionError(RuntimeError):
    """Base error for local encrypted storage failures."""


class LocalDatabaseLockedError(LocalStorageEncryptionError):
    """Raised when an encrypted local database has not been unlocked."""


class LocalDatabaseEncryptionUnavailableError(LocalStorageEncryptionError):
    """Raised when SQLCipher support is required but unavailable."""


class EncryptionManager:
    """
    Manages encryption for local storage.
    Uses SQLCipher for database encryption and Fernet for key storage.
    """
    
    # Key derivation parameters
    SALT_SIZE = 16
    KEY_LENGTH = 32
    ITERATIONS = 480000  # OWASP recommended
    
    def __init__(self, user_id: str, storage_dir: Optional[Path] = None):
        self.user_id = user_id
        
        if storage_dir is None:
            from .local_db import get_storage_dir
            storage_dir = get_storage_dir(user_id)
        
        self.storage_dir = storage_dir
        self.key_file = storage_dir / ".encryption_key"
        self.salt_file = storage_dir / ".salt"
        
        # Encryption state
        self._db_key: Optional[str] = None
        self._is_encrypted = False

    @staticmethod
    def _set_windows_hidden(path: Path, hidden: bool) -> None:
        """Set or clear Windows hidden file attribute when supported."""
        if os.name != "nt":
            return
        try:
            import ctypes
            FILE_ATTRIBUTE_HIDDEN = 0x2
            FILE_ATTRIBUTE_NORMAL = 0x80
            attrs = FILE_ATTRIBUTE_HIDDEN if hidden else FILE_ATTRIBUTE_NORMAL
            ctypes.windll.kernel32.SetFileAttributesW(str(path), attrs)
        except Exception:
            pass
    
    @property
    def is_encrypted(self) -> bool:
        """Check if encryption is enabled"""
        return self.key_file.exists()
    
    def generate_db_key(self) -> str:
        """Generate a random database encryption key"""
        # Generate 256-bit random key
        return secrets.token_hex(32)
    
    def derive_key_from_password(self, password: str) -> bytes:
        """Derive encryption key from user password using PBKDF2"""
        salt = self._get_or_create_salt()
        
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=self.KEY_LENGTH,
            salt=salt,
            iterations=self.ITERATIONS,
        )
        
        return base64.urlsafe_b64encode(kdf.derive(password.encode()))
    
    def _get_or_create_salt(self) -> bytes:
        """Get existing salt or create new one"""
        if self.salt_file.exists():
            return self.salt_file.read_bytes()
        else:
            salt = secrets.token_bytes(self.SALT_SIZE)
            self.salt_file.write_bytes(salt)
            self._set_windows_hidden(self.salt_file, True)
            return salt
    
    def enable_encryption(self, password: str) -> bool:
        """
        Enable encryption for the database.
        This will encrypt existing data and all future data.
        
        Args:
            password: User's password to derive encryption key
            
        Returns:
            True if encryption was enabled successfully
        """
        try:
            # Generate database key
            db_key = self.generate_db_key()
            
            # Encrypt and store the database key using user's password
            master_key = self.derive_key_from_password(password)
            fernet = Fernet(master_key)
            encrypted_db_key = fernet.encrypt(db_key.encode())
            
            # Save encrypted key
            if self.key_file.exists():
                self._set_windows_hidden(self.key_file, False)
            self.key_file.write_bytes(encrypted_db_key)
            self._set_windows_hidden(self.key_file, True)
            
            self._db_key = db_key
            self._is_encrypted = True
            
            return True
            
        except Exception as e:
            logger.warning("Failed to enable encryption for user_id=%s: %s", self.user_id, e)
            return False
    
    def unlock(self, password: str) -> bool:
        """
        Unlock encrypted database with password.
        
        Args:
            password: User's password
            
        Returns:
            True if unlocked successfully
        """
        if not self.is_encrypted:
            return True  # Not encrypted, nothing to unlock
        
        try:
            # Derive master key from password
            master_key = self.derive_key_from_password(password)
            fernet = Fernet(master_key)
            
            # Decrypt database key
            encrypted_db_key = self.key_file.read_bytes()
            db_key = fernet.decrypt(encrypted_db_key).decode()
            
            self._db_key = db_key
            self._is_encrypted = True
            
            return True
            
        except Exception as e:
            logger.info("Failed to unlock encrypted DB for user_id=%s: %s", self.user_id, e)
            return False
    
    def get_db_key(self) -> Optional[str]:
        """Get the database encryption key (must unlock first)"""
        return self._db_key
    
    def change_password(self, old_password: str, new_password: str) -> bool:
        """
        Change the encryption password.
        
        Args:
            old_password: Current password
            new_password: New password
            
        Returns:
            True if password was changed successfully
        """
        if not self.is_encrypted:
            return False
        
        # First unlock with old password
        if not self.unlock(old_password):
            return False
        
        try:
            # Regenerate salt for new password
            if self.salt_file.exists():
                self._set_windows_hidden(self.salt_file, False)
            self.salt_file.unlink(missing_ok=True)
            self._get_or_create_salt()  # Ensure new key derivation uses a fresh salt.

            # Re-encrypt database key with new password (derived from the new salt)
            new_master_key = self.derive_key_from_password(new_password)
            fernet = Fernet(new_master_key)
            encrypted_db_key = fernet.encrypt(self._db_key.encode())
            
            # Save new encrypted key
            if self.key_file.exists():
                self._set_windows_hidden(self.key_file, False)
            self.key_file.write_bytes(encrypted_db_key)
            self._set_windows_hidden(self.key_file, True)
            
            return True
            
        except Exception as e:
            logger.warning("Failed to change encryption password for user_id=%s: %s", self.user_id, e)
            return False
    
    def disable_encryption(self, password: str) -> bool:
        """
        Disable encryption (decrypt database).
        WARNING: This will remove encryption protection.
        
        Args:
            password: Current password to verify
            
        Returns:
            True if encryption was disabled
        """
        if not self.is_encrypted:
            return True
        
        # Verify password
        if not self.unlock(password):
            return False
        
        try:
            # Remove key files
            if self.key_file.exists():
                self._set_windows_hidden(self.key_file, False)
            if self.salt_file.exists():
                self._set_windows_hidden(self.salt_file, False)
            self.key_file.unlink(missing_ok=True)
            self.salt_file.unlink(missing_ok=True)
            
            self._db_key = None
            self._is_encrypted = False
            
            return True
            
        except Exception as e:
            logger.warning("Failed to disable encryption for user_id=%s: %s", self.user_id, e)
            return False


def get_encryption_manager(user_id: str) -> EncryptionManager:
    """Get encryption manager for a user"""
    return EncryptionManager(user_id)


# ============================================================================
# SQLCipher Database Wrapper
# ============================================================================

class EncryptedDatabase:
    """
    Wrapper for SQLCipher encrypted database.
    """
    
    SQLCIPHER_AVAILABLE = False
    
    def __init__(self, db_path: Path, encryption_key: Optional[str] = None):
        self.db_path = db_path
        self.encryption_key = encryption_key
        self._check_sqlcipher()
    
    def _check_sqlcipher(self):
        """Check if SQLCipher is available"""
        try:
            import sqlcipher3
            self.SQLCIPHER_AVAILABLE = True
        except ImportError:
            try:
                import pysqlcipher3.dbapi2
                self.SQLCIPHER_AVAILABLE = True
            except ImportError:
                self.SQLCIPHER_AVAILABLE = False
    
    def connect(self):
        """Get database connection (encrypted if key provided)"""
        if self.encryption_key and self.SQLCIPHER_AVAILABLE:
            return self._get_encrypted_connection()
        if self.encryption_key:
            raise LocalDatabaseEncryptionUnavailableError(
                "Encrypted local database requires SQLCipher support on this system"
            )
        return self._get_regular_connection()
    
    def _get_encrypted_connection(self):
        """Get SQLCipher encrypted connection"""
        try:
            import sqlcipher3 as sqlite3
        except ImportError:
            import pysqlcipher3.dbapi2 as sqlite3
        
        conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        
        # Set encryption key
        conn.execute(f"PRAGMA key = '{self.encryption_key}'")
        
        # Verify encryption is working
        try:
            conn.execute("SELECT 1")
        except Exception as e:
            conn.close()
            raise ValueError(f"Failed to open encrypted database: {e}")
        
        return conn
    
    def _get_regular_connection(self):
        """Get regular SQLite connection"""
        import sqlite3
        
        conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        conn.row_factory = sqlite3.Row
        
        return conn
    
    def encrypt_existing_database(self, encryption_key: str) -> bool:
        """
        Encrypt an existing unencrypted database.
        Creates a new encrypted copy and replaces the original.
        """
        if not self.SQLCIPHER_AVAILABLE:
            logger.warning("SQLCipher not available; install with `pip install sqlcipher3`.")
            return False
        
        try:
            import sqlcipher3 as sqlite3
        except ImportError:
            import pysqlcipher3.dbapi2 as sqlite3
        
        # Create encrypted copy
        encrypted_path = self.db_path.with_suffix('.encrypted.db')
        
        try:
            # Open original database
            import sqlite3 as std_sqlite3
            src_conn = std_sqlite3.connect(str(self.db_path))
            
            # Open new encrypted database
            dst_conn = sqlite3.connect(str(encrypted_path))
            dst_conn.execute(f"PRAGMA key = '{encryption_key}'")
            
            # Copy all data
            src_conn.backup(dst_conn)
            
            src_conn.close()
            dst_conn.close()
            
            # Replace original with encrypted
            backup_path = self.db_path.with_suffix('.unencrypted.bak')
            self.db_path.rename(backup_path)
            encrypted_path.rename(self.db_path)
            
            # Remove backup after successful encryption
            backup_path.unlink()
            
            self.encryption_key = encryption_key
            return True
            
        except Exception as e:
            logger.warning("Failed to encrypt database at %s: %s", self.db_path, e)
            # Cleanup
            if encrypted_path.exists():
                encrypted_path.unlink()
            return False
    
    def decrypt_database(self) -> bool:
        """
        Decrypt an encrypted database.
        Creates an unencrypted copy and replaces the original.
        """
        if not self.encryption_key:
            return True  # Already unencrypted
        if not self.SQLCIPHER_AVAILABLE:
            logger.warning("SQLCipher not available; cannot decrypt database at %s", self.db_path)
            return False
        
        try:
            import sqlcipher3 as sqlite3
        except ImportError:
            import pysqlcipher3.dbapi2 as sqlite3
        
        decrypted_path = self.db_path.with_suffix('.decrypted.db')
        
        try:
            # Open encrypted database
            src_conn = sqlite3.connect(str(self.db_path))
            src_conn.execute(f"PRAGMA key = '{self.encryption_key}'")
            
            # Export to plaintext SQLite
            src_conn.execute(f"ATTACH DATABASE '{decrypted_path}' AS plaintext KEY ''")
            src_conn.execute("SELECT sqlcipher_export('plaintext')")
            src_conn.execute("DETACH DATABASE plaintext")
            src_conn.close()
            
            # Replace original with decrypted
            backup_path = self.db_path.with_suffix('.encrypted.bak')
            self.db_path.rename(backup_path)
            decrypted_path.rename(self.db_path)
            
            # Remove backup
            backup_path.unlink()
            
            self.encryption_key = None
            return True
            
        except Exception as e:
            logger.warning("Failed to decrypt database at %s: %s", self.db_path, e)
            if decrypted_path.exists():
                decrypted_path.unlink()
            return False


def check_sqlcipher_available() -> dict:
    """Check SQLCipher availability and return status"""
    status = {
        "sqlcipher_available": False,
        "library": None,
        "message": ""
    }
    
    try:
        import sqlcipher3
        status["sqlcipher_available"] = True
        status["library"] = "sqlcipher3"
        status["message"] = "SQLCipher is available"
    except ImportError:
        try:
            import pysqlcipher3.dbapi2
            status["sqlcipher_available"] = True
            status["library"] = "pysqlcipher3"
            status["message"] = "SQLCipher is available (via pysqlcipher3)"
        except ImportError:
            status["message"] = "SQLCipher not available. Install with: pip install sqlcipher3-binary"
    
    return status
