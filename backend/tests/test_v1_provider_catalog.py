import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import User
from app.routes import v1_api
from app.routes.auth_utils import get_current_user


class V1ProviderCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = User(
            id="user-provider-1",
            email="provider@example.com",
            username="provider-user",
            name="Provider User",
            plan="pro",
            is_active=True,
        )

        app = FastAPI()
        app.include_router(v1_api.router, prefix="/api")
        app.dependency_overrides[get_current_user] = lambda: self.user
        app.dependency_overrides[v1_api.get_db] = lambda: None
        app.dependency_overrides[v1_api.v1_rate_limit] = lambda: None

        self.client = TestClient(app)

    def test_provider_catalog_lists_five_public_providers(self) -> None:
        response = self.client.get("/api/v1/providers")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([item["id"] for item in body["data"]], [
            "texapi",
            "openai",
            "google",
            "anthropic",
            "alibaba",
        ])

        texapi = body["data"][0]
        self.assertEqual(texapi["kind"], "gateway")
        self.assertEqual(texapi["default_base_url"], "")
        self.assertFalse(texapi["managed_by_server"])
        self.assertTrue(texapi["supports_byok"])

        google = next(item for item in body["data"] if item["id"] == "google")
        self.assertEqual(google["request_api_provider"], "gemini")
        self.assertIn("gemini", google["aliases"])

        openai = next(item for item in body["data"] if item["id"] == "openai")
        self.assertFalse(openai["managed_by_server"])
        self.assertTrue(openai["supports_byok"])

    def test_provider_aliases_still_normalize_to_supported_internal_modes(self) -> None:
        self.assertEqual(v1_api._normalize_api_provider("google"), "gemini")
        self.assertEqual(v1_api._normalize_api_provider("texapi"), "openai")
        self.assertEqual(v1_api._normalize_api_provider("dashscope"), "alibaba")


if __name__ == "__main__":
    unittest.main()
