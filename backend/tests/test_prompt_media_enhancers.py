import unittest

from app.routes.v1_api import (
    V1AudioSpeechRequest,
    V1VideoGenerationRequest,
    _build_alibaba_tts_instruction,
    _enhance_video_generation_prompt,
    _enhance_voice_prompt_input,
    _resolve_alibaba_tts_language_type,
    _resolve_alibaba_tts_model,
)


class PromptMediaEnhancerTests(unittest.TestCase):
    def test_voice_enhancer_applies_pronunciation_and_symbol_cleanup(self) -> None:
        request = V1AudioSpeechRequest(
            model="qwen3-tts-flash",
            input="chao mung den voi pigtex & uu dai 50%",
            language="vi",
            pronunciation_dictionary="PigTex: Pich Tex",
            brand_terms=["PigTex"],
            prompt_enhance=True,
            prompt_profile="world_class",
        )

        enhanced = _enhance_voice_prompt_input(request)

        self.assertIn("Pich Tex", enhanced)
        self.assertIn("và", enhanced)
        self.assertIn("phần trăm", enhanced)

    def test_alibaba_tts_keeps_requested_model_for_world_class_profile(self) -> None:
        request = V1AudioSpeechRequest(
            model="qwen3-tts-flash",
            input="Xin chao ban den voi PigTex",
            language="vi",
            prompt_enhance=True,
            prompt_profile="world_class",
        )

        resolved_model = _resolve_alibaba_tts_model(request)
        language_type = _resolve_alibaba_tts_language_type(request, request.input)
        instruction = _build_alibaba_tts_instruction(request, request.input)

        self.assertEqual(resolved_model, "qwen3-tts-flash")
        self.assertEqual(language_type, "Auto")
        self.assertTrue(instruction)

    def test_alibaba_tts_keeps_english_language_type_when_requested(self) -> None:
        request = V1AudioSpeechRequest(
            model="qwen3-tts-instruct-flash",
            input="Welcome to PigTex",
            language="english",
            prompt_enhance=True,
        )

        self.assertEqual(
            _resolve_alibaba_tts_language_type(request, request.input),
            "English",
        )

    def test_video_enhancer_keeps_normal_scene_request_neutral(self) -> None:
        request = V1VideoGenerationRequest(
            prompt="tao mot video phong canh chill chill o bo bien luc hoang hon",
            duration="10",
            style="cinematic",
            prompt_enhance=True,
            prompt_profile="world_class",
        )

        enhanced = _enhance_video_generation_prompt(request)

        self.assertIn(request.prompt, enhanced)
        self.assertNotIn("Take action now", enhanced)
        self.assertNotIn("marketing video", enhanced.lower())
        self.assertIn("Avoid turning this into an advertisement", enhanced)
        self.assertIn("avoid visible text", enhanced.lower())

    def test_video_enhancer_switches_to_promotional_mode_for_ad_brief(self) -> None:
        request = V1VideoGenerationRequest(
            prompt="tao video quang cao cho quan ca phe PigTex",
            duration="10",
            prompt_enhance=True,
            prompt_profile="world_class",
            cta="Ghe quan ngay hom nay",
        )

        enhanced = _enhance_video_generation_prompt(request)

        self.assertRegex(enhanced.lower(), r"(marketing|promotional) video")
        self.assertRegex(enhanced.lower(), r"(cta in final segment|closing action)")


if __name__ == "__main__":
    unittest.main()
