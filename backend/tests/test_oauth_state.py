import unittest
from unittest.mock import patch

from app import oauth_state as oauth_state_module


class OAuthStateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_store = oauth_state_module._store
        self.original_values = {
            "app_env": oauth_state_module.settings.app_env,
            "oauth_state_backend": oauth_state_module.settings.oauth_state_backend,
            "redis_url": oauth_state_module.settings.redis_url,
            "google_client_id": oauth_state_module.settings.google_client_id,
            "google_client_secret": oauth_state_module.settings.google_client_secret,
            "github_client_id": oauth_state_module.settings.github_client_id,
            "github_client_secret": oauth_state_module.settings.github_client_secret,
        }

    def tearDown(self) -> None:
        oauth_state_module._store = self.original_store
        for key, value in self.original_values.items():
            setattr(oauth_state_module.settings, key, value)

    def test_production_redis_oauth_state_fails_closed_when_redis_is_unavailable(self) -> None:
        oauth_state_module.settings.app_env = "production"
        oauth_state_module.settings.oauth_state_backend = "redis"
        oauth_state_module.settings.redis_url = "redis://:secret@127.0.0.1:6399/0"
        oauth_state_module.settings.google_client_id = "google-client"
        oauth_state_module.settings.google_client_secret = "google-secret"
        oauth_state_module.settings.github_client_id = ""
        oauth_state_module.settings.github_client_secret = ""

        with patch.object(
            oauth_state_module,
            "RedisOAuthStateStore",
            side_effect=RuntimeError("redis down"),
        ):
            oauth_state_module._store = oauth_state_module._build_store()

        self.assertIsInstance(
            oauth_state_module._store,
            oauth_state_module.UnavailableOAuthStateStore,
        )

        with self.assertRaises(oauth_state_module.OAuthStateStoreUnavailableError):
            oauth_state_module.set_oauth_state("state-1", {"status": "pending"}, 60)

    def test_development_auto_backend_can_fallback_to_memory(self) -> None:
        oauth_state_module.settings.app_env = "development"
        oauth_state_module.settings.oauth_state_backend = "auto"
        oauth_state_module.settings.redis_url = "redis://:secret@127.0.0.1:6399/0"
        oauth_state_module.settings.google_client_id = ""
        oauth_state_module.settings.google_client_secret = ""
        oauth_state_module.settings.github_client_id = ""
        oauth_state_module.settings.github_client_secret = ""

        with patch.object(
            oauth_state_module,
            "RedisOAuthStateStore",
            side_effect=RuntimeError("redis down"),
        ):
            oauth_state_module._store = oauth_state_module._build_store()

        self.assertIsInstance(
            oauth_state_module._store,
            oauth_state_module.InMemoryOAuthStateStore,
        )
        oauth_state_module.set_oauth_state("state-1", {"status": "pending"}, 60)
        self.assertEqual(
            oauth_state_module.get_oauth_state("state-1"),
            {"status": "pending"},
        )


if __name__ == "__main__":
    unittest.main()
