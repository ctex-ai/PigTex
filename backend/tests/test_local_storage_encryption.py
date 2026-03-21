import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.local_storage.encryption import (
    EncryptedDatabase,
    LocalDatabaseEncryptionUnavailableError,
    LocalDatabaseLockedError,
)
from app.local_storage.local_db import LocalDatabase


class LocalStorageEncryptionTests(unittest.TestCase):
    def test_encrypted_database_connect_rejects_plaintext_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "local.db"
            db_path.touch()
            encrypted_db = EncryptedDatabase(db_path, encryption_key="secret-key")
            encrypted_db.SQLCIPHER_AVAILABLE = False

            with self.assertRaises(LocalDatabaseEncryptionUnavailableError):
                encrypted_db.connect()

    def test_locked_encrypted_database_skips_auto_init_and_blocks_access(self) -> None:
        def _fake_setup(self, password=None):
            self._sqlcipher_available = True
            self._encryption_manager = SimpleNamespace(is_encrypted=True)
            self._encryption_enabled = True
            self._encryption_key = None

        with tempfile.TemporaryDirectory() as temp_dir:
            storage_dir = Path(temp_dir)
            with patch.object(LocalDatabase, "_setup_encryption", _fake_setup):
                local_db = LocalDatabase("user-1", storage_dir=storage_dir)

            self.assertFalse((storage_dir / "local.db").exists())
            self.assertEqual(
                local_db.get_encryption_status(),
                {
                    "encrypted": True,
                    "sqlcipher_available": True,
                    "locked": True,
                    "unlocked": False,
                },
            )

            with self.assertRaises(LocalDatabaseLockedError):
                with local_db._get_connection():
                    self.fail("Locked encrypted database should not open a connection")


if __name__ == "__main__":
    unittest.main()
