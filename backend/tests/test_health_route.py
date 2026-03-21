import asyncio
import json
import unittest
from unittest.mock import patch

from fastapi.responses import JSONResponse

from app import main as main_module


class HealthRouteTests(unittest.TestCase):
    def test_health_reports_ok_when_db_and_oauth_state_are_healthy(self) -> None:
        with patch.object(main_module, "check_database_connection"), patch.object(
            main_module,
            "get_oauth_state_backend_status",
            return_value={
                "configured_backend": "auto",
                "resolved_backend": "memory",
                "active_backend": "memory",
                "healthy": True,
                "required": False,
                "detail": "ok",
            },
        ):
            response = asyncio.run(main_module.health())

        self.assertEqual(
            response,
            {
                "status": "ok",
                "db": "ok",
                "oauth_state": {
                    "configured_backend": "auto",
                    "resolved_backend": "memory",
                    "active_backend": "memory",
                    "healthy": True,
                    "required": False,
                    "detail": "ok",
                },
            },
        )

    def test_health_returns_503_when_required_oauth_state_backend_is_unhealthy(self) -> None:
        with patch.object(main_module, "check_database_connection"), patch.object(
            main_module,
            "get_oauth_state_backend_status",
            return_value={
                "configured_backend": "redis",
                "resolved_backend": "redis",
                "active_backend": "unavailable",
                "healthy": False,
                "required": True,
                "detail": "redis down",
            },
        ):
            response = asyncio.run(main_module.health())

        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            json.loads(response.body.decode("utf-8")),
            {
                "status": "degraded",
                "db": "ok",
                "oauth_state": {
                    "configured_backend": "redis",
                    "resolved_backend": "redis",
                    "active_backend": "unavailable",
                    "healthy": False,
                    "required": True,
                    "detail": "redis down",
                },
            },
        )


if __name__ == "__main__":
    unittest.main()
