import unittest

from fastapi import HTTPException

from app.models import User
from app.routes import v1_api


class ResolveUpstreamConfigFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = User(
            id="user-cred-1",
            email="cred@example.com",
            username="cred-user",
            name="Credential User",
            plan="free",
            is_active=True,
        )

    def test_resolve_upstream_config_requires_request_credentials(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            v1_api._resolve_upstream_config(
                current_user=self.user,
                db=None,
            )

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail.get("error"), "api_credentials_required")

    def test_resolve_upstream_config_uses_explicit_request_credentials(self) -> None:
        cfg = v1_api._resolve_upstream_config(
            current_user=self.user,
            db=None,
            api_key="AIza-request-key",
            base_url="https://generativelanguage.googleapis.com",
            api_provider="google",
        )

        self.assertEqual(cfg.source, "request")
        self.assertEqual(cfg.api_key, "AIza-request-key")
        self.assertEqual(cfg.base_url, "https://generativelanguage.googleapis.com")
        self.assertEqual(cfg.api_provider, "gemini")

    def test_resolve_upstream_config_raises_when_all_sources_are_missing(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            v1_api._resolve_upstream_config(
                current_user=self.user,
                db=None,
            )

        self.assertEqual(ctx.exception.status_code, 403)
        detail = ctx.exception.detail
        self.assertEqual(detail.get("error"), "api_credentials_required")


if __name__ == "__main__":
    unittest.main()
