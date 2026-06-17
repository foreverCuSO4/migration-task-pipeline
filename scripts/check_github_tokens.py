#!/usr/bin/env python3
"""Check GitHub tokens from auth.json without printing token values."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from migration_task_pipeline.github_auth import GitHubToken, load_github_tokens, normalize_tokens


DEFAULT_CODE_SEARCH_QUERY = "torch.cuda repo:pytorch/pytorch"


@dataclass(frozen=True)
class EndpointCheck:
    name: str
    ok: bool
    status_code: int | None
    message: str
    login: str = ""
    total_count: int | None = None
    rate_limit_remaining: str = ""
    rate_limit_reset: str = ""


@dataclass(frozen=True)
class TokenCheck:
    label: str
    fingerprint: str
    user: EndpointCheck
    code_search: EndpointCheck | None = None

    @property
    def ok(self) -> bool:
        if not self.user.ok:
            return False
        return self.code_search is None or self.code_search.ok


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--auth-file",
        default="auth.json",
        help="JSON file containing github_tokens or legacy github_api_key/github_token/github_key.",
    )
    parser.add_argument(
        "--include-env",
        action="store_true",
        help="Also check GITHUB_TOKEN before tokens loaded from auth.json.",
    )
    parser.add_argument(
        "--skip-code-search",
        action="store_true",
        help="Only check /user. By default the script also checks /search/code for Layer B readiness.",
    )
    parser.add_argument(
        "--code-search-query",
        default=DEFAULT_CODE_SEARCH_QUERY,
        help="Code-search smoke query used unless --skip-code-search is set.",
    )
    parser.add_argument(
        "--api-base-url",
        default="https://api.github.com",
        help="GitHub API base URL.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="HTTP timeout in seconds.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of text.",
    )
    return parser.parse_args()


def collect_tokens(auth_file: str | Path, *, include_env: bool = False) -> list[GitHubToken]:
    tokens: list[GitHubToken] = []
    env_token = os.getenv("GITHUB_TOKEN")
    if include_env and env_token and env_token.strip():
        tokens.append(GitHubToken(token=env_token.strip(), name="GITHUB_TOKEN"))
    tokens.extend(load_github_tokens(auth_file))
    return normalize_tokens(tokens)


def token_fingerprint(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:12]} len:{len(token)}"


def check_token(
    token: GitHubToken,
    *,
    session: requests.Session,
    api_base_url: str,
    timeout: float,
    code_search_query: str | None,
) -> TokenCheck:
    user = check_endpoint(
        session=session,
        api_base_url=api_base_url,
        token=token.token,
        path="/user",
        params=None,
        timeout=timeout,
    )
    code_search = None
    if code_search_query is not None:
        code_search = check_endpoint(
            session=session,
            api_base_url=api_base_url,
            token=token.token,
            path="/search/code",
            params={"q": code_search_query, "per_page": 1},
            timeout=timeout,
            accept="application/vnd.github.text-match+json",
        )
    return TokenCheck(
        label=token.label,
        fingerprint=token_fingerprint(token.token),
        user=user,
        code_search=code_search,
    )


def check_endpoint(
    *,
    session: requests.Session,
    api_base_url: str,
    token: str,
    path: str,
    params: dict[str, Any] | None,
    timeout: float,
    accept: str = "application/vnd.github+json",
) -> EndpointCheck:
    endpoint_name = path.lstrip("/") or path
    try:
        response = session.get(
            f"{api_base_url.rstrip('/')}{path}",
            headers={
                "Accept": accept,
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            params=params,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return EndpointCheck(endpoint_name, False, None, f"request failed: {exc}")

    payload = response_json(response)
    message = response_message(payload)
    login = str(payload.get("login") or "") if isinstance(payload, dict) else ""
    total_count = payload.get("total_count") if isinstance(payload, dict) else None
    if not isinstance(total_count, int):
        total_count = None
    return EndpointCheck(
        name=endpoint_name,
        ok=response.status_code == 200,
        status_code=response.status_code,
        message=message,
        login=login,
        total_count=total_count,
        rate_limit_remaining=response.headers.get("X-RateLimit-Remaining", ""),
        rate_limit_reset=format_rate_limit_reset(response.headers.get("X-RateLimit-Reset", "")),
    )


def response_json(response: requests.Response) -> object:
    try:
        return response.json()
    except Exception:
        return {}


def response_message(payload: object) -> str:
    if isinstance(payload, dict):
        return str(payload.get("message") or "")
    return ""


def format_rate_limit_reset(value: str) -> str:
    if not value:
        return ""
    try:
        return datetime.fromtimestamp(int(value)).isoformat(timespec="seconds")
    except (ValueError, OSError):
        return value


def render_text(results: list[TokenCheck], *, include_code_search: bool) -> str:
    lines = [f"checked tokens: {len(results)}"]
    for index, result in enumerate(results, start=1):
        lines.append(f"[{index}] {result.label} ({result.fingerprint})")
        lines.append(f"    /user        {format_endpoint(result.user)}")
        if include_code_search and result.code_search is not None:
            lines.append(f"    /search/code {format_endpoint(result.code_search)}")
    ok_count = sum(1 for result in results if result.ok)
    lines.append(f"summary: ok {ok_count}/{len(results)}")
    return "\n".join(lines)


def format_endpoint(check: EndpointCheck) -> str:
    status = "OK" if check.ok else "FAIL"
    http_status = f"HTTP {check.status_code}" if check.status_code is not None else "NO_RESPONSE"
    details = []
    if check.login:
        details.append(f"login={check.login}")
    if check.total_count is not None:
        details.append(f"total_count={check.total_count}")
    if check.message:
        details.append(f"message={check.message}")
    if check.rate_limit_remaining:
        details.append(f"remaining={check.rate_limit_remaining}")
    if check.rate_limit_reset:
        details.append(f"reset={check.rate_limit_reset}")
    suffix = f"  {'; '.join(details)}" if details else ""
    return f"{status} {http_status}{suffix}"


def render_json(results: list[TokenCheck]) -> str:
    return json.dumps([token_check_to_json(result) for result in results], indent=2, sort_keys=True)


def token_check_to_json(result: TokenCheck) -> dict[str, object]:
    payload: dict[str, object] = {
        "label": result.label,
        "fingerprint": result.fingerprint,
        "ok": result.ok,
        "user": endpoint_check_to_json(result.user),
    }
    if result.code_search is not None:
        payload["code_search"] = endpoint_check_to_json(result.code_search)
    return payload


def endpoint_check_to_json(check: EndpointCheck) -> dict[str, object]:
    return {
        "name": check.name,
        "ok": check.ok,
        "status_code": check.status_code,
        "message": check.message,
        "login": check.login,
        "total_count": check.total_count,
        "rate_limit_remaining": check.rate_limit_remaining,
        "rate_limit_reset": check.rate_limit_reset,
    }


def main() -> int:
    args = parse_args()
    tokens = collect_tokens(args.auth_file, include_env=args.include_env)
    if not tokens:
        print(f"No GitHub tokens found in {args.auth_file}", file=sys.stderr)
        return 2

    session = requests.Session()
    code_search_query = None if args.skip_code_search else args.code_search_query
    results = [
        check_token(
            token,
            session=session,
            api_base_url=args.api_base_url,
            timeout=args.timeout,
            code_search_query=code_search_query,
        )
        for token in tokens
    ]

    if args.json:
        print(render_json(results))
    else:
        print(render_text(results, include_code_search=code_search_query is not None))
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
