"""Shared GitHub authentication helpers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class GitHubToken:
    token: str
    name: str = ""

    @property
    def label(self) -> str:
        name = self.name.strip()
        token = self.token.strip()
        if not name or name == token or _looks_like_token(name):
            return "github-token"
        return name


class GitHubTokenPool:
    """Round-robin token provider for GitHub API clients."""

    def __init__(self, tokens: Iterable[GitHubToken | str]) -> None:
        self._tokens = normalize_tokens(tokens)
        if not self._tokens:
            raise RuntimeError("GITHUB_TOKEN or auth.json GitHub token is required for GitHub API access")
        self._index = 0

    @classmethod
    def from_env(cls, *, auth_path: str | Path = "auth.json") -> "GitHubTokenPool":
        tokens: list[GitHubToken] = []
        env_token = os.getenv("GITHUB_TOKEN")
        if env_token and env_token.strip():
            tokens.append(GitHubToken(token=env_token.strip(), name="GITHUB_TOKEN"))
        tokens.extend(load_github_tokens(auth_path))
        return cls(tokens)

    @classmethod
    def from_token(cls, token: str) -> "GitHubTokenPool":
        return cls([GitHubToken(token=token, name="token")])

    def __len__(self) -> int:
        return len(self._tokens)

    @property
    def tokens(self) -> tuple[GitHubToken, ...]:
        return tuple(self._tokens)

    def next_token(self) -> GitHubToken:
        token = self._tokens[self._index]
        self._index = (self._index + 1) % len(self._tokens)
        return token


def normalize_tokens(tokens: Iterable[GitHubToken | str]) -> list[GitHubToken]:
    result = []
    seen = set()
    for item in tokens:
        if isinstance(item, GitHubToken):
            token = item.token.strip()
            name = item.name.strip()
        else:
            token = str(item).strip()
            name = ""
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(GitHubToken(token=token, name=name))
    return result


def load_github_tokens(auth_path: str | Path) -> list[GitHubToken]:
    path = Path(auth_path)
    if not path.exists():
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []

    tokens = list(_tokens_from_github_tokens_field(payload.get("github_tokens")))
    tokens.extend(_tokens_from_legacy_keys(payload))
    return normalize_tokens(tokens)


def _tokens_from_github_tokens_field(value: object) -> Iterable[GitHubToken]:
    if isinstance(value, list):
        for index, item in enumerate(value, start=1):
            if isinstance(item, dict):
                token = item.get("token")
                name = item.get("name") or f"github_tokens[{index}]"
                if isinstance(token, str):
                    yield GitHubToken(token=token, name=str(name))
            elif isinstance(item, str):
                yield GitHubToken(token=item, name=f"github_tokens[{index}]")
    elif isinstance(value, dict):
        for name, token in value.items():
            if isinstance(token, str):
                yield GitHubToken(token=token, name=str(name))


def _tokens_from_legacy_keys(payload: dict[str, object]) -> Iterable[GitHubToken]:
    candidate_keys = ("github_api_key", "github_token", "github_key")
    for key in candidate_keys:
        value = payload.get(key)
        if isinstance(value, str):
            yield GitHubToken(token=value, name=key)
    for value in payload.values():
        if isinstance(value, dict):
            for key in candidate_keys:
                nested = value.get(key)
                if isinstance(nested, str):
                    yield GitHubToken(token=nested, name=key)


def _looks_like_token(value: str) -> bool:
    prefixes = ("ghp_", "github_pat_", "gho_", "ghu_", "ghs_", "ghr_")
    return value.startswith(prefixes)
