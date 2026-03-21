"""
Prompt Injector Service - "Bơm Ngầm" system.
Injects system prompts and skills into AI requests.
Users never see this - it's the secret sauce of PigTex.
"""

import json
import re
import os
import logging
from typing import List, Optional, Dict, Any, Tuple
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

from ..models import SystemPrompt, Skill, PromptTemplate
from ..prompting import SkillFoundry, build_foundry_from_env
from ..prompting.packs import PromptPackStore

logger = logging.getLogger(__name__)


class PromptCache:
    """
    In-memory cache for prompts to avoid DB hits on every request.
    Refreshes every 5 minutes.
    """
    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._last_refresh: datetime = datetime.min
        self._ttl = timedelta(minutes=5)
    
    def is_stale(self) -> bool:
        return datetime.now() - self._last_refresh > self._ttl
    
    def set(self, key: str, value: Any):
        self._cache[key] = value
        self._last_refresh = datetime.now()
    
    def get(self, key: str) -> Optional[Any]:
        if self.is_stale():
            return None
        return self._cache.get(key)
    
    def clear(self):
        self._cache.clear()
        self._last_refresh = datetime.min


class FilePromptPack:
    """
    File-based prompt/rule pack loader.
    Loads repository-level JSON data from `data/*` and caches it briefly.
    """

    @classmethod
    def load(cls, force_reload: bool = False) -> Dict[str, Any]:
        return {
            "system_prompts": PromptPackStore.load_json_dir("system_prompts", force_reload=force_reload),
            "query_rewrite": PromptPackStore.load_json_file(
                "enhancement_rules",
                "query_rewrite.json",
                force_reload=force_reload,
            ),
            "context_enrichment": PromptPackStore.load_json_file(
                "enhancement_rules",
                "context_enrichment.json",
                force_reload=force_reload,
            ),
            "response_quality": PromptPackStore.load_json_file(
                "enhancement_rules",
                "response_quality.json",
                force_reload=force_reload,
            ),
            "expert_prompt_intelligence": PromptPackStore.load_json_file(
                "enhancement_rules",
                "expert_prompt_intelligence.json",
                force_reload=force_reload,
            ),
            "weak_model_booster": PromptPackStore.load_json_file(
                "enhancement_rules",
                "weak_model_booster.json",
                force_reload=force_reload,
            ),
            "skill_curriculum": PromptPackStore.load_json_file(
                "enhancement_rules",
                "skill_curriculum.json",
                force_reload=force_reload,
            ),
            "skill_prompt_training_config": PromptPackStore.load_json_file(
                "enhancement_rules",
                "skill_prompt_training_config.json",
                force_reload=force_reload,
            ),
            "output_filters": PromptPackStore.load_json_file(
                "quality_filters",
                "output_filters.json",
                force_reload=force_reload,
            ),
            "hallucination_guard": PromptPackStore.load_json_file(
                "quality_filters",
                "hallucination_guard.json",
                force_reload=force_reload,
            ),
            "content_policy": PromptPackStore.load_json_file(
                "safety_guardrails",
                "content_policy.json",
                force_reload=force_reload,
            ),
            "prompt_injection_defense": PromptPackStore.load_json_file(
                "safety_guardrails",
                "prompt_injection_defense.json",
                force_reload=force_reload,
            ),
            "topic_handlers": PromptPackStore.load_json_file(
                "context_injection",
                "topic_handlers.json",
                force_reload=force_reload,
            ),
            "emotional_responses": PromptPackStore.load_json_file(
                "context_injection",
                "emotional_responses.json",
                force_reload=force_reload,
            ),
        }


