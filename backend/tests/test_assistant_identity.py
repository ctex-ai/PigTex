import unittest

from app.assistant_identity import (
    PIGTEX_IDENTITY_SYSTEM_PROMPT,
    apply_pigtex_identity_system_prompt,
)


class AssistantIdentityTests(unittest.TestCase):
    def test_prepends_identity_when_no_system_message_exists(self) -> None:
        messages = [{"role": "user", "content": "xin chao"}]

        updated = apply_pigtex_identity_system_prompt(messages)

        self.assertEqual(updated[0]["role"], "system")
        self.assertEqual(updated[0]["content"], PIGTEX_IDENTITY_SYSTEM_PROMPT)
        self.assertEqual(updated[1]["role"], "user")

    def test_identity_stays_first_when_system_message_exists(self) -> None:
        messages = [{"role": "system", "content": "Answer briefly."}]

        updated = apply_pigtex_identity_system_prompt(messages)

        self.assertTrue(updated[0]["content"].startswith(PIGTEX_IDENTITY_SYSTEM_PROMPT))
        self.assertIn("Answer briefly.", updated[0]["content"])

    def test_identity_prompt_is_not_duplicated(self) -> None:
        messages = [{"role": "system", "content": PIGTEX_IDENTITY_SYSTEM_PROMPT}]

        updated = apply_pigtex_identity_system_prompt(messages)

        self.assertEqual(updated[0]["content"], PIGTEX_IDENTITY_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
