"""Output parsing and validation for Stage D review cards."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover - exercised only in broken envs
    yaml = None
    YAML_IMPORT_ERROR = exc
else:
    YAML_IMPORT_ERROR = None


VALID_VERDICTS = {"pilot", "hold", "reject"}
VALID_CONFIDENCE = {"high", "medium", "low"}
REQUIRED_TOP_LEVEL_KEYS = {
    "schema_version",
    "repo",
    "verdict",
    "project_summary",
    "migration_surface",
    "task_sketch",
    "verifier_feasibility",
    "hidden_case_plan",
    "benchmark_value",
    "risks",
    "manual_probe",
    "reviewer_notes",
    "open_questions",
}


@dataclass(frozen=True)
class ValidatedReviewCard:
    payload: dict[str, Any]
    yaml_text: str


def parse_and_validate_review_card(text: str) -> ValidatedReviewCard:
    """Extract a YAML review card from model output and validate core fields."""
    if yaml is None:  # pragma: no cover - defensive
        raise RuntimeError("PyYAML is required to parse Stage D review cards") from YAML_IMPORT_ERROR

    yaml_text = extract_yaml_document(text)
    try:
        payload = yaml.safe_load(yaml_text)
    except Exception as exc:
        raise ValueError("OpenCode output is not valid YAML") from exc
    if not isinstance(payload, dict):
        raise ValueError("OpenCode output must be a single YAML mapping")

    missing = sorted(REQUIRED_TOP_LEVEL_KEYS - set(payload))
    if missing:
        raise ValueError(f"Review card is missing required top-level keys: {', '.join(missing)}")

    if payload.get("schema_version") != "g4_review.v1":
        raise ValueError("Review card schema_version must be g4_review.v1")

    repo = payload.get("repo")
    if not isinstance(repo, dict) or not str(repo.get("key") or "").strip():
        raise ValueError("Review card repo.key is required")

    verdict = payload.get("verdict")
    if not isinstance(verdict, dict):
        raise ValueError("Review card verdict must be a mapping")
    status = str(verdict.get("status") or "").strip().lower()
    confidence = str(verdict.get("confidence") or "").strip().lower()
    if status not in VALID_VERDICTS:
        raise ValueError("Review card verdict.status must be one of: pilot, hold, reject")
    if confidence not in VALID_CONFIDENCE:
        raise ValueError("Review card verdict.confidence must be one of: high, medium, low")
    if not str(verdict.get("summary") or "").strip():
        raise ValueError("Review card verdict.summary is required")

    if not isinstance(payload.get("risks"), list):
        raise ValueError("Review card risks must be a list")
    if not isinstance(payload.get("open_questions"), list):
        raise ValueError("Review card open_questions must be a list")

    return ValidatedReviewCard(payload=payload, yaml_text=normalize_yaml_text(payload))


def extract_yaml_document(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        raise ValueError("OpenCode output is empty")

    fenced = re.search(r"```(?:yaml|yml)?\s*(.*?)```", stripped, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        return fenced.group(1).strip()

    marker = "schema_version:"
    marker_index = stripped.find(marker)
    if marker_index >= 0:
        return stripped[marker_index:].strip()

    return stripped


def normalize_yaml_text(payload: dict[str, Any]) -> str:
    return yaml.safe_dump(payload, allow_unicode=False, sort_keys=False, width=120)
