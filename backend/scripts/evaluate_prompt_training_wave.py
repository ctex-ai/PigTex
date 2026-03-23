"""
Offline evaluator for PigTex prompt/skill training wave.

Computes concrete metrics from scenario cases without calling external LLM APIs:
- intent detection hit-rate
- prompt training score (weak/strong model tier)
- required section coverage
- prompt char budget usage
- target pass-rate
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "data" / "system_prompts").exists():
            return parent
    raise RuntimeError("Cannot locate repo root containing data/system_prompts")


REPO_ROOT = _find_repo_root()
BACKEND_ROOT = REPO_ROOT / "App_desktop" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Keep script logs quiet for batch scoring.
os.environ.setdefault("PIGTEX_PROMPT_INJECTION_LOG_ENABLED", "0")
os.environ.setdefault("PIGTEX_PROMPT_INJECTION_LOG_TEXT", "0")

from app.memory.prompt_injector import PromptInjector  # noqa: E402


@dataclass
class EvalCaseResult:
    case_id: str
    expected_intent: str
    detected_intent: str
    intent_match: bool
    weak_score: float
    weak_grade: str
    weak_pass: bool
    weak_chars: int
    strong_score: float
    strong_grade: str
    strong_pass: bool
    strong_chars: int


def percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    sorted_vals = sorted(values)
    idx = (len(sorted_vals) - 1) * p
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return float(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac)


def _load_cases(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases", [])
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"No cases found in {path}")
    normalized: List[Dict[str, Any]] = []
    for idx, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            continue
        message = str(case.get("message", "")).strip()
        expected = str(case.get("expected_intent", "")).strip()
        case_id = str(case.get("id", f"case-{idx:03d}")).strip()
        if not message or not expected:
            continue
        normalized.append(
            {
                "id": case_id,
                "message": message,
                "expected_intent": expected,
            }
        )
    if not normalized:
        raise ValueError(f"No valid cases found in {path}")
    return normalized


def _pct(part: int, whole: int) -> float:
    if whole <= 0:
        return 0.0
    return round((part / whole) * 100.0, 2)


def evaluate_cases(
    cases: List[Dict[str, Any]],
    weak_model: str,
    strong_model: str,
) -> Dict[str, Any]:
    injector = PromptInjector(db=None)  # type: ignore[arg-type]
    results: List[EvalCaseResult] = []
    intent_hits = 0
    weak_required_coverage: List[float] = []
    strong_required_coverage: List[float] = []
    weak_section_hits: Dict[str, int] = {}
    strong_section_hits: Dict[str, int] = {}

    for case in cases:
        msg = case["message"]
        expected_intent = case["expected_intent"]

        detected_intent = injector.detect_intent(msg) or "none"
        if detected_intent == expected_intent:
            intent_hits += 1

        keywords = injector.extract_keywords(msg)
        weak_diag = injector.build_prompt_diagnostics(
            user_message=msg,
            model=weak_model,
            detected_intent=detected_intent,
            keywords=keywords,
            include_base_prompt=False,
        )
        strong_diag = injector.build_prompt_diagnostics(
            user_message=msg,
            model=strong_model,
            detected_intent=detected_intent,
            keywords=keywords,
            include_base_prompt=False,
        )

        weak_score = float(weak_diag["training_score"]["score"])
        weak_grade = str(weak_diag["training_score"]["grade"])
        weak_pass = bool(weak_diag["training_score"]["passes_target"])
        weak_chars = int(weak_diag["total_chars"])

        strong_score = float(strong_diag["training_score"]["score"])
        strong_grade = str(strong_diag["training_score"]["grade"])
        strong_pass = bool(strong_diag["training_score"]["passes_target"])
        strong_chars = int(strong_diag["total_chars"])

        weak_required_coverage.append(
            float(weak_diag["training_score"]["required_coverage_percent"])
        )
        strong_required_coverage.append(
            float(strong_diag["training_score"]["required_coverage_percent"])
        )

        for section in weak_diag["sections"]:
            heading = str(section["heading"])
            weak_section_hits[heading] = weak_section_hits.get(heading, 0) + 1
        for section in strong_diag["sections"]:
            heading = str(section["heading"])
            strong_section_hits[heading] = strong_section_hits.get(heading, 0) + 1

        results.append(
            EvalCaseResult(
                case_id=case["id"],
                expected_intent=expected_intent,
                detected_intent=detected_intent,
                intent_match=(detected_intent == expected_intent),
                weak_score=weak_score,
                weak_grade=weak_grade,
                weak_pass=weak_pass,
                weak_chars=weak_chars,
                strong_score=strong_score,
                strong_grade=strong_grade,
                strong_pass=strong_pass,
                strong_chars=strong_chars,
            )
        )

    total = len(results)
    weak_scores = [r.weak_score for r in results]
    strong_scores = [r.strong_score for r in results]
    weak_chars = [float(r.weak_chars) for r in results]
    strong_chars = [float(r.strong_chars) for r in results]
    weak_pass_count = sum(1 for r in results if r.weak_pass)
    strong_pass_count = sum(1 for r in results if r.strong_pass)

    by_intent_total: Dict[str, int] = {}
    by_intent_hits: Dict[str, int] = {}
    for r in results:
        by_intent_total[r.expected_intent] = by_intent_total.get(r.expected_intent, 0) + 1
        if r.intent_match:
            by_intent_hits[r.expected_intent] = by_intent_hits.get(r.expected_intent, 0) + 1
    by_intent = {
        intent: {
            "cases": count,
            "hit": by_intent_hits.get(intent, 0),
            "hit_rate_percent": _pct(by_intent_hits.get(intent, 0), count),
        }
        for intent, count in sorted(by_intent_total.items())
    }

    training_cfg = injector._load_training_config()  # noqa: SLF001
    target_cfg = training_cfg.get("targets", {}) if isinstance(training_cfg, dict) else {}

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evaluator": "evaluate_prompt_training_wave.py",
        "cases_file_scenarios": total,
        "models": {
            "weak": weak_model,
            "strong": strong_model,
        },
        "targets": target_cfg,
        "metrics": {
            "intent_detection_hit_rate_percent": _pct(intent_hits, total),
            "weak_model": {
                "avg_score": round(statistics.mean(weak_scores), 2),
                "p50_score": round(percentile(weak_scores, 0.50), 2),
                "p95_score": round(percentile(weak_scores, 0.95), 2),
                "target_pass_rate_percent": _pct(weak_pass_count, total),
                "required_section_coverage_avg_percent": round(statistics.mean(weak_required_coverage), 2),
                "prompt_chars_p50": round(percentile(weak_chars, 0.50), 1),
                "prompt_chars_p95": round(percentile(weak_chars, 0.95), 1),
            },
            "strong_model": {
                "avg_score": round(statistics.mean(strong_scores), 2),
                "p50_score": round(percentile(strong_scores, 0.50), 2),
                "p95_score": round(percentile(strong_scores, 0.95), 2),
                "target_pass_rate_percent": _pct(strong_pass_count, total),
                "required_section_coverage_avg_percent": round(statistics.mean(strong_required_coverage), 2),
                "prompt_chars_p50": round(percentile(strong_chars, 0.50), 1),
                "prompt_chars_p95": round(percentile(strong_chars, 0.95), 1),
            },
            "sections_coverage_percent": {
                "weak": {k: _pct(v, total) for k, v in sorted(weak_section_hits.items())},
                "strong": {k: _pct(v, total) for k, v in sorted(strong_section_hits.items())},
            },
        },
        "intent_breakdown": by_intent,
        "case_results": [
            {
                "id": r.case_id,
                "expected_intent": r.expected_intent,
                "detected_intent": r.detected_intent,
                "intent_match": r.intent_match,
                "weak_score": r.weak_score,
                "weak_grade": r.weak_grade,
                "weak_pass_target": r.weak_pass,
                "strong_score": r.strong_score,
                "strong_grade": r.strong_grade,
                "strong_pass_target": r.strong_pass,
            }
            for r in results
        ],
    }
    return report


def _resolve_path(path_value: str, default: Path) -> Path:
    if not path_value:
        return default
    path = Path(path_value)
    if path.is_absolute():
        return path
    return (REPO_ROOT / path).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate PigTex prompt/skill training wave metrics."
    )
    parser.add_argument(
        "--cases",
        default="ops/observability/training/prompt_training_cases_v1.json",
        help="Path to training cases JSON",
    )
    parser.add_argument(
        "--weak-model",
        default="gpt-4o-mini",
        help="Model id treated as weak tier",
    )
    parser.add_argument(
        "--strong-model",
        default="gpt-5-low",
        help="Model id treated as strong/medium tier",
    )
    parser.add_argument(
        "--output-json",
        default="ops/observability/reports/prompt-training-wave-latest.json",
        help="Output report path",
    )
    args = parser.parse_args()

    cases_path = _resolve_path(args.cases, REPO_ROOT / "ops" / "observability" / "training" / "prompt_training_cases_v1.json")
    output_path = _resolve_path(args.output_json, REPO_ROOT / "ops" / "observability" / "reports" / "prompt-training-wave-latest.json")
    cases = _load_cases(cases_path)

    report = evaluate_cases(
        cases=cases,
        weak_model=args.weak_model,
        strong_model=args.strong_model,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    weak = report["metrics"]["weak_model"]
    strong = report["metrics"]["strong_model"]
    print("Prompt/Skill Training Wave Evaluation")
    print(f"- Cases: {report['cases_file_scenarios']}")
    print(f"- Intent hit-rate: {report['metrics']['intent_detection_hit_rate_percent']:.2f}%")
    print(f"- Weak avg score: {weak['avg_score']:.2f} (pass-rate {weak['target_pass_rate_percent']:.2f}%)")
    print(f"- Strong avg score: {strong['avg_score']:.2f} (pass-rate {strong['target_pass_rate_percent']:.2f}%)")
    print(f"- Report: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
