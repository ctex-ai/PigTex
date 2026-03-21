import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.memory.prompt_injector import PromptInjector
from app.models import Skill, SystemPrompt


class PromptInjectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(
            cls.engine,
            tables=[SystemPrompt.__table__, Skill.__table__],
        )
        cls.SessionLocal = sessionmaker(bind=cls.engine)

    def setUp(self) -> None:
        PromptInjector._cache.clear()
        self.session = self.SessionLocal()
        self.session.query(Skill).delete()
        self.session.query(SystemPrompt).delete()
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def test_get_system_prompt_prefers_targeted_variant(self) -> None:
        self.session.add_all(
            [
                SystemPrompt(
                    name="default_assistant",
                    prompt_content="generic prompt",
                    is_active=True,
                    weight=10,
                ),
                SystemPrompt(
                    name="default_assistant:gpt-4o:pro",
                    prompt_content="premium gpt4o prompt",
                    target_models='["gpt-4o"]',
                    target_tiers='["pro"]',
                    is_active=True,
                    weight=100,
                ),
            ]
        )
        self.session.commit()

        injector = PromptInjector(self.session)

        result = injector.get_system_prompt(
            prompt_name="default_assistant",
            model="gpt-4o",
            user_tier="pro",
        )

        self.assertEqual(result, "premium gpt4o prompt")

    def test_malformed_skill_json_does_not_crash_matching_or_formatting(self) -> None:
        self.session.add_all(
            [
                Skill(
                    name="broken_skill",
                    instruction="Do not crash.",
                    trigger_keywords="not-json",
                    examples="{bad-json",
                    is_active=True,
                    priority=90,
                ),
                Skill(
                    name="healthy_skill",
                    instruction="Give a useful answer.",
                    trigger_keywords='["prompt", "natural"]',
                    examples='["Example 1"]',
                    is_active=True,
                    priority=80,
                ),
            ]
        )
        self.session.commit()

        injector = PromptInjector(self.session)
        skills = injector.get_skills_for_intent(keywords=["prompt"])
        rendered = injector.format_skills_for_prompt(skills)

        self.assertEqual(len(skills), 1)
        self.assertIn("Healthy Skill", rendered)
        self.assertIn("Example 1", rendered)

    def test_dynamic_guidance_includes_topic_expertise_and_natural_language_layer(self) -> None:
        injector = PromptInjector(self.session)

        guidance = injector._build_dynamic_file_instructions(
            user_message="Mình bị đau đầu 2 ngày rồi, nên xử lý thế nào?",
            detected_intent="advice",
            keywords=["đau", "xử", "lý"],
        )

        self.assertIn("Natural-language interpretation", guidance)
        self.assertIn("healthcare professional", guidance)

    def test_training_score_penalizes_heading_only_evidence(self) -> None:
        injector = PromptInjector(self.session)

        score = injector.compute_prompt_training_score(
            included_headings=[
                "PigTex Core Prompt Pack",
                "PigTex Quality & Safety",
                "PigTex Expert Prompt Intelligence",
                "PigTex Adaptive Guidance",
                "PigTex Skill Curriculum",
            ],
            total_chars=6200,
            model="gpt-4o",
        )

        self.assertLess(score["score"], 80.0)

    def test_detect_intent_prefers_debug_over_brief_style_hint(self) -> None:
        injector = PromptInjector(self.session)

        intent = injector.detect_intent(
            "Đọc lỗi stack trace này và tìm root cause rồi đề xuất cách fix ngắn gọn"
        )

        self.assertEqual(intent, "debug")

    def test_detect_intent_understands_creative_ads_prompt(self) -> None:
        injector = PromptInjector(self.session)

        intent = injector.detect_intent(
            "Viết cho tôi 3 hook Facebook Ads cho serum trị mụn, giọng tự nhiên, không lố"
        )

        self.assertEqual(intent, "creative")

    def test_file_pack_budget_keeps_competitive_skill_and_contract_slots(self) -> None:
        class StubFoundry:
            @staticmethod
            def resolve_matches(*, user_message: str, detected_intent: str | None = None, keywords=None):
                return [
                    {
                        "title": "Meta Ads Hook Champion",
                        "domain": "marketing.ads.facebook.ad_creative",
                        "instruction_core": "Front-load the hook, then make the pain or desire concrete.",
                        "output_contract": [
                            "Return exactly 3 hook options.",
                            "Keep each hook concise.",
                            "Do not add greeting or outro filler.",
                        ],
                        "score_total": 91.0,
                    }
                ]

            @staticmethod
            def format_runtime_skills(skills):
                return (
                    "### Meta Ads Hook Champion\n"
                    "- Role: Champion skill\n"
                    "- Domain: marketing.ads.facebook.ad_creative\n"
                    "- Apply: Front-load the hook and vary the angle.\n"
                    "- Output contract: Return exactly 3 hook options."
                )

            @staticmethod
            def format_runtime_output_contracts(skills):
                return (
                    "### Champion Contract: Meta Ads Hook Champion\n"
                    "- Return exactly 3 hook options.\n"
                    "- Keep each hook concise.\n"
                    "- Do not add greeting or outro filler."
                )

        injector = PromptInjector(self.session)
        injector._skill_foundry = StubFoundry()

        sections = injector._build_file_pack_sections(
            user_message="Viết cho tôi 3 hook Facebook Ads cho serum trị mụn, giọng tự nhiên, không lố",
            detected_intent="creative",
            keywords=["hook", "facebook", "ads", "mụn"],
            include_base_prompt=True,
            model="gpt-4o",
        )
        headings = [injector._extract_section_heading(section) for section in sections]

        self.assertIn("PigTex Competitive Skill Matches", headings)
        self.assertIn("PigTex Monetization Output Contract", headings)

    def test_build_injected_prompt_prioritizes_ads_skill_before_generic_tactics(self) -> None:
        class StubFoundry:
            @staticmethod
            def resolve_matches(*, user_message: str, detected_intent: str | None = None, keywords=None):
                return [
                    {
                        "title": "Meta Ads Hook Champion",
                        "domain": "marketing.ads.facebook.ad_creative",
                        "instruction_core": "Front-load the hook and avoid soft generic copy.",
                        "output_contract": [
                            "Return exactly 3 hook options.",
                            "Do not add greeting or outro filler.",
                        ],
                        "score_total": 91.0,
                    }
                ]

            @staticmethod
            def format_runtime_skills(skills):
                return (
                    "### Meta Ads Hook Champion\n"
                    "- Role: Champion skill\n"
                    "- Domain: marketing.ads.facebook.ad_creative\n"
                    "- Apply: Front-load the hook and vary the angle."
                )

            @staticmethod
            def format_runtime_output_contracts(skills):
                return (
                    "### Champion Contract: Meta Ads Hook Champion\n"
                    "- Return exactly 3 hook options.\n"
                    "- Do not add greeting or outro filler."
                )

        injector = PromptInjector(self.session)
        injector._skill_foundry = StubFoundry()

        prompt = injector.build_injected_prompt(
            user_message="Viết cho tôi 3 hook Facebook Ads cho serum trị mụn, giọng tự nhiên, không lố",
            model="gpt-4o",
            user_tier="pro",
            detected_intent="creative",
            keywords=["hook", "facebook", "ads", "mụn"],
        )

        self.assertIn("Meta Ads Hook Champion", prompt)
        self.assertIn("Return exactly 3 hook options.", prompt)
        self.assertLess(
            prompt.find("## PigTex Competitive Skill Matches"),
            prompt.find("## PigTex Expert Prompt Intelligence"),
        )


if __name__ == "__main__":
    unittest.main()
