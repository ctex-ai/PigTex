import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.local_storage.local_db import LocalDatabase
from app.local_storage.local_models import LocalConversation, LocalMessage
from app.local_storage.request_scope import bind_request_local_scope, reset_request_local_scope


class LocalMachineScopeTests(unittest.TestCase):
    def test_machine_scope_merges_legacy_account_databases_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            legacy_a_dir = root / "legacy-a"
            legacy_b_dir = root / "legacy-b"

            legacy_a_db = LocalDatabase(
                "legacy-a",
                storage_dir=legacy_a_dir,
                normalize_user_scope=False,
            )
            legacy_b_db = LocalDatabase(
                "legacy-b",
                storage_dir=legacy_b_dir,
                normalize_user_scope=False,
            )

            legacy_a_db.save_conversation(
                LocalConversation(
                    id="conv-a",
                    user_id="legacy-a",
                    title="Account A",
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                )
            )
            legacy_a_db.save_message(
                LocalMessage(
                    id="msg-a",
                    conversation_id="conv-a",
                    role="user",
                    content="from account a",
                    token_count=3,
                    created_at=datetime.now(),
                )
            )

            legacy_b_db.save_conversation(
                LocalConversation(
                    id="conv-b",
                    user_id="legacy-b",
                    title="Account B",
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                )
            )
            legacy_b_db.save_message(
                LocalMessage(
                    id="msg-b",
                    conversation_id="conv-b",
                    role="user",
                    content="from account b",
                    token_count=3,
                    created_at=datetime.now(),
                )
            )

            tokens = bind_request_local_scope(
                "device-shared",
                ("legacy-a", "legacy-b"),
            )
            with (
                patch("app.local_storage.scope.get_storage_root", return_value=root),
                patch("app.local_storage.local_db.get_storage_root", return_value=root),
                patch("app.local_storage.local_db.resolve_local_owner_id", return_value="device-shared"),
            ):
                try:
                    machine_db = LocalDatabase("active-account")

                    conversations = machine_db.get_conversations(limit=10, include_archived=True)
                    self.assertEqual({item.id for item in conversations}, {"conv-a", "conv-b"})
                    self.assertEqual(machine_db.get_messages("conv-a")[0].content, "from account a")
                    self.assertEqual(machine_db.get_messages("conv-b")[0].content, "from account b")

                    marker_path = machine_db.storage_dir / ".legacy_account_storage_merged_v1.json"
                    self.assertTrue(marker_path.exists())

                    reopened_db = LocalDatabase("another-account")
                    reopened_conversations = reopened_db.get_conversations(limit=10, include_archived=True)
                    self.assertEqual({item.id for item in reopened_conversations}, {"conv-a", "conv-b"})
                finally:
                    reset_request_local_scope(tokens)


if __name__ == "__main__":
    unittest.main()
