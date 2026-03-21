import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.memory.memory_coordinator import MemoryCoordinator, WorkingMemory


class MemoryCoordinatorBuildContextTests(unittest.TestCase):
    def test_build_context_skips_sync_knowledge_fallback_after_preload_timeout(self) -> None:
        coordinator = MemoryCoordinator.__new__(MemoryCoordinator)
        coordinator.user_id = "user-1"
        coordinator.local = SimpleNamespace()
        coordinator.prompt_injector = SimpleNamespace(
            detect_intent=lambda _message: "analysis",
            extract_keywords=lambda _message: ["knowledge"],
            build_injected_prompt=lambda **_kwargs: "Injected prompt",
        )
        coordinator.working = WorkingMemory()
        coordinator._context_maintenance_enabled = False
        coordinator._stream_context_preload_timeout_ms = 1
        coordinator._context_preload_timeout_ms = 1
        coordinator._profile_store = SimpleNamespace(format_for_context=lambda: "")
        coordinator._context_store = SimpleNamespace(
            format_for_context=lambda workspace_id=None, conversation_id=None: ""
        )
        coordinator.get_rules_context = lambda _workspace_id=None: ""

        def slow_search(*_args, **_kwargs):
            time.sleep(0.05)
            return []

        coordinator.search_knowledge = MagicMock(side_effect=slow_search)

        started_at = time.perf_counter()
        context = asyncio.run(
            coordinator.build_context(
                user_message="find the relevant note",
                model="gpt-4o-mini",
                include_knowledge=True,
                include_facts=False,
                include_history=False,
                latency_mode="balanced",
            )
        )
        elapsed_ms = (time.perf_counter() - started_at) * 1000

        self.assertLess(elapsed_ms, 200.0)
        self.assertEqual(coordinator.search_knowledge.call_count, 1)
        self.assertEqual(context.knowledge_context, "")
        self.assertEqual(context.system_prompt, "Injected prompt")


if __name__ == "__main__":
    unittest.main()
