import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.local_storage.local_db import LocalDatabase
from app.local_storage.local_models import LocalConversation, LocalMessage, LocalWorkspace


class LocalDatabaseSyncMergeTests(unittest.TestCase):
    def test_merge_import_does_not_double_count_messages(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source_db = LocalDatabase("user-1", storage_dir=Path(source_dir))
            target_db = LocalDatabase("user-1", storage_dir=Path(target_dir))

            conversation = LocalConversation(
                id="conv-1",
                user_id="user-1",
                title="Conversation",
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            source_db.save_conversation(conversation)
            source_db.save_message(
                LocalMessage(
                    id="msg-1",
                    conversation_id="conv-1",
                    role="user",
                    content="hello",
                    token_count=5,
                    created_at=datetime.now(),
                )
            )

            payload = source_db.export_all_data()
            target_db.import_from_data(payload, merge=False)
            target_db.import_from_data(payload, merge=True)

            restored = target_db.get_conversation("conv-1")
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual(restored.total_messages, 1)
            self.assertEqual(restored.total_tokens, 5)

    def test_merge_import_keeps_newer_local_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            older_time = datetime.now() - timedelta(days=1)
            newer_time = datetime.now()

            source_db = LocalDatabase("user-1", storage_dir=Path(source_dir))
            target_db = LocalDatabase("user-1", storage_dir=Path(target_dir))

            source_db.save_workspace(
                LocalWorkspace(
                    id="ws-1",
                    user_id="user-1",
                    name="Older workspace",
                    created_at=older_time,
                    updated_at=older_time,
                )
            )
            target_db.save_workspace(
                LocalWorkspace(
                    id="ws-1",
                    user_id="user-1",
                    name="Newer workspace",
                    created_at=older_time,
                    updated_at=newer_time,
                )
            )

            payload = source_db.export_all_data()
            target_db.import_from_data(payload, merge=True)

            restored = target_db.get_workspace("ws-1")
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual(restored.name, "Newer workspace")


if __name__ == "__main__":
    unittest.main()
