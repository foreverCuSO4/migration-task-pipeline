"""Authentication helpers for Stage D OpenCode review."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_opencode_api_key(auth_path: str | Path, *, provider_id: str) -> str:
    """Load a provider API key from auth.json without exposing it in diagnostics."""
    path = Path(auth_path)
    if not path.exists():
        raise RuntimeError(f"OpenCode auth file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Unable to read OpenCode auth file: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"OpenCode auth file must contain a JSON object: {path}")

    key = _key_from_provider_map(payload.get("opencode_api_keys"), provider_id=provider_id)
    if key:
        return key

    legacy_key = payload.get("opencode_api_key")
    if isinstance(legacy_key, str) and legacy_key.strip():
        return legacy_key.strip()

    raise RuntimeError(f"OpenCode API key for provider '{provider_id}' was not found in {path}")


def _key_from_provider_map(value: Any, *, provider_id: str) -> str:
    if isinstance(value, dict):
        direct = value.get(provider_id)
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            item_provider = str(item.get("provider_id") or item.get("name") or "").strip()
            item_key = item.get("api_key") or item.get("token")
            if item_provider == provider_id and isinstance(item_key, str) and item_key.strip():
                return item_key.strip()
    return ""