class PromptInjector:
    """
    Main service for injecting prompts and skills.
    This is the "bơm ngầm" - hidden AI enhancement layer.
    """
    
    _cache = PromptCache()
    TOPIC_PRIORITY_SCORE = {
        "critical": 4,
        "high": 3,
        "medium": 2,
        "low": 1,
    }
    MAX_TOPIC_INSTRUCTIONS = 2
    MAX_DYNAMIC_QUERY_RULES = 3
    FILE_PACK_CHAR_BUDGET = 6500
    # Increase char budget for weak models (more prompt engineering needed)
    FILE_PACK_CHAR_BUDGET_WEAK = 8500
    MAX_EXPERT_GLOBAL_RULES = 4
    MAX_EXPERT_INTENT_RULES = 3
    MAX_EXPERT_CONDITIONAL_MODULES = 4
    MAX_EXPERT_MODULE_RULES = 3
    MAX_CURRICULUM_GLOBAL_RULES = 4
    MAX_CURRICULUM_INTENT_RULES = 3
    MAX_CURRICULUM_KEYWORD_MODULES = 2
    MAX_CURRICULUM_KEYWORD_RULES = 2
    MAX_CURRICULUM_OUTPUT_ITEMS = 5
    CORE_SECTION_MAX_CHARS = 3100
    QUALITY_SECTION_MAX_CHARS = 1500
    EXPERT_SECTION_MAX_CHARS = 1100
    ADAPTIVE_SECTION_MAX_CHARS = 700
    CURRICULUM_SECTION_MAX_CHARS = 1300
    COMPETITIVE_SECTION_MAX_CHARS = 1200
    MONETIZATION_CONTRACT_SECTION_MAX_CHARS = 700
    # Weak model booster section budget
    WEAK_MODEL_BOOST_SECTION_MAX_CHARS = 2200
    PROMPT_SLOT_ORDER = (
        "core",
        "quality",
        "competitive",
        "contract",
        "adaptive",
        "expert",
        "curriculum",
        "weak_boost",
    )
    PROMPT_SLOT_CAPS_DEFAULT = {
        "core": 2000,
        "quality": 950,
        "competitive": 950,
        "contract": 700,
        "adaptive": 500,
        "expert": 650,
        "curriculum": 550,
        "weak_boost": 0,
    }
    PROMPT_SLOT_CAPS_WEAK = {
        "core": 2400,
        "quality": 1200,
        "competitive": 1100,
        "contract": 850,
        "adaptive": 650,
        "expert": 850,
        "curriculum": 700,
        "weak_boost": 700,
    }
    PROMPT_LOG_PREVIEW_CHARS = 320
    DEFAULT_TRAINING_TARGET_SCORE = 88.0
    DEFAULT_TRAINING_WEIGHTS = {
        "core_prompt": 15.0,
        "quality_safety": 18.0,
        "expert_tactics": 16.0,
        "adaptive_guidance": 12.0,
        "skill_curriculum": 16.0,
        "weak_model_boost": 13.0,
        "budget_efficiency": 10.0,
    }
    DEFAULT_REQUIRED_SECTIONS = [
        "PigTex Core Prompt Pack",
        "PigTex Quality & Safety",
        "PigTex Expert Prompt Intelligence",
        "PigTex Adaptive Guidance",
        "PigTex Skill Curriculum",
    ]
    
    # Model tier classification — cached per-process
    MODEL_TIER_CACHE: Dict[str, str] = {}
    MODEL_TIER_WEAK = "weak"
    MODEL_TIER_MEDIUM = "medium"
    MODEL_TIER_STRONG = "strong"

    def __init__(self, db: Session):
        self.db = db
        self._weak_model_booster_cache: Optional[Dict[str, Any]] = None
        self._skill_foundry: Optional[SkillFoundry] = None

    @staticmethod
    def _safe_json_list(raw_value: Any, *, field_name: str = "") -> List[Any]:
        if raw_value is None:
            return []
        if isinstance(raw_value, list):
            return raw_value
        if isinstance(raw_value, tuple):
            return list(raw_value)
        if not isinstance(raw_value, str):
            return []

        text = raw_value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            if field_name:
                logger.warning("Invalid JSON list in %s", field_name)
            return []
        if isinstance(parsed, list):
            return parsed
        if field_name:
            logger.warning("Expected JSON list in %s, got %s", field_name, type(parsed).__name__)
        return []

    def _normalize_target_values(self, raw_value: Any, *, field_name: str = "") -> set[str]:
        values = self._safe_json_list(raw_value, field_name=field_name)
        normalized: set[str] = set()
        for value in values:
            item = str(value).strip().lower()
            if item:
                normalized.add(item)
        return normalized

    def _model_target_aliases(self, model: Optional[str]) -> set[str]:
        normalized = (model or "").strip().lower()
        aliases: set[str] = set()
        if normalized:
            aliases.add(normalized)
            aliases.add(normalized.split("/")[-1])
            aliases.add(normalized.split(":")[0])
            aliases.add(normalized.split("@")[0])
        aliases.add(self.classify_model_tier(model))
        aliases.update({"all", "*", "default"})
        return {alias for alias in aliases if alias}

    @staticmethod
    def _tier_target_aliases(user_tier: str) -> set[str]:
        normalized = (user_tier or "free").strip().lower() or "free"
        aliases = {normalized, "all", "*", "default"}
        if normalized in {"pro", "unlimited", "enterprise"}:
            aliases.add("paid")
        return aliases

    @staticmethod
    def _prompt_name_matches(candidate_name: str, prompt_name: str) -> bool:
        normalized_candidate = (candidate_name or "").strip().lower()
        normalized_prompt_name = (prompt_name or "").strip().lower()
        if not normalized_candidate or not normalized_prompt_name:
            return False
        if normalized_candidate == normalized_prompt_name:
            return True
        separators = (":", "@", "__", ".", "-")
        return any(
            normalized_candidate.startswith(f"{normalized_prompt_name}{separator}")
            for separator in separators
        )

    def _score_system_prompt_candidate(
        self,
        prompt: SystemPrompt,
        *,
        prompt_name: str,
        model: Optional[str],
        user_tier: str,
    ) -> Optional[int]:
        if not self._prompt_name_matches(prompt.name, prompt_name):
            return None

        score = 0
        normalized_name = (prompt.name or "").strip().lower()
        normalized_prompt_name = (prompt_name or "").strip().lower()
        if normalized_name == normalized_prompt_name:
            score += 100
        else:
            score += 70

        model_targets = self._normalize_target_values(
            prompt.target_models,
            field_name=f"system_prompts[{prompt.name}].target_models",
        )
        if model_targets:
            aliases = self._model_target_aliases(model)
            if not aliases.intersection(model_targets):
                return None
            if model:
                model_lower = model.strip().lower()
                if model_lower in model_targets or model_lower.split("/")[-1] in model_targets:
                    score += 30
                else:
                    score += 15
        else:
            score += 5

        tier_targets = self._normalize_target_values(
            prompt.target_tiers,
            field_name=f"system_prompts[{prompt.name}].target_tiers",
        )
        if tier_targets:
            aliases = self._tier_target_aliases(user_tier)
            if not aliases.intersection(tier_targets):
                return None
            if (user_tier or "free").strip().lower() in tier_targets:
                score += 20
            else:
                score += 10
        else:
            score += 5

        try:
            score += max(0, int(prompt.weight or 0) // 10)
        except (TypeError, ValueError):
            score += 0
        return score
    
    # =========================================================================
    # System Prompts
    # =========================================================================
    
    def get_system_prompt(
        self, 
        prompt_name: str = "default_assistant",
        model: Optional[str] = None,
        user_tier: str = "free"
    ) -> Optional[str]:
        """
        Get system prompt by name, filtered by model and tier.
        Falls back to default if specific not found.
        """
        cache_key = f"system_prompt:{prompt_name}:{model}:{user_tier}"
        cached = self._cache.get(cache_key)
        if cached:
            return cached

        candidates = self.db.query(SystemPrompt).filter(SystemPrompt.is_active == True).all()

        def _pick(target_name: str) -> Optional[SystemPrompt]:
            best_prompt: Optional[SystemPrompt] = None
            best_score: Optional[int] = None
            for candidate in candidates:
                score = self._score_system_prompt_candidate(
                    candidate,
                    prompt_name=target_name,
                    model=model,
                    user_tier=user_tier,
                )
                if score is None:
                    continue
                if best_score is None or score > best_score:
                    best_prompt = candidate
                    best_score = score
            return best_prompt

        prompt = _pick(prompt_name)
        if not prompt and prompt_name != "default_assistant":
            prompt = _pick("default_assistant")

        if prompt and prompt.prompt_content:
            self._cache.set(cache_key, prompt.prompt_content)
            return prompt.prompt_content
        
        return None
    
    def get_all_system_prompts(self) -> List[SystemPrompt]:
        """Get all active system prompts (for admin)"""
        return self.db.query(SystemPrompt).filter(
            SystemPrompt.is_active == True
        ).all()
    
    # =========================================================================
    # Skills
    # =========================================================================
    
    def get_skills_for_intent(
        self, 
        intent: Optional[str] = None,
        keywords: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get relevant skills based on detected intent or keywords.
        Returns list of dicts to avoid DetachedInstanceError.
        """
        cache_key = f"skills:{intent}:{','.join(keywords or [])}"
        cached = self._cache.get(cache_key)
        if cached:
            return cached
        
        skills = []
        seen_ids = set()
        
        # Get skills by intent
        if intent:
            intent_skills = self.db.query(Skill).filter(
                Skill.is_active == True,
                Skill.trigger_intent == intent
            ).order_by(Skill.priority.desc()).all()
            for s in intent_skills:
                seen_ids.add(s.id)
                skills.append(s)
        
        # Get skills by keywords
        if keywords:
            all_skills = self.db.query(Skill).filter(
                Skill.is_active == True,
                Skill.trigger_keywords.isnot(None)
            ).all()
            
            for skill in all_skills:
                if skill.id in seen_ids:
                    continue
                if skill.trigger_keywords:
                    trigger_kws = self._safe_json_list(
                        skill.trigger_keywords,
                        field_name=f"skills[{skill.name}].trigger_keywords",
                    )
                    if any(kw.lower() in [k.lower() for k in keywords] for kw in trigger_kws):
                        skills.append(skill)
                        seen_ids.add(skill.id)
        
        # Sort by priority
        skills.sort(key=lambda s: s.priority or 0, reverse=True)
        
        # Convert to dicts to avoid DetachedInstanceError
        skill_dicts = [
            {
                "id": s.id,
                "name": s.name,
                "instruction": s.instruction,
                "examples": s.examples,
                "priority": s.priority,
            }
            for s in skills
        ]
        
        self._cache.set(cache_key, skill_dicts)
        return skill_dicts
    
    def format_skills_for_prompt(self, skills: List[Dict[str, Any]]) -> str:
        """Format skill dicts into a string to inject into system prompt"""
        if not skills:
            return ""
        
        sections = []
        for skill in skills:
            section = f"### {skill['name'].replace('_', ' ').title()}\n"
            section += skill["instruction"]
            
            if skill.get("examples"):
                examples = self._safe_json_list(
                    skill["examples"],
                    field_name=f"skills[{skill['name']}].examples",
                )
                if examples:
                    section += "\n\nExamples:\n"
                    for ex in examples[:2]:  # Max 2 examples
                        section += f"- {ex}\n"
            
            sections.append(section)
        
        return "\n\n".join(sections)
    
    # =========================================================================
    # Templates
    # =========================================================================
    
    def get_template(self, template_name: str) -> Optional[PromptTemplate]:
        """Get a prompt template by name"""
        return self.db.query(PromptTemplate).filter(
            PromptTemplate.is_active == True,
            PromptTemplate.name == template_name
        ).first()
    
    def render_template(
        self, 
        template: PromptTemplate,
        variables: Dict[str, str]
    ) -> str:
        """Render a template with variables"""
        content = template.template
        for key, value in variables.items():
            content = content.replace(f"{{{{{key}}}}}", value)
        return content

    # =========================================================================
    # File-based Prompt Pack (data/*)
    # =========================================================================

    def _is_file_pack_enabled(self) -> bool:
        raw = os.getenv("PIGTEX_FILE_PROMPT_PACK_ENABLED", "1").strip().lower()
        return raw in {"1", "true", "yes", "on"}

    def _is_prompt_injection_log_enabled(self) -> bool:
        raw = os.getenv("PIGTEX_PROMPT_INJECTION_LOG_ENABLED", "1").strip().lower()
        return raw in {"1", "true", "yes", "on"}

    def _is_prompt_injection_text_log_enabled(self) -> bool:
        raw = os.getenv("PIGTEX_PROMPT_INJECTION_LOG_TEXT", "0").strip().lower()
        return raw in {"1", "true", "yes", "on"}

    def _compact_text_for_log(self, text: str, max_chars: int = 0) -> str:
        compact = re.sub(r"\s+", " ", str(text or "")).strip()
        if not compact:
            return ""
        limit = max_chars if max_chars > 0 else self.PROMPT_LOG_PREVIEW_CHARS
        if len(compact) <= limit:
            return compact
        return compact[: max(0, limit - 3)].rstrip() + "..."

    @staticmethod
    def _extract_section_heading(section_text: str) -> str:
        first_line = str(section_text or "").splitlines()[0].strip()
        if first_line.startswith("## "):
            return first_line[3:].strip()
        return first_line

    def _load_prompt_pack(self) -> Dict[str, Any]:
        if not self._is_file_pack_enabled():
            return {}
        return FilePromptPack.load()

    def _get_skill_foundry(self) -> SkillFoundry:
        if self._skill_foundry is None:
            self._skill_foundry = build_foundry_from_env()
        return self._skill_foundry

    def _collect_file_system_prompts(self, include_base_prompt: bool) -> List[str]:
        pack = self._load_prompt_pack()
        prompts = pack.get("system_prompts", {})
        if not isinstance(prompts, dict):
            return []

        scored: List[Tuple[int, str, str]] = []
        for file_name, payload in prompts.items():
            if not isinstance(payload, dict):
                continue
            prompt_text = str(payload.get("prompt", "")).strip()
            if not prompt_text:
                continue

            prompt_id = str(payload.get("id", file_name)).lower()
            is_base = "base_chat" in prompt_id or file_name.startswith("base_chat")
            if is_base and not include_base_prompt:
                continue

            try:
                priority = int(payload.get("priority", 100))
            except (TypeError, ValueError):
                priority = 100

            scored.append((priority, file_name, prompt_text))

        scored.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in scored]

    def _build_static_quality_guardrails(self) -> str:
        pack = self._load_prompt_pack()
        sections: List[str] = []

        response_quality = pack.get("response_quality", {})
        if isinstance(response_quality, dict):
            quality_instructions = response_quality.get("quality_instructions", {})
            if isinstance(quality_instructions, dict):
                always_inject = str(quality_instructions.get("always_inject", "")).strip()
                if always_inject:
                    sections.append(always_inject)

        for key in ("hallucination_guard", "content_policy", "prompt_injection_defense"):
            payload = pack.get(key, {})
            if not isinstance(payload, dict):
                continue
            text = str(payload.get("system_injection", "")).strip()
            if text:
                sections.append(text)

        return "\n\n".join(sections)

    def _classify_message_type(self, message: str) -> str:
        text = (message or "").strip().lower()
        if not text:
            return "quick_question"

        if any(token in text for token in ("so sánh", "compare", "vs ", " versus ", "khác nhau")):
            return "comparison"
        if any(token in text for token in ("danh sách", "liệt kê", "list", "top ")):
            return "list_request"
        if any(token in text for token in ("làm sao", "cách ", "how to", "hướng dẫn", "tutorial", "step by step")):
            return "how_to"
        if any(token in text for token in ("story", "poem", "thơ", "viết truyện", "sáng tác")):
            return "creative"
        if any(token in text for token in ("buồn", "chán", "mệt", "lo", "stress", "sad", "anxious", "angry", "frustrated")):
            return "emotional_support"

        token_count = len(re.findall(r"\w+", text, flags=re.UNICODE))
        if token_count <= 8:
            return "quick_question"
        if token_count >= 36 or "\n" in message:
            return "detailed_question"
        return "explanation"

    def _estimate_complexity(self, message: str, detected_intent: Optional[str]) -> str:
        """Estimate query complexity: trivial | low | medium | high | expert."""
        text = (message or "").strip().lower()
        token_count = len(re.findall(r"\w+", text, flags=re.UNICODE))

        # Quick greetings / smalltalk
        if token_count <= 4 and not re.search(r"[\d\)\(]+\s*[\+\-\*/=]", text):
            trivial_markers = {
                "hi", "hello", "hey", "chào", "alo", "ok", "thanks",
                "cảm ơn", "bye", "tạm biệt",
            }
            if all(w in trivial_markers for w in re.findall(r"\w+", text, flags=re.UNICODE)):
                return "trivial"

        expert_intents = {"research", "debug", "code_review"}
        if detected_intent in expert_intents and token_count >= 20:
            return "expert"

        high_complexity_intents = {
            "analysis", "research", "planning", "code_generation", "code_review", "debug"
        }
        if detected_intent in high_complexity_intents:
            return "high"
        if re.search(r"[\d\)\(]+\s*[\+\-\*/=]", text):
            return "high"
        if token_count <= 8:
            return "low"
        if token_count >= 32:
            return "high"
        return "medium"

    def _build_query_rewrite_instructions(
        self,
        user_message: str,
        detected_intent: Optional[str]
    ) -> str:
        pack = self._load_prompt_pack()
        query_rewrite = pack.get("query_rewrite", {})
        if not isinstance(query_rewrite, dict):
            return ""

        rules = query_rewrite.get("rules", [])
        if not isinstance(rules, list):
            return ""

        rule_map: Dict[str, Dict[str, Any]] = {}
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            rule_id = str(rule.get("id", "")).strip()
            if rule_id:
                rule_map[rule_id] = rule

        text = (user_message or "").strip()
        message_type = self._classify_message_type(text)
        complexity = self._estimate_complexity(text, detected_intent)
        instructions: List[str] = []

        if len(text) < 15:
            clarify_rule = rule_map.get("clarify_vague", {})
            clarify_instruction = str(clarify_rule.get("instruction", "")).strip()
            if clarify_instruction:
                instructions.append(clarify_instruction)

        if complexity == "high":
            reasoning_rule = rule_map.get("add_reasoning", {})
            reasoning_instruction = str(reasoning_rule.get("instruction", "")).strip()
            if reasoning_instruction:
                instructions.append(reasoning_instruction)

        format_rule = rule_map.get("specify_format", {})
        format_templates = format_rule.get("instruction_templates", {})
        if isinstance(format_templates, dict):
            format_instruction = str(format_templates.get(message_type, "")).strip()
            if format_instruction:
                instructions.append(format_instruction)

        depth_rule = rule_map.get("depth_control", {})
        depth_templates = depth_rule.get("instruction_templates", {})
        if isinstance(depth_templates, dict):
            depth_instruction = str(depth_templates.get(message_type, "")).strip()
            if depth_instruction:
                instructions.append(depth_instruction)

        deduped: List[str] = []
        seen: set[str] = set()
        for instruction in instructions:
            key = instruction.lower()
            if not instruction or key in seen:
                continue
            seen.add(key)
            deduped.append(instruction)
            if len(deduped) >= self.MAX_DYNAMIC_QUERY_RULES:
                break

        if not deduped:
            return ""
        return "\n".join(f"- {item}" for item in deduped)

    @staticmethod
    def _normalize_instruction_list(raw_list: Any, max_items: int) -> List[str]:
        if not isinstance(raw_list, list):
            return []

        normalized: List[str] = []
        seen: set[str] = set()
        for value in raw_list:
            item = str(value).strip()
            key = item.lower()
            if not item or key in seen:
                continue
            seen.add(key)
            normalized.append(item)
            if len(normalized) >= max_items:
                break
        return normalized

    def _module_keyword_hit(
        self,
        trigger_keywords: Any,
        text_lower: str,
        extracted_keywords: set[str]
    ) -> bool:
        if not isinstance(trigger_keywords, list):
            return False

        for keyword in trigger_keywords:
            kw = str(keyword).strip().lower()
            if not kw:
                continue
            if kw in text_lower or kw in extracted_keywords:
                return True
        return False

    def _should_apply_expert_module(
        self,
        module_payload: Dict[str, Any],
        detected_intent: Optional[str],
        text_lower: str,
        word_count: int,
        extracted_keywords: set[str]
    ) -> bool:
        if not isinstance(module_payload, dict):
            return False

        if bool(module_payload.get("always")):
            return True

        message_patterns = module_payload.get("message_patterns", [])
        pattern_hit = False
        if isinstance(message_patterns, list) and message_patterns:
            for pattern in message_patterns:
                token = str(pattern).strip().lower()
                if token and token in text_lower:
                    pattern_hit = True
                    break

        trigger_keywords = module_payload.get("trigger_keywords")
        keyword_hit = self._module_keyword_hit(trigger_keywords, text_lower, extracted_keywords)
        has_explicit_trigger_rules = (
            (isinstance(message_patterns, list) and bool(message_patterns))
            or isinstance(trigger_keywords, list)
        )

        intents = module_payload.get("intents", [])
        if isinstance(intents, list) and intents:
            normalized_intents = {str(item).strip() for item in intents if item}
            if detected_intent not in normalized_intents and not has_explicit_trigger_rules:
                return False

        min_words = module_payload.get("min_words", 0)
        try:
            min_words = int(min_words)
        except (TypeError, ValueError):
            min_words = 0
        if word_count < max(0, min_words):
            return False

        # If module defines explicit trigger rules, require a hit.
        if has_explicit_trigger_rules:
            return pattern_hit or keyword_hit

        return True

    def _build_expert_prompt_intelligence(
        self,
        user_message: str,
        detected_intent: Optional[str],
        keywords: Optional[List[str]]
    ) -> str:
        pack = self._load_prompt_pack()
        expert_pack = pack.get("expert_prompt_intelligence", {})
        if not isinstance(expert_pack, dict):
            return ""

        text = str(user_message or "")
        text_lower = text.lower()
        word_count = len(re.findall(r"\w+", text_lower, flags=re.UNICODE))

        effective_intent = detected_intent or self.detect_intent(text)
        extracted_keywords = {str(k).strip().lower() for k in (keywords or []) if k}

        sections: List[str] = []

        global_rules = self._normalize_instruction_list(
            expert_pack.get("global_rules", []),
            self.MAX_EXPERT_GLOBAL_RULES
        )
        if global_rules:
            sections.append(
                "Global tactics:\n" + "\n".join(f"- {item}" for item in global_rules)
            )

        intent_modules = expert_pack.get("intent_modules", {})
        selected_intent_module = ""
        if isinstance(intent_modules, dict) and effective_intent:
            module_payload = intent_modules.get(effective_intent, {})
            if isinstance(module_payload, dict):
                module_name = str(module_payload.get("name", "")).strip() or effective_intent.replace("_", " ").title()
                instructions = self._normalize_instruction_list(
                    module_payload.get("instructions", []),
                    self.MAX_EXPERT_INTENT_RULES
                )
                if instructions:
                    selected_intent_module = module_name
                    sections.append(
                        f"Intent tactics ({module_name}):\n"
                        + "\n".join(f"- {item}" for item in instructions)
                    )

        conditional_modules = expert_pack.get("conditional_modules", {})
        selected_conditional_names: List[str] = []
        if isinstance(conditional_modules, dict):
            selected_modules: List[Tuple[int, str, str]] = []
            for module_key, module_payload in conditional_modules.items():
                if not isinstance(module_payload, dict):
                    continue
                if not self._should_apply_expert_module(
                    module_payload=module_payload,
                    detected_intent=effective_intent,
                    text_lower=text_lower,
                    word_count=word_count,
                    extracted_keywords=extracted_keywords,
                ):
                    continue

                instructions = self._normalize_instruction_list(
                    module_payload.get("instructions", []),
                    self.MAX_EXPERT_MODULE_RULES
                )
                if not instructions:
                    continue

                module_name = str(module_payload.get("name", "")).strip() or module_key.replace("_", " ").title()
                try:
                    priority = int(module_payload.get("priority", 50))
                except (TypeError, ValueError):
                    priority = 50
                compiled = (
                    f"Conditional tactics ({module_name}):\n"
                    + "\n".join(f"- {item}" for item in instructions)
                )
                selected_modules.append((priority, module_name, compiled))

            selected_modules.sort(key=lambda item: item[0], reverse=True)
            for _, module_name, compiled in selected_modules[: self.MAX_EXPERT_CONDITIONAL_MODULES]:
                selected_conditional_names.append(module_name)
                sections.append(compiled)

        result = "\n\n".join(section for section in sections if section)
        if self._is_prompt_injection_log_enabled():
            logger.info(
                (
                    "Prompt intelligence selected | intent=%s | words=%d | global_rules=%d | "
                    "intent_module=%s | conditional_modules=%s"
                ),
                effective_intent or "none",
                word_count,
                len(global_rules),
                selected_intent_module or "none",
                ", ".join(selected_conditional_names) if selected_conditional_names else "none",
            )
            if self._is_prompt_injection_text_log_enabled() and result:
                logger.info(
                    "Prompt intelligence preview: %s",
                    self._compact_text_for_log(result),
                )

        return result

    def _load_training_config(self) -> Dict[str, Any]:
        pack = self._load_prompt_pack()
        payload = pack.get("skill_prompt_training_config", {})
        if isinstance(payload, dict):
            return payload
        return {}

    def _resolve_training_target_score(self, model: Optional[str]) -> float:
        cfg = self._load_training_config()
        targets = cfg.get("targets", {})
        if not isinstance(targets, dict):
            return self.DEFAULT_TRAINING_TARGET_SCORE

        target_value = targets.get("overall_training_score_min", self.DEFAULT_TRAINING_TARGET_SCORE)
        tier_specific_key = (
            "weak_model_training_score_min"
            if self.is_weak_model(model)
            else "strong_model_training_score_min"
        )
        if tier_specific_key in targets:
            target_value = targets.get(tier_specific_key, target_value)

        try:
            return float(target_value)
        except (TypeError, ValueError):
            return self.DEFAULT_TRAINING_TARGET_SCORE

    def _resolve_training_weights(self) -> Dict[str, float]:
        cfg = self._load_training_config()
        raw_weights = cfg.get("scoring_weights", {})
        if not isinstance(raw_weights, dict):
            return dict(self.DEFAULT_TRAINING_WEIGHTS)

        weights = dict(self.DEFAULT_TRAINING_WEIGHTS)
        for key, value in raw_weights.items():
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if numeric <= 0:
                continue
            weights[str(key)] = numeric
        return weights

    def _resolve_required_sections(self) -> List[str]:
        cfg = self._load_training_config()
        raw_required = cfg.get("required_sections", [])
        if not isinstance(raw_required, list):
            return list(self.DEFAULT_REQUIRED_SECTIONS)

        required: List[str] = []
        seen: set[str] = set()
        for value in raw_required:
            item = str(value).strip()
            key = item.lower()
            if not item or key in seen:
                continue
            seen.add(key)
            required.append(item)
        return required or list(self.DEFAULT_REQUIRED_SECTIONS)

    def _resolve_training_grade(self, score: float) -> str:
        cfg = self._load_training_config()
        grading = cfg.get("grading", [])
        if not isinstance(grading, list):
            grading = []

        parsed_grading: List[Tuple[float, str]] = []
        for item in grading:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label", "")).strip()
            try:
                min_score = float(item.get("min_score", 0))
            except (TypeError, ValueError):
                continue
            if label:
                parsed_grading.append((min_score, label))

        if not parsed_grading:
            if score >= 95:
                return "A+"
            if score >= 90:
                return "A"
            if score >= 80:
                return "B"
            if score >= 70:
                return "C"
            return "D"

        parsed_grading.sort(key=lambda item: item[0], reverse=True)
        for min_score, label in parsed_grading:
            if score >= min_score:
                return label
        return parsed_grading[-1][1]

    def _build_skill_curriculum_instructions(
        self,
        user_message: str,
        detected_intent: Optional[str],
        keywords: Optional[List[str]]
    ) -> str:
        pack = self._load_prompt_pack()
        curriculum_pack = pack.get("skill_curriculum", {})
        if not isinstance(curriculum_pack, dict):
            return ""

        sections: List[str] = []
        text = str(user_message or "")
        text_lower = text.lower()
        effective_intent = detected_intent or self.detect_intent(text)
        extracted_keywords = {str(k).strip().lower() for k in (keywords or []) if k}

        global_rules = self._normalize_instruction_list(
            curriculum_pack.get("global_rules", []),
            self.MAX_CURRICULUM_GLOBAL_RULES,
        )
        if global_rules:
            sections.append(
                "Global skill rules:\n"
                + "\n".join(f"- {item}" for item in global_rules)
            )

        selected_intent_module = ""
        intent_curriculum = curriculum_pack.get("intent_curriculum", {})
        if isinstance(intent_curriculum, dict) and effective_intent:
            module_payload = intent_curriculum.get(effective_intent, {})
            if isinstance(module_payload, dict):
                module_name = (
                    str(module_payload.get("name", "")).strip()
                    or effective_intent.replace("_", " ").title()
                )
                instructions = self._normalize_instruction_list(
                    module_payload.get("instructions", []),
                    self.MAX_CURRICULUM_INTENT_RULES,
                )
                output_items = self._normalize_instruction_list(
                    module_payload.get("output_contract", []),
                    self.MAX_CURRICULUM_OUTPUT_ITEMS,
                )
                subsection: List[str] = []
                if instructions:
                    subsection.append(
                        "Instructions:\n"
                        + "\n".join(f"- {item}" for item in instructions)
                    )
                if output_items:
                    subsection.append(
                        "Output contract:\n"
                        + "\n".join(f"- {item}" for item in output_items)
                    )
                if subsection:
                    selected_intent_module = module_name
                    sections.append(
                        f"Intent playbook ({module_name}):\n"
                        + "\n\n".join(subsection)
                    )

        keyword_modules = curriculum_pack.get("keyword_modules", {})
        selected_keyword_modules: List[str] = []
        if isinstance(keyword_modules, dict):
            for module_key, module_payload in keyword_modules.items():
                if len(selected_keyword_modules) >= self.MAX_CURRICULUM_KEYWORD_MODULES:
                    break
                if not isinstance(module_payload, dict):
                    continue

                trigger_keywords = module_payload.get("trigger_keywords", [])
                if not isinstance(trigger_keywords, list):
                    continue

                hit = False
                for keyword in trigger_keywords:
                    kw = str(keyword).strip().lower()
                    if not kw:
                        continue
                    if kw in text_lower or kw in extracted_keywords:
                        hit = True
                        break
                if not hit:
                    continue

                instructions = self._normalize_instruction_list(
                    module_payload.get("instructions", []),
                    self.MAX_CURRICULUM_KEYWORD_RULES,
                )
                if not instructions:
                    continue

                module_name = str(module_payload.get("name", "")).strip()
                if not module_name:
                    module_name = str(module_key).replace("_", " ").title()

                selected_keyword_modules.append(module_name)
                sections.append(
                    f"Keyword module ({module_name}):\n"
                    + "\n".join(f"- {item}" for item in instructions)
                )

        result = "\n\n".join(section for section in sections if section)
        if self._is_prompt_injection_log_enabled():
            logger.info(
                "Skill curriculum selected | intent=%s | global=%d | intent_module=%s | keyword_modules=%s",
                effective_intent or "none",
                len(global_rules),
                selected_intent_module or "none",
                ", ".join(selected_keyword_modules) if selected_keyword_modules else "none",
            )
            if self._is_prompt_injection_text_log_enabled() and result:
                logger.info(
                    "Skill curriculum preview: %s",
                    self._compact_text_for_log(result),
                )
        return result

    def compute_prompt_training_score(
        self,
        included_headings: List[str],
        total_chars: int,
        model: Optional[str] = None,
        section_texts: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Compute a normalized 0-100 training score for injected prompt sections.
        """
        normalized_headings = {str(item).strip().lower() for item in included_headings if item}
        section_body_by_heading: Dict[str, str] = {}
        for section_text in section_texts or []:
            heading = self._extract_section_heading(section_text).lower()
            body = "\n".join(str(section_text or "").splitlines()[1:]).strip()
            if heading and body:
                section_body_by_heading[heading] = body
        weights = self._resolve_training_weights()
        required_sections = self._resolve_required_sections()
        target_score = self._resolve_training_target_score(model)

        component_headings = {
            "core_prompt": "PigTex Core Prompt Pack",
            "quality_safety": "PigTex Quality & Safety",
            "expert_tactics": "PigTex Expert Prompt Intelligence",
            "adaptive_guidance": "PigTex Adaptive Guidance",
            "skill_curriculum": "PigTex Skill Curriculum",
            "weak_model_boost": "PigTex Model Intelligence Boost",
        }

        model_is_weak = self.is_weak_model(model)
        score_points = 0.0
        score_max = 0.0
        components: Dict[str, Any] = {}

        for component, heading in component_headings.items():
            if component == "weak_model_boost" and not model_is_weak:
                continue

            weight = float(weights.get(component, 0.0))
            if weight <= 0:
                continue

            score_max += weight
            present = heading.lower() in normalized_headings
            body_chars = len(section_body_by_heading.get(heading.lower(), ""))
            if not present:
                points = 0.0
            elif not section_texts:
                points = round(weight * 0.45, 2)
            elif body_chars >= 180:
                points = weight
            elif body_chars >= 80:
                points = round(weight * 0.7, 2)
            else:
                points = round(weight * 0.3, 2)
            score_points += points
            components[component] = {
                "heading": heading,
                "weight": weight,
                "present": present,
                "points": points,
                "body_chars": body_chars,
            }

        budget_weight = float(weights.get("budget_efficiency", 0.0))
        if budget_weight > 0:
            score_max += budget_weight
            char_budget = (
                self.FILE_PACK_CHAR_BUDGET_WEAK
                if model_is_weak
                else self.FILE_PACK_CHAR_BUDGET
            )
            if total_chars <= int(char_budget * 0.92):
                budget_points = budget_weight
            elif total_chars <= char_budget:
                budget_points = round(budget_weight * 0.7, 2)
            else:
                budget_points = 0.0
            score_points += budget_points
            components["budget_efficiency"] = {
                "weight": budget_weight,
                "points": budget_points,
                "char_budget": char_budget,
                "total_chars": total_chars,
            }

        score = round((score_points / score_max) * 100.0, 1) if score_max > 0 else 0.0
        required_hits = sum(
            1 for section in required_sections if section.lower() in normalized_headings
        )
        required_coverage_percent = round(
            (required_hits / len(required_sections)) * 100.0, 1
        ) if required_sections else 100.0

        grade = self._resolve_training_grade(score)
        return {
            "score": score,
            "grade": grade,
            "target_score": target_score,
            "passes_target": score >= target_score,
            "required_sections": required_sections,
            "required_coverage_percent": required_coverage_percent,
            "components": components,
        }

    def _build_emotion_instruction(self, user_message: str) -> str:
        pack = self._load_prompt_pack()
        emotional_data = pack.get("emotional_responses", {})
        if not isinstance(emotional_data, dict):
            return ""

        handlers = emotional_data.get("emotion_handlers", {})
        if not isinstance(handlers, dict):
            return ""

        text = (user_message or "").lower()
        if not text:
            return ""

        best_score = 0
        best_instruction = ""
        for _, payload in handlers.items():
            if not isinstance(payload, dict):
                continue

            detection = payload.get("detection_keywords", {})
            if not isinstance(detection, dict):
                continue

            keywords: List[str] = []
            for lang in ("vi", "en"):
                lang_keywords = detection.get(lang, [])
                if isinstance(lang_keywords, list):
                    keywords.extend(str(kw).lower() for kw in lang_keywords if kw)

            score = sum(1 for kw in keywords if kw and kw in text)
            if score <= best_score:
                continue

            instruction = str(payload.get("inject_instruction", "")).strip()
            if not instruction:
                continue

            best_score = score
            best_instruction = instruction

        return best_instruction if best_score > 0 else ""

    def _build_topic_instructions(
        self,
        user_message: str,
        keywords: Optional[List[str]]
    ) -> List[str]:
        pack = self._load_prompt_pack()
        topic_data = pack.get("topic_handlers", {})
        if not isinstance(topic_data, dict):
            return []

        handlers = topic_data.get("handlers", {})
        if not isinstance(handlers, dict):
            return []

        text = (user_message or "").lower()
        extracted_keywords = {str(k).lower() for k in (keywords or []) if k}
        matched: List[Tuple[int, int, str]] = []

        for _, payload in handlers.items():
            if not isinstance(payload, dict):
                continue

            trigger_keywords = payload.get("keywords", [])
            if not isinstance(trigger_keywords, list):
                continue

            local_hits = 0
            for keyword in trigger_keywords:
                kw = str(keyword).strip().lower()
                if not kw:
                    continue
                if kw in text or kw in extracted_keywords:
                    local_hits += 1

            if local_hits <= 0:
                continue

            inject_text = str(payload.get("inject", "")).strip()
            if not inject_text:
                continue

            extras: List[str] = []
            disclaimer = str(payload.get("disclaimer", "")).strip()
            if disclaimer:
                extras.append(f"Disclaimer: {disclaimer}")

            crisis_resources = payload.get("crisis_resources", {})
            if isinstance(crisis_resources, dict):
                vn_resource = str(crisis_resources.get("vietnam", "")).strip()
                intl_resource = str(crisis_resources.get("international", "")).strip()
                if vn_resource or intl_resource:
                    resource_lines = []
                    if vn_resource:
                        resource_lines.append(f"- Vietnam: {vn_resource}")
                    if intl_resource:
                        resource_lines.append(f"- International: {intl_resource}")
                    extras.append("If needed, suggest crisis resources:\n" + "\n".join(resource_lines))

            compiled = inject_text if not extras else inject_text + "\n" + "\n".join(extras)
            priority_key = str(payload.get("priority", "low")).strip().lower()
            priority_score = self.TOPIC_PRIORITY_SCORE.get(priority_key, 1)
            matched.append((priority_score, local_hits, compiled))

        if not matched:
            return []

        matched.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in matched[:self.MAX_TOPIC_INSTRUCTIONS]]

    def _detect_context_topics(
        self,
        user_message: str,
        detected_intent: Optional[str],
        keywords: Optional[List[str]],
    ) -> List[str]:
        text = (user_message or "").lower()
        extracted_keywords = {str(item).strip().lower() for item in (keywords or []) if item}
        topic_keywords: Dict[str, List[str]] = {
            "health": [
                "bệnh", "đau", "triệu chứng", "thuốc", "sức khỏe", "doctor", "symptom", "pain",
            ],
            "finance": [
                "đầu tư", "chứng khoán", "crypto", "bitcoin", "tài chính", "invest", "loan", "money",
            ],
            "tech": [
                "code", "bug", "error", "lập trình", "programming", "api", "database", "ai", "app",
            ],
            "cooking": [
                "nấu", "recipe", "món ăn", "cook", "food", "ingredients",
            ],
            "travel": [
                "du lịch", "vé máy bay", "khách sạn", "travel", "hotel", "visa", "itinerary",
            ],
            "education": [
                "học", "study", "exam", "homework", "explain", "giải thích", "lesson",
            ],
            "relationships": [
                "người yêu", "chia tay", "dating", "relationship", "crush", "hẹn hò",
            ],
            "creative_writing": [
                "viết", "story", "poem", "truyện", "thơ", "compose",
            ],
        }
        intent_topic_map = {
            "learning": "education",
            "code_generation": "tech",
            "debug": "tech",
            "code_review": "tech",
            "creative": "creative_writing",
            "advice": "relationships",
        }

        scored_topics: List[Tuple[int, str]] = []
        for topic, candidates in topic_keywords.items():
            hits = 0
            for candidate in candidates:
                normalized = str(candidate).strip().lower()
                if not normalized:
                    continue
                if normalized in text or normalized in extracted_keywords:
                    hits += 1
            if detected_intent and intent_topic_map.get(detected_intent) == topic:
                hits += 1
            if hits > 0:
                scored_topics.append((hits, topic))

        scored_topics.sort(key=lambda item: item[0], reverse=True)
        return [topic for _, topic in scored_topics[: self.MAX_TOPIC_INSTRUCTIONS]]

    def _build_context_enrichment_instructions(
        self,
        user_message: str,
        detected_intent: Optional[str],
        keywords: Optional[List[str]],
    ) -> List[str]:
        pack = self._load_prompt_pack()
        payload = pack.get("context_enrichment", {})
        if not isinstance(payload, dict):
            return []

        layers = payload.get("enrichment_layers", [])
        if not isinstance(layers, list):
            return []

        topic_layer: Optional[Dict[str, Any]] = None
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            if str(layer.get("id", "")).strip() == "topic_expertise":
                topic_layer = layer
                break

        if not isinstance(topic_layer, dict):
            return []

        topics_payload = topic_layer.get("topics", {})
        if not isinstance(topics_payload, dict):
            return []

        detected_topics = self._detect_context_topics(user_message, detected_intent, keywords)
        instructions: List[str] = []
        for topic in detected_topics:
            instruction = str(topics_payload.get(topic, "")).strip()
            if instruction:
                instructions.append(instruction)
        return instructions

    def _build_natural_language_interpretation_instructions(
        self,
        user_message: str,
        detected_intent: Optional[str],
    ) -> List[str]:
        text = (user_message or "").strip()
        if not text:
            return []

        token_count = len(re.findall(r"\w+", text, flags=re.UNICODE))
        message_type = self._classify_message_type(text)
        instructions = [
            "Infer the user's real goal from everyday language before asking follow-up questions.",
            "Translate vague wording into the most useful concrete task while preserving the user's intent and tone.",
        ]

        if token_count <= 20 or detected_intent in {None, "learning", "advice", "planning", "creative"}:
            instructions.append(
                "When details are missing, choose the most reasonable default and state that assumption briefly instead of blocking on multiple questions."
            )

        if message_type in {"quick_question", "how_to", "explanation"}:
            instructions.append(
                "Lead with the direct answer first, then add optional examples, steps, or caveats only when they materially help."
            )
        else:
            instructions.append(
                "Organize the response into a clear outcome, practical actions, and only the minimum clarifications needed to avoid a wrong result."
            )

        instructions.append(
            "Ask at most one focused clarification question when a wrong assumption would materially change the answer or deliverable."
        )
        return instructions

    def _build_competitive_skill_matches(
        self,
        user_message: str,
        detected_intent: Optional[str],
        keywords: Optional[List[str]],
    ) -> str:
        matched_skills = self._resolve_competitive_skill_matches(
            user_message=user_message,
            detected_intent=detected_intent,
            keywords=keywords,
        )
        if not matched_skills:
            return ""
        try:
            foundry = self._get_skill_foundry()
            return foundry.format_runtime_skills(matched_skills)
        except Exception as exc:
            logger.warning("Skill Foundry runtime formatting skipped: %s", exc)
            return ""

    def _resolve_competitive_skill_matches(
        self,
        user_message: str,
        detected_intent: Optional[str],
        keywords: Optional[List[str]],
    ) -> List[Dict[str, Any]]:
        try:
            foundry = self._get_skill_foundry()
            return foundry.resolve_matches(
                user_message=user_message,
                detected_intent=detected_intent,
                keywords=keywords,
            )
        except Exception as exc:
            logger.warning("Skill Foundry runtime selection skipped: %s", exc)
            return []

    def _build_monetization_output_contract(
        self,
        matched_skills: Optional[List[Dict[str, Any]]],
    ) -> str:
        if not matched_skills:
            return ""
        try:
            foundry = self._get_skill_foundry()
            return foundry.format_runtime_output_contracts(matched_skills)
        except Exception as exc:
            logger.warning("Skill Foundry runtime contract formatting skipped: %s", exc)
            return ""

    def _resolve_prompt_slot_caps(self, model: Optional[str]) -> Dict[str, int]:
        return dict(
            self.PROMPT_SLOT_CAPS_WEAK
            if self.is_weak_model(model)
            else self.PROMPT_SLOT_CAPS_DEFAULT
        )

    def _build_dynamic_file_instructions(
        self,
        user_message: str,
        detected_intent: Optional[str],
        keywords: Optional[List[str]]
    ) -> str:
        sections: List[str] = []

        query_rewrite = self._build_query_rewrite_instructions(user_message, detected_intent)
        if query_rewrite:
            sections.append("Query optimization:\n" + query_rewrite)

        emotion_instruction = self._build_emotion_instruction(user_message)
        if emotion_instruction:
            sections.append("Emotion handling:\n- " + emotion_instruction)

        topic_instructions = self._build_topic_instructions(user_message, keywords)
        if topic_instructions:
            topic_lines = "\n".join(f"- {item}" for item in topic_instructions)
            sections.append("Topic guidance:\n" + topic_lines)

        enrichment_instructions = self._build_context_enrichment_instructions(
            user_message=user_message,
            detected_intent=detected_intent,
            keywords=keywords,
        )
        if enrichment_instructions:
            sections.append(
                "Topic expertise:\n"
                + "\n".join(f"- {item}" for item in enrichment_instructions)
            )

        natural_language_guidance = self._build_natural_language_interpretation_instructions(
            user_message=user_message,
            detected_intent=detected_intent,
        )
        if natural_language_guidance:
            sections.append(
                "Natural-language interpretation:\n"
                + "\n".join(f"- {item}" for item in natural_language_guidance)
            )

        return "\n\n".join(sections)

    # =========================================================================
    # Weak Model Booster
    # =========================================================================

    def _load_weak_model_booster(self) -> Dict[str, Any]:
        """Load weak model booster config from data/enhancement_rules/."""
        if self._weak_model_booster_cache is not None:
            return self._weak_model_booster_cache

        pack = self._load_prompt_pack()
        booster = pack.get("weak_model_booster", {})
        if isinstance(booster, dict) and booster:
            self._weak_model_booster_cache = booster
            return booster

        self._weak_model_booster_cache = {}
        return {}

    def classify_model_tier(self, model: Optional[str]) -> str:
        """
        Classify a model into weak/medium/strong tier.
        Determines how much prompt boosting to apply.

        Check order: strong → weak → medium.
        Within each tier, longer patterns match first to prevent
        broad patterns (e.g. 'gpt-4o') from swallowing specific
        ones (e.g. 'gpt-4o-mini').
        """
        if not model:
            return self.MODEL_TIER_MEDIUM

        model_lower = model.strip().lower()
        if model_lower in self.MODEL_TIER_CACHE:
            return self.MODEL_TIER_CACHE[model_lower]

        booster = self._load_weak_model_booster()
        classification = booster.get("model_classification", {})

        # Check strong first (rare, never ambiguous)
        for pattern in sorted(classification.get("strong_model_patterns", []), key=len, reverse=True):
            if str(pattern).lower() in model_lower:
                self.MODEL_TIER_CACHE[model_lower] = self.MODEL_TIER_STRONG
                return self.MODEL_TIER_STRONG

        # Check weak BEFORE medium to catch specific weak names
        # (e.g. 'gpt-4o-mini' before the broader 'gpt-4o')
        for pattern in sorted(classification.get("weak_model_patterns", []), key=len, reverse=True):
            if str(pattern).lower() in model_lower:
                self.MODEL_TIER_CACHE[model_lower] = self.MODEL_TIER_WEAK
                return self.MODEL_TIER_WEAK

        for pattern in sorted(classification.get("medium_model_patterns", []), key=len, reverse=True):
            if str(pattern).lower() in model_lower:
                self.MODEL_TIER_CACHE[model_lower] = self.MODEL_TIER_MEDIUM
                return self.MODEL_TIER_MEDIUM

        # Default heuristic: unknown models get medium treatment
        self.MODEL_TIER_CACHE[model_lower] = self.MODEL_TIER_MEDIUM
        return self.MODEL_TIER_MEDIUM

    def is_weak_model(self, model: Optional[str]) -> bool:
        """Check if a model is classified as weak/free tier."""
        return self.classify_model_tier(model) == self.MODEL_TIER_WEAK

    def _detect_language_hint(self, user_message: str) -> str:
        """Detect if user message is Vietnamese, English, or unknown."""
        text = (user_message or "").strip()
        if not text:
            return "unknown"

        # Check for Vietnamese characters
        vn_chars = len(re.findall(
            r"[ăâđêôơưĂÂĐÊÔƠƯáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]",
            text
        ))
        if vn_chars >= 2:
            return "vi"
        return "en"

    def _build_weak_model_boost(self, user_message: str, model: Optional[str],
                                 detected_intent: Optional[str], keywords: Optional[List[str]]) -> str:
        """
        Build additional prompt sections for weak models.
        Combines: system boost + CoT + few-shot + format enforcement.
        """
        if not self.is_weak_model(model):
            return ""

        booster = self._load_weak_model_booster()
        if not booster:
            return ""

        sections: List[str] = []
        complexity = self._estimate_complexity(user_message, detected_intent)
        lang = self._detect_language_hint(user_message)

        # 1. System-level boost (always for weak models)
        sys_boost = booster.get("weak_model_system_boost", {})
        if isinstance(sys_boost, dict):
            boost_instruction = str(sys_boost.get("instruction", "")).strip()
            if boost_instruction:
                sections.append(boost_instruction)

        # 2. Chain-of-Thought injection (for medium+ complexity)
        cot = booster.get("chain_of_thought", {})
        if isinstance(cot, dict):
            threshold = str(cot.get("complexity_threshold", "medium")).strip().lower()
            complexities_above = {"trivial": 0, "low": 1, "medium": 2, "high": 3, "expert": 4}
            msg_level = complexities_above.get(complexity, 2)
            threshold_level = complexities_above.get(threshold, 2)

            if msg_level >= threshold_level:
                # Pick language-specific CoT instruction
                if lang == "vi":
                    cot_text = str(cot.get("instruction_vi", cot.get("instruction_generic", ""))).strip()
                else:
                    cot_text = str(cot.get("instruction_en", cot.get("instruction_generic", ""))).strip()
                if cot_text:
                    sections.append(f"**Step-by-step reasoning required:**\n{cot_text}")

        # 3. Few-shot examples (match by intent/keywords)
        few_shots = booster.get("few_shot_templates", {})
        if isinstance(few_shots, dict) and complexity in ("medium", "high", "expert"):
            text_lower = (user_message or "").lower()
            extracted_kw = {str(k).lower() for k in (keywords or []) if k}

            for _category, template in few_shots.items():
                if not isinstance(template, dict) or _category == "description":
                    continue
                trigger_kws = template.get("trigger_keywords", [])
                if not isinstance(trigger_kws, list):
                    continue

                matched = any(
                    str(kw).lower() in text_lower or str(kw).lower() in extracted_kw
                    for kw in trigger_kws
                )
                if not matched:
                    continue

                example = template.get("example", {})
                if isinstance(example, dict) and example.get("user") and example.get("assistant"):
                    sections.append(
                        f"**Example of good response format:**\n"
                        f"User: {example['user']}\n"
                        f"Assistant: {example['assistant']}"
                    )
                    break  # Only inject 1 few-shot to save tokens

        # 4. Format enforcement (always for weak models)
        format_rules = booster.get("format_enforcement", {})
        if isinstance(format_rules, dict):
            anti_ramble = str(format_rules.get("anti_ramble_instruction", "")).strip()
            if anti_ramble:
                sections.append(anti_ramble)

            rules_list = format_rules.get("rules", [])
            if isinstance(rules_list, list) and rules_list:
                rules_text = "\n".join(f"- {str(r).strip()}" for r in rules_list[:4] if r)
                if rules_text:
                    sections.append(f"**Output quality rules:**\n{rules_text}")

        result = "\n\n".join(section for section in sections if section)

        if self._is_prompt_injection_log_enabled():
            logger.info(
                "Weak model boost applied | model=%s | tier=%s | complexity=%s | lang=%s | sections=%d | chars=%d",
                model or "unknown",
                self.classify_model_tier(model),
                complexity,
                lang,
                len(sections),
                len(result),
            )

        return result

    def get_self_refinement_config(self, model: Optional[str],
                                    user_message: str,
                                    detected_intent: Optional[str]) -> Optional[Dict[str, Any]]:
        """
        Returns self-refinement config if applicable for this model + query.
        Returns None if self-refinement should not be used.
        """
        if not self.is_weak_model(model):
            return None

        booster = self._load_weak_model_booster()
        refine_config = booster.get("self_refinement", {})
        if not isinstance(refine_config, dict) or not refine_config.get("enabled", False):
            return None

        complexity = self._estimate_complexity(user_message, detected_intent)
        threshold = str(refine_config.get("complexity_threshold", "high")).strip().lower()
        complexities = {"trivial": 0, "low": 1, "medium": 2, "high": 3, "expert": 4}
        if complexities.get(complexity, 2) < complexities.get(threshold, 3):
            return None

        lang = self._detect_language_hint(user_message)
        if lang == "vi":
            review_prompt = refine_config.get("review_prompt_vi", refine_config.get("review_prompt_generic", ""))
        else:
            review_prompt = refine_config.get("review_prompt_en", refine_config.get("review_prompt_generic", ""))

        return {
            "enabled": True,
            "review_prompt": str(review_prompt).strip(),
            "max_tokens": int(refine_config.get("max_tokens_for_refinement", 4096)),
        }

    def get_web_search_recommendation(self, model: Optional[str],
                                       user_message: str,
                                       detected_intent: Optional[str]) -> bool:
        """
        Recommend whether to auto-enable web search for this request.
        Primarily used for weak/free models to compensate for knowledge gaps.
        """
        if not self.is_weak_model(model):
            return False

        booster = self._load_weak_model_booster()
        ws_config = booster.get("web_search_auto_trigger", {})
        if not isinstance(ws_config, dict) or not ws_config.get("enabled", False):
            return False

        text_lower = (user_message or "").lower()

        # Check intent-based triggers
        always_intents = ws_config.get("always_search_intents", [])
        if isinstance(always_intents, list) and detected_intent in always_intents:
            return True

        # Check keyword triggers
        trigger_kws = ws_config.get("trigger_keywords", [])
        if isinstance(trigger_kws, list):
            for kw in trigger_kws:
                if str(kw).lower() in text_lower:
                    return True

        return False

    # =========================================================================
    # File Pack Section Builder (with weak model boost integration)
    # =========================================================================

    def _build_file_pack_sections(
        self,
        user_message: str,
        detected_intent: Optional[str],
        keywords: Optional[List[str]],
        include_base_prompt: bool,
        model: Optional[str] = None
    ) -> List[str]:
        section_candidates: List[Dict[str, Any]] = []
        section_meta: Dict[str, Dict[str, Any]] = {}
        slot_caps = self._resolve_prompt_slot_caps(model)
        slot_order = {name: index for index, name in enumerate(self.PROMPT_SLOT_ORDER)}

        file_system_prompts = self._collect_file_system_prompts(include_base_prompt=include_base_prompt)
        if file_system_prompts:
            section_candidates.append(
                {
                    "slot": "core",
                    "heading": "## PigTex Core Prompt Pack",
                    "content": "\n\n".join(file_system_prompts),
                    "max_chars": min(self.CORE_SECTION_MAX_CHARS, slot_caps.get("core", self.CORE_SECTION_MAX_CHARS)),
                }
            )

        static_quality_guardrails = self._build_static_quality_guardrails()
        if static_quality_guardrails:
            section_candidates.append(
                {
                    "slot": "quality",
                    "heading": "## PigTex Quality & Safety",
                    "content": static_quality_guardrails,
                    "max_chars": min(self.QUALITY_SECTION_MAX_CHARS, slot_caps.get("quality", self.QUALITY_SECTION_MAX_CHARS)),
                }
            )

        expert_tactics = self._build_expert_prompt_intelligence(
            user_message=user_message,
            detected_intent=detected_intent,
            keywords=keywords
        )
        if expert_tactics:
            section_candidates.append(
                {
                    "slot": "expert",
                    "heading": "## PigTex Expert Prompt Intelligence",
                    "content": expert_tactics,
                    "max_chars": min(self.EXPERT_SECTION_MAX_CHARS, slot_caps.get("expert", self.EXPERT_SECTION_MAX_CHARS)),
                }
            )

        dynamic_guidance = self._build_dynamic_file_instructions(
            user_message=user_message,
            detected_intent=detected_intent,
            keywords=keywords
        )
        if dynamic_guidance:
            section_candidates.append(
                {
                    "slot": "adaptive",
                    "heading": "## PigTex Adaptive Guidance",
                    "content": dynamic_guidance,
                    "max_chars": min(self.ADAPTIVE_SECTION_MAX_CHARS, slot_caps.get("adaptive", self.ADAPTIVE_SECTION_MAX_CHARS)),
                }
            )

        skill_curriculum = self._build_skill_curriculum_instructions(
            user_message=user_message,
            detected_intent=detected_intent,
            keywords=keywords,
        )
        if skill_curriculum:
            section_candidates.append(
                {
                    "slot": "curriculum",
                    "heading": "## PigTex Skill Curriculum",
                    "content": skill_curriculum,
                    "max_chars": min(self.CURRICULUM_SECTION_MAX_CHARS, slot_caps.get("curriculum", self.CURRICULUM_SECTION_MAX_CHARS)),
                }
            )

        matched_competitive_skills = self._resolve_competitive_skill_matches(
            user_message=user_message,
            detected_intent=detected_intent,
            keywords=keywords,
        )
        competitive_skills = ""
        if matched_competitive_skills:
            try:
                competitive_skills = self._get_skill_foundry().format_runtime_skills(matched_competitive_skills)
            except Exception as exc:
                logger.warning("Skill Foundry runtime formatting skipped: %s", exc)
        if competitive_skills:
            section_candidates.append(
                {
                    "slot": "competitive",
                    "heading": "## PigTex Competitive Skill Matches",
                    "content": competitive_skills,
                    "max_chars": min(self.COMPETITIVE_SECTION_MAX_CHARS, slot_caps.get("competitive", self.COMPETITIVE_SECTION_MAX_CHARS)),
                }
            )

        monetization_contract = self._build_monetization_output_contract(matched_competitive_skills)
        if monetization_contract:
            section_candidates.append(
                {
                    "slot": "contract",
                    "heading": "## PigTex Monetization Output Contract",
                    "content": monetization_contract,
                    "max_chars": min(
                        self.MONETIZATION_CONTRACT_SECTION_MAX_CHARS,
                        slot_caps.get("contract", self.MONETIZATION_CONTRACT_SECTION_MAX_CHARS),
                    ),
                }
            )

        # Weak Model Booster section — only for weak/free tier models
        weak_boost = self._build_weak_model_boost(
            user_message=user_message,
            model=model,
            detected_intent=detected_intent,
            keywords=keywords
        )
        if weak_boost:
            section_candidates.append(
                {
                    "slot": "weak_boost",
                    "heading": "## PigTex Model Intelligence Boost",
                    "content": weak_boost,
                    "max_chars": min(self.WEAK_MODEL_BOOST_SECTION_MAX_CHARS, slot_caps.get("weak_boost", self.WEAK_MODEL_BOOST_SECTION_MAX_CHARS)),
                }
            )

        section_candidates.sort(
            key=lambda item: slot_order.get(str(item.get("slot") or ""), len(slot_order))
        )
        sections: List[str] = []
        for candidate in section_candidates:
            heading = str(candidate.get("heading") or "").strip()
            content = str(candidate.get("content") or "")
            max_chars = int(candidate.get("max_chars") or 0)
            original = f"{heading}\n{content}".strip()
            text = original
            if not text:
                continue

            cap_trimmed = False
            if max_chars > 0 and len(text) > max_chars:
                trimmed = text[: max(0, max_chars - 30)].rstrip()
                text = trimmed + "\n\n[Section trimmed for budget]"
                cap_trimmed = True

            section_meta[heading] = {
                "original_chars": len(original),
                "final_chars": len(text),
                "cap_trimmed": cap_trimmed,
                "cap": max_chars,
            }
            sections.append(text)

        # Keep file-based injection bounded to avoid context bloat.
        # Use higher budget for weak models (they need more prompt engineering).
        char_budget = (
            self.FILE_PACK_CHAR_BUDGET_WEAK
            if self.is_weak_model(model)
            else self.FILE_PACK_CHAR_BUDGET
        )
        bounded: List[str] = []
        bounded_meta: List[Dict[str, Any]] = []
        remaining = char_budget
        for section in sections:
            text = section.strip()
            heading = self._extract_section_heading(text)
            if not text or remaining <= 0:
                continue

            if len(text) <= remaining:
                bounded.append(text)
                bounded_meta.append(
                    {"heading": heading, "chars": len(text), "budget_trimmed": False}
                )
                remaining -= len(text) + 2
                continue

            if remaining < 320:
                break

            trimmed = text[: max(0, remaining - 28)].rstrip()
            final_text = trimmed + "\n\n[Truncated for token budget]"
            bounded.append(final_text)
            bounded_meta.append(
                {"heading": heading, "chars": len(final_text), "budget_trimmed": True}
            )
            break

        if self._is_prompt_injection_log_enabled():
            included_headings = [meta["heading"] for meta in bounded_meta]
            candidate_headings = [
                str(item.get("heading") or "").replace("## ", "")
                for item in section_candidates
                if str(item.get("heading") or "").strip()
            ]
            dropped_headings = [h for h in candidate_headings if h not in included_headings]
            total_chars = sum(len(s) for s in bounded)
            training_metrics = self.compute_prompt_training_score(
                included_headings=included_headings,
                total_chars=total_chars,
                model=model,
                section_texts=bounded,
            )

            section_summaries: List[str] = []
            for item in bounded_meta:
                heading = item["heading"]
                key = f"## {heading}"
                meta = section_meta.get(key, {})
                flags: List[str] = []
                if bool(meta.get("cap_trimmed")):
                    flags.append("cap_trim")
                if bool(item.get("budget_trimmed")):
                    flags.append("budget_trim")
                flag_text = f"|{'+'.join(flags)}" if flags else ""
                section_summaries.append(f"{heading}:{item['chars']}{flag_text}")

            logger.info(
                (
                    "Prompt pack sections | intent=%s | included=%s | dropped=%s | "
                    "total_chars=%d | budget=%d | training_score=%.1f | grade=%s | target=%.1f | pass=%s"
                ),
                detected_intent or "none",
                ", ".join(section_summaries) if section_summaries else "none",
                ", ".join(dropped_headings) if dropped_headings else "none",
                total_chars,
                char_budget,
                float(training_metrics.get("score", 0.0)),
                training_metrics.get("grade", "N/A"),
                float(training_metrics.get("target_score", self.DEFAULT_TRAINING_TARGET_SCORE)),
                bool(training_metrics.get("passes_target", False)),
            )
            if self._is_prompt_injection_text_log_enabled():
                for idx, section in enumerate(bounded, start=1):
                    logger.info(
                        "Prompt section preview #%d (%s): %s",
                        idx,
                        self._extract_section_heading(section),
                        self._compact_text_for_log(section),
                    )

        return bounded

    def build_prompt_diagnostics(
        self,
        user_message: str,
        model: Optional[str] = None,
        detected_intent: Optional[str] = None,
        keywords: Optional[List[str]] = None,
        include_base_prompt: bool = False,
    ) -> Dict[str, Any]:
        """
        Build lightweight diagnostics for prompt/skill training evaluation.
        Does not require DB system prompt data.
        """
        effective_intent = detected_intent or self.detect_intent(user_message)
        effective_keywords = keywords if keywords is not None else self.extract_keywords(user_message)
        sections = self._build_file_pack_sections(
            user_message=user_message,
            detected_intent=effective_intent,
            keywords=effective_keywords,
            include_base_prompt=include_base_prompt,
            model=model,
        )
        section_info: List[Dict[str, Any]] = []
        headings: List[str] = []
        for section in sections:
            heading = self._extract_section_heading(section)
            headings.append(heading)
            section_info.append({"heading": heading, "chars": len(section)})

        total_chars = sum(item["chars"] for item in section_info)
        score = self.compute_prompt_training_score(
            included_headings=headings,
            total_chars=total_chars,
            model=model,
            section_texts=sections,
        )
        return {
            "model": model or "default",
            "model_tier": self.classify_model_tier(model),
            "intent": effective_intent or "none",
            "keywords": effective_keywords,
            "sections": section_info,
            "section_count": len(section_info),
            "total_chars": total_chars,
            "training_score": score,
        }
    
    # =========================================================================
    # Main Injection Method
    # =========================================================================
    
    def build_injected_prompt(
        self,
        user_message: str,
        model: Optional[str] = None,
        user_tier: str = "free",
        detected_intent: Optional[str] = None,
        keywords: Optional[List[str]] = None,
        user_context: Optional[str] = None
    ) -> str:
        """
        Build the complete injected system prompt.
        This is the main method that combines everything.

        Flow:
        1. Get base system prompt
        2. Add file-based prompt pack (including weak model boost)
        3. Add relevant skills
        4. Add user context (from local memory)

        Returns the full system prompt to inject.
        """
        parts = []
        model_tier = self.classify_model_tier(model)

        # 1. Base system prompt
        system_prompt = self.get_system_prompt(
            "default_assistant",
            model,
            user_tier
        )
        if system_prompt:
            parts.append(system_prompt)

        # 1.5 File-based prompt pack (repository data/*)
        # - If DB has a base prompt, skip file base prompt to avoid duplication.
        # - Passes model to enable weak model boost section.
        file_sections = self._build_file_pack_sections(
            user_message=user_message,
            detected_intent=detected_intent,
            keywords=keywords,
            include_base_prompt=not bool(system_prompt),
            model=model,
        )
        parts.extend(file_sections)

        # 2. Relevant skills
        skills = self.get_skills_for_intent(detected_intent, keywords)
        if skills:
            skills_text = self.format_skills_for_prompt(skills)
            parts.append(f"\n## Techniques to Apply\n{skills_text}")

        # 3. User context (from local memory - passed in)
        if user_context:
            parts.append(f"\n## User Context\n{user_context}")

        final_prompt = "\n\n".join(part for part in parts if str(part).strip())
        if self._is_prompt_injection_log_enabled():
            logger.info(
                (
                    "Injected prompt built | model=%s | tier=%s | intent=%s | user_tier=%s | "
                    "parts=%d | total_chars=%d"
                ),
                model or "default",
                model_tier,
                detected_intent or "none",
                user_tier,
                len(parts),
                len(final_prompt),
            )
            if self._is_prompt_injection_text_log_enabled() and final_prompt:
                logger.info(
                    "Injected prompt preview: %s",
                    self._compact_text_for_log(final_prompt),
                )
        return final_prompt

    def _match_intent_signal(self, message_lower: str, message_tokens: set[str], keyword: str) -> float:
        kw = str(keyword).strip().lower()
        if not kw:
            return 0.0

        if " " in kw:
            return 3.2 if kw in message_lower else 0.0

        if len(kw) <= 3 and re.fullmatch(r"[a-z0-9_]+", kw):
            return 0.9 if kw in message_tokens else 0.0

        if re.fullmatch(r"[a-z0-9_+\-\.]+", kw):
            pattern = rf"(?<!\w){re.escape(kw)}(?!\w)"
            return 1.5 if re.search(pattern, message_lower) else 0.0

        return 1.3 if kw in message_lower else 0.0

    def _score_intent_candidates(self, message: str) -> Dict[str, float]:
        message_lower = " ".join((message or "").lower().split())
        message_tokens = set(re.findall(r"\w+", message_lower, flags=re.UNICODE))
        if not message_lower:
            return {}

        intent_keywords = {
            "creative": [
                "story", "poem", "essay", "creative", "compose",
                "viết truyện", "viet truyen", "sáng tạo", "sang tao", "thơ", "tho",
                "kịch bản", "kich ban", "hook", "headline", "facebook ads", "tiktok ads",
                "ad copy", "quảng cáo", "quang cao", "caption", "script",
            ],
            "planning": [
                "plan", "organize", "schedule", "strategy", "roadmap",
                "kế hoạch", "ke hoach", "tổ chức", "to chuc", "lộ trình", "lo trinh", "chiến lược", "chien luoc",
            ],
            "learning": [
                "teach", "explain", "what is", "how to", "learn", "tutorial",
                "dạy", "day", "giải thích", "giai thich", "là gì", "la gi", "học", "hoc",
            ],
            "advice": [
                "recommend", "suggest", "advice", "tips", "should i",
                "tư vấn", "tu van", "gợi ý", "goi y", "nên", "nen",
                "bắt đầu từ đâu", "bat dau tu dau",
            ],
            "summarize": ["summarize", "tldr", "brief", "tóm tắt", "tom tat"],
            "analysis": [
                "analyze", "compare", "evaluate", "pros and cons",
                "phân tích", "phan tich", "so sánh", "so sanh", "đánh giá", "danh gia",
            ],
            "research": [
                "research", "find information", "learn about", "documentation",
                "citation", "source", "evidence", "benchmark",
                "nghiên cứu", "nghien cuu", "tìm hiểu", "tim hieu", "tài liệu", "tai lieu", "trích dẫn", "trich dan",
            ],
            "code_review": [
                "refactor", "optimize code", "code review", "code quality", "review rủi ro",
                "regression", "test gap", "review bug risk", "đánh giá rủi ro code", "danh gia rui ro code",
            ],
            "debug": [
                "bug", "error", "traceback", "exception", "stack trace", "debug",
                "root cause", "fix bug", "sửa lỗi", "sua loi", "lỗi", "loi", "log lỗi", "log loi",
            ],
            "code_generation": [
                "function", "class", "api", "implement", "code", "database", "algorithm", "component",
                "viết hàm", "viet ham", "triển khai", "trien khai", "xây dựng api", "xay dung api",
            ],
        }

        scores = {intent: 0.0 for intent in intent_keywords}
        for intent, keywords in intent_keywords.items():
            for keyword in keywords:
                scores[intent] += self._match_intent_signal(message_lower, message_tokens, keyword)

        code_signal_hits = sum(
            1
            for keyword in (
                "stack trace", "traceback", "exception", "api", "function", "class", "bug",
                "root cause", "refactor", "database", "component", "lỗi", "loi",
            )
            if self._match_intent_signal(message_lower, message_tokens, keyword) > 0
        )
        if code_signal_hits:
            scores["debug"] += min(4.2, code_signal_hits * 0.75)
            scores["code_review"] += min(2.8, code_signal_hits * 0.35)
            scores["code_generation"] += min(2.5, code_signal_hits * 0.3)

        if any(marker in message_lower for marker in ("stack trace", "traceback", "root cause", "exception", "sửa lỗi", "sua loi")):
            scores["debug"] += 4.5

        if any(marker in message_lower for marker in ("refactor", "code review", "review rủi ro", "regression", "test gap")):
            scores["code_review"] += 4.2

        if any(marker in message_lower for marker in ("implement", "triển khai", "trien khai", "xây dựng", "xay dung")) and any(
            marker in message_lower for marker in ("api", "function", "class", "component", "database")
        ):
            scores["code_generation"] += 3.8

        if any(marker in message_lower for marker in ("tóm tắt", "tom tat", "summarize", "tldr")):
            scores["summarize"] += 4.5

        if "ngắn gọn" in message_lower or "briefly" in message_lower or "concise" in message_lower:
            scores["summarize"] += 0.3

        if any(marker in message_lower for marker in ("facebook ads", "tiktok ads", "hook", "headline", "quảng cáo", "quang cao")):
            scores["creative"] += 3.4

        if "compare" in message_lower or "so sánh" in message_lower or "so sanh" in message_lower:
            scores["analysis"] += 3.2

        if "research" in message_lower or "nghiên cứu" in message_lower or "nghien cuu" in message_lower:
            scores["research"] += 3.4

        if "không biết nên bắt đầu từ đâu" in message_lower or "khong biet nen bat dau tu dau" in message_lower:
            scores["advice"] += 3.0

        return scores

    def detect_intent(self, message: str) -> Optional[str]:
        """
        Weighted intent detection for natural-language prompts.
        Prefer stronger code/creative/research signals over weak style hints.
        """
        scores = self._score_intent_candidates(message)
        if not scores:
            return None

        ranked = sorted(
            scores.items(),
            key=lambda item: (
                item[1],
                item[0] in {"debug", "code_review", "code_generation"},
                item[0] in {"research", "analysis", "creative"},
            ),
            reverse=True,
        )
        best_intent, best_score = ranked[0]
        if best_score < 1.2:
            return None

        if len(ranked) > 1:
            second_score = ranked[1][1]
            if best_score - second_score < 0.45 and best_intent == "summarize":
                for preferred_intent in ("debug", "code_review", "code_generation", "research", "analysis", "creative"):
                    if scores.get(preferred_intent, 0.0) >= best_score - 0.35:
                        return preferred_intent
        return best_intent
    
    def extract_keywords(self, message: str) -> List[str]:
        """Extract keywords from message for skill matching"""
        # Simple keyword extraction
        # In production, use NLP
        common_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "have", "has", "had", "do", "does", "did", "will", "would",
            "could", "should", "may", "might", "must", "can", "i", "you",
            "we", "they", "it", "this", "that", "these", "those", "my",
            "your", "our", "their", "its", "to", "of", "in", "for", "on",
            "with", "at", "by", "from", "as", "into", "through", "about",
            "please", "help", "me", "want", "need"
        }
        
        words = re.findall(r"\w+", message.lower(), flags=re.UNICODE)
        keywords: List[str] = []
        seen: set[str] = set()

        for word in words:
            if len(word) <= 2 or word in common_words:
                continue
            if word.isdigit() or word in seen:
                continue
            seen.add(word)
            keywords.append(word)
            if len(keywords) >= 10:
                break

        return keywords


def get_prompt_injector(db: Session) -> PromptInjector:
    """Factory function to get prompt injector"""
    return PromptInjector(db)
