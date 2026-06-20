"""OpenCode subprocess integration for Stage D."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any

from .config import OpenCodeConfig


API_KEY_ENV = "MTP_OPENCODE_API_KEY"
BASE_URL_ENV = "MTP_OPENCODE_BASE_URL"


@dataclass(frozen=True)
class OpenCodeRequest:
    command: list[str]
    env: dict[str, str]
    cwd: Path
    timeout_seconds: int
    display_command: list[str]


@dataclass(frozen=True)
class OpenCodeCompleted:
    returncode: int
    stdout: str
    stderr: str


class SubprocessOpenCodeRunner:
    def run(self, request: OpenCodeRequest) -> OpenCodeCompleted:
        binary = request.command[0]
        if shutil.which(binary) is None:
            raise RuntimeError(f"OpenCode binary not found on PATH: {binary}")
        completed = subprocess.run(
            request.command,
            cwd=request.cwd,
            env=request.env,
            text=True,
            capture_output=True,
            timeout=request.timeout_seconds,
            check=False,
        )
        return OpenCodeCompleted(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


def build_opencode_request(
    *,
    config: OpenCodeConfig,
    api_key: str,
    workspace_dir: str | Path,
    prompt_text: str,
    agent_prompt: str,
    external_allow_paths: list[str | Path],
    timeout_seconds: int,
    base_env: dict[str, str],
) -> OpenCodeRequest:
    if not config.base_url:
        raise RuntimeError("Layer D OpenCode base_url is required in config")
    if not config.model:
        raise RuntimeError("Layer D OpenCode model is required in config")
    if not api_key:
        raise RuntimeError("Layer D OpenCode API key is empty")

    full_model = f"{config.provider_id}/{config.model}"
    inline_config = build_opencode_inline_config(
        config=config,
        full_model=full_model,
        agent_prompt=agent_prompt,
        external_allow_paths=external_allow_paths,
    )

    env = dict(base_env)
    env[API_KEY_ENV] = api_key
    env[BASE_URL_ENV] = config.base_url
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(inline_config, ensure_ascii=True, sort_keys=True)
    env.setdefault("OPENCODE_DISABLE_AUTOUPDATE", "true")

    command = [
        config.opencode_binary,
        "run",
        "--agent",
        config.agent_name,
        "--model",
        full_model,
        "--dir",
        str(Path(workspace_dir)),
        "--format",
        "json",
        prompt_text,
    ]
    return OpenCodeRequest(
        command=command,
        env=env,
        cwd=Path(workspace_dir),
        timeout_seconds=timeout_seconds,
        display_command=list(command),
    )


def build_opencode_inline_config(
    *,
    config: OpenCodeConfig,
    full_model: str,
    agent_prompt: str,
    external_allow_paths: list[str | Path],
) -> dict[str, Any]:
    permission = build_permissions(external_allow_paths)
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": full_model,
        "small_model": full_model,
        "enabled_providers": [config.provider_id],
        "provider": {
            config.provider_id: {
                "name": config.provider_name,
                "npm": config.npm,
                "options": {
                    "baseURL": f"{{env:{BASE_URL_ENV}}}",
                    "apiKey": f"{{env:{API_KEY_ENV}}}",
                },
                "models": {
                    config.model: {
                        "name": config.model,
                    },
                },
            }
        },
        "permission": permission,
        "agent": {
            config.agent_name: {
                "description": "Review a candidate repository for G4 task suitability.",
                "mode": "primary",
                "model": full_model,
                "prompt": agent_prompt,
            }
        },
    }


def build_permissions(external_allow_paths: list[str | Path]) -> dict[str, Any]:
    external_rules: dict[str, str] = {}
    for path in external_allow_paths:
        resolved = Path(path).resolve()
        external_rules[str(resolved)] = "allow"
        external_rules[str(resolved / "**")] = "allow"
    external_rules["*"] = "deny"
    return {
        "*": "deny",
        "read": {
            "*": "allow",
            "*.env": "deny",
            "*.env.*": "deny",
            "auth.json": "deny",
            "**/auth.json": "deny",
        },
        "glob": "allow",
        "grep": "allow",
        "list": "allow",
        "webfetch": "allow",
        "websearch": "allow",
        "external_directory": external_rules,
        "bash": "deny",
        "edit": "deny",
        "write": "deny",
        "apply_patch": "deny",
        "task": "deny",
        "question": "deny",
        "lsp": "deny",
        "skill": "deny",
    }
