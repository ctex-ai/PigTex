import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import AdminAuditEvent, User
from app.prompting.skill_foundry import SkillFoundry
from app.routes.auth_utils import get_current_user
from app.routes.skill_foundry import router


class SkillFoundryAdminRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)
        self.foundry = SkillFoundry(data_root=self.root)
        incoming = self.root / "incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        (incoming / "facebook_hooks.md").write_text(
            """
## Facebook Hook Skill
Use this when the user needs Facebook ads hooks with a hard promise and clear CTA.
Always surface the pain point first, sharpen the promise, and finish with a direct-response angle.

Output contract:
- Return 3 hooks
- Keep each hook under 12 words
- End with a concrete CTA angle

## Safety
Do not promise impossible medical outcomes.
Avoid vague filler and weak generic hooks.
""",
            encoding="utf-8",
        )

        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
        Base.metadata.create_all(engine, tables=[User.__table__, AdminAuditEvent.__table__])
        self.db = SessionLocal()
        self.user = User(
            id="user-1",
            email="user@example.com",
            username="user",
            name="User",
            plan="pro",
            is_active=True,
            role="user",
        )
        self.admin = User(
            id="admin-1",
            email="admin@example.com",
            username="admin",
            name="Admin",
            plan="pro",
            is_active=True,
            role="admin",
        )
        self.db.add_all([self.user, self.admin])
        self.db.commit()

        app = FastAPI()
        app.include_router(router, prefix="/api")

        def override_get_db():
            try:
                yield self.db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        self.app = app

    def tearDown(self) -> None:
        self.db.close()
        self._temp_dir.cleanup()

    def test_non_admin_cannot_access_overview(self) -> None:
        self.app.dependency_overrides[get_current_user] = lambda: self.user
        client = TestClient(self.app)

        with patch("app.routes.skill_foundry.build_foundry_from_env", return_value=self.foundry):
            response = client.get("/api/skill-foundry/overview")

        self.assertEqual(response.status_code, 403)

    def test_admin_can_compile_publish_and_audit(self) -> None:
        self.app.dependency_overrides[get_current_user] = lambda: self.admin
        client = TestClient(self.app)

        with patch("app.routes.skill_foundry.build_foundry_from_env", return_value=self.foundry), patch(
            "app.routes.skill_foundry._build_route_foundry", return_value=self.foundry
        ):
            compile_response = client.post(
                "/api/skill-foundry/compile",
                json={"input_path": None, "dry_run": False, "max_files": 20},
            )
            self.assertEqual(compile_response.status_code, 200)
            self.assertTrue(self.foundry.load_draft_registry()["active_skills"])
            self.assertEqual(self.foundry.load_registry()["active_skills"], [])

            publish_response = client.post(
                "/api/skill-foundry/publish",
                json={"note": "Publish tested registry"},
            )
            self.assertEqual(publish_response.status_code, 200)
            self.assertTrue(self.foundry.load_registry()["active_skills"])

            audit_response = client.get("/api/skill-foundry/audit?limit=10")
            self.assertEqual(audit_response.status_code, 200)
            items = audit_response.json()["items"]
            self.assertGreaterEqual(len(items), 2)
            actions = {item["action"] for item in items}
            self.assertIn("compile", actions)
            self.assertIn("publish", actions)


if __name__ == "__main__":
    unittest.main()
