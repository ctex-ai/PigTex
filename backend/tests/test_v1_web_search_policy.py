import unittest

from app.routes.v1_api import (
    V1ChatCompletionRequest,
    _derive_web_search_policy,
    _is_internal_tool_turn_text,
)


class V1WebSearchPolicyTests(unittest.TestCase):
    def test_internal_tool_turn_text_is_detected(self) -> None:
        self.assertTrue(_is_internal_tool_turn_text("[PIGTEX_TOOL_RESULT]\nhello"))
        self.assertTrue(_is_internal_tool_turn_text("   [PIGTEX_TOOL_RESULT]\nhello"))
        self.assertFalse(_is_internal_tool_turn_text("list folder and summarize it"))

    def test_internal_tool_turn_disables_recommended_search(self) -> None:
        request = V1ChatCompletionRequest(
            model="qwen3.5-flash",
            messages=[{"role": "user", "content": "placeholder"}],
            mode="fast",
        )

        policy = _derive_web_search_policy(
            request,
            "[PIGTEX_TOOL_RESULT]\nAction errors:\n- list_directory(bau_cu): Folder not found",
        )

        self.assertFalse(policy["recommended_search"])
        self.assertEqual(policy["resolved_mode"], "auto")
        self.assertEqual(policy["reason_label"], "internal_tool_turn")
        self.assertFalse(policy["deep_read"])
        self.assertFalse(policy["deep_verify"])

    def test_price_query_enables_search_with_deep_read(self) -> None:
        request = V1ChatCompletionRequest(
            model="qwen3.5-flash",
            messages=[{"role": "user", "content": "placeholder"}],
            mode="fast",
        )

        policy = _derive_web_search_policy(
            request,
            "Giá vàng hôm nay bao nhiêu?",
        )

        self.assertTrue(policy["recommended_search"])
        self.assertEqual(policy["resolved_mode"], "fast")
        self.assertTrue(policy["deep_read"])
        self.assertGreaterEqual(policy["max_results"], 6)
        self.assertTrue(policy["price_intent"])
        self.assertIn("price", policy["reason_label"])


if __name__ == "__main__":
    unittest.main()
