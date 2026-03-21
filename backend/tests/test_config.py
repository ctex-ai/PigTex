import unittest

from app.config import Settings


class SettingsTests(unittest.TestCase):
    def test_cloud_backup_forces_migration_head_check(self) -> None:
        settings = Settings(cloud_backup_enabled=True)
        self.assertTrue(settings.should_require_db_migration_head)

    def test_flag_still_allows_manual_migration_head_check(self) -> None:
        settings = Settings(
            cloud_backup_enabled=False,
            require_db_migration_head_on_startup=True,
        )
        self.assertTrue(settings.should_require_db_migration_head)

    def test_production_rejects_short_jwt_secret(self) -> None:
        with self.assertRaisesRegex(ValueError, "JWT_SECRET_KEY"):
            Settings(
                app_env="production",
                jwt_secret_key="too-short",
            )

    def test_production_requires_authenticated_redis_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "REDIS_URL"):
            Settings(
                app_env="production",
                jwt_secret_key="a" * 32,
                rate_limit_backend="redis",
                redis_url="redis://redis:6379/0",
            )

    def test_production_oauth_requires_shared_redis_state(self) -> None:
        with self.assertRaisesRegex(ValueError, "OAuth login"):
            Settings(
                app_env="production",
                jwt_secret_key="a" * 32,
                google_client_id="google-client",
                google_client_secret="google-secret",
                oauth_state_backend="memory",
            )

    def test_production_explicit_redis_oauth_backend_requires_redis_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "REDIS_URL"):
            Settings(
                app_env="production",
                jwt_secret_key="a" * 32,
                oauth_state_backend="redis",
            )

    def test_production_sync_billing_requires_cloud_backup(self) -> None:
        with self.assertRaisesRegex(ValueError, "CLOUD_BACKUP_ENABLED"):
            Settings(
                app_env="production",
                jwt_secret_key="a" * 32,
                sync_billing_enabled=True,
                sync_billing_provider="payos",
                sync_payos_client_id="client-id",
                sync_payos_api_key="api-key",
                sync_payos_checksum_key="checksum-key",
                cloud_backup_enabled=False,
            )

    def test_production_cloud_backup_requires_spaces_credentials(self) -> None:
        with self.assertRaisesRegex(ValueError, "SPACES_ACCESS_KEY_ID"):
            Settings(
                app_env="production",
                jwt_secret_key="a" * 32,
                cloud_backup_enabled=True,
            )

    def test_default_cors_origins_allow_desktop_packaged_origin(self) -> None:
        settings = Settings()
        self.assertIn("null", settings.get_cors_origins())


if __name__ == "__main__":
    unittest.main()
