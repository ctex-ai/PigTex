import base64
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.local_storage.request_scope import (
    LOCAL_DEVICE_SCOPE_HEADER,
    LOCAL_LEGACY_ACCOUNTS_HEADER,
    bind_request_local_scope,
    parse_legacy_account_ids_header,
    reset_request_local_scope,
)
from app.models import User
from app.routes import images as images_module
from app.routes.auth_utils import get_current_user


PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/w8AAgMBgN4L4N8AAAAASUVORK5CYII="
)


class ImageRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = User(
            id="user-1",
            email="owner@example.com",
            username="owner",
            name="Owner",
            plan="pro",
            is_active=True,
        )
        self.other_user = User(
            id="user-2",
            email="other@example.com",
            username="other",
            name="Other",
            plan="pro",
            is_active=True,
        )
        self.temp_dir = tempfile.TemporaryDirectory()

        app = FastAPI()

        @app.middleware("http")
        async def local_scope_middleware(request, call_next):
            tokens = bind_request_local_scope(
                request.headers.get(LOCAL_DEVICE_SCOPE_HEADER),
                parse_legacy_account_ids_header(request.headers.get(LOCAL_LEGACY_ACCOUNTS_HEADER)),
            )
            try:
                return await call_next(request)
            finally:
                reset_request_local_scope(tokens)

        app.include_router(images_module.router, prefix="/api")
        app.dependency_overrides[get_current_user] = lambda: self.user

        self.app = app
        self.client = TestClient(app)
        self.upload_dir_patch = patch.object(images_module, "UPLOAD_DIR", self.temp_dir.name)
        self.upload_dir_patch.start()
        self.device_headers = {
            LOCAL_DEVICE_SCOPE_HEADER: "device-test-1",
            LOCAL_LEGACY_ACCOUNTS_HEADER: f"{self.user.id},{self.other_user.id}",
        }

    def tearDown(self) -> None:
        self.upload_dir_patch.stop()
        self.temp_dir.cleanup()

    def test_upload_rejects_svg_files(self) -> None:
        response = self.client.post(
            "/api/images/upload",
            files={
                "files": (
                    "vector.svg",
                    b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
                    "image/svg+xml",
                )
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("unsupported", response.json()["detail"].lower())

    def test_private_image_serve_is_shared_for_accounts_on_same_machine(self) -> None:
        filename = images_module.save_image_to_user_disk(
            "img-1",
            base64.b64decode(PNG_1X1_BASE64),
            "png",
            self.user.id,
        )
        serve_path = images_module.build_image_serve_url(self.user.id, filename)

        owner_response = self.client.get(serve_path, headers=self.device_headers)
        self.assertEqual(owner_response.status_code, 200)
        self.assertEqual(owner_response.headers["cache-control"], "private, no-store, max-age=0")

        self.app.dependency_overrides[get_current_user] = lambda: self.other_user
        other_response = self.client.get(serve_path, headers=self.device_headers)
        self.assertEqual(other_response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
