"""GitHub repository URL extraction and normalization."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import urlparse

GITHUB_TEXT_RE = re.compile(
    r"(?:(?:https?|git)://|git@)?github\.com[:/]"
    r"(?P<owner>[A-Za-z0-9_.-]+)"
    r"/"
    r"(?P<repo>[A-Za-z0-9_.-]+)"
    r"(?P<tail>/[^\s\]\)>\"'`}]*)?",
    re.IGNORECASE,
)

NON_REPO_OWNERS = {"features", "about", "topics", "trending", "marketplace", "pricing"}
NON_REPO_REPOS = {"issues", "pulls", "pull", "wiki", "settings"}
STOP_SEGMENTS = {
    "issues",
    "pulls",
    "pull",
    "tree",
    "blob",
    "releases",
    "release",
    "tags",
    "tag",
    "archive",
    "actions",
    "wiki",
    "graphs",
    "network",
    "projects",
    "security",
    "compare",
    "commit",
    "commits",
}
TRAILING_PUNCTUATION = ".,;:!?)'\"`}]>"


@dataclass(frozen=True)
class NormalizedGitHubURL:
    repo_url: str
    owner: str
    repo: str
    repo_key: str


def normalize_github_url(value: str) -> NormalizedGitHubURL | None:
    """Normalize a GitHub URL-like string to https://github.com/<owner>/<repo>."""
    if not value:
        return None

    text = unescape(str(value).strip())
    if "github.com" not in text.lower():
        return None

    if text.startswith("git@github.com:"):
        text = "ssh://" + text.replace("git@github.com:", "github.com/", 1)
    elif text.lower().startswith("github.com/"):
        text = "https://" + text

    parsed = urlparse(text)
    if parsed.netloc.lower() != "github.com":
        return None

    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) < 2:
        return None

    owner = _clean_segment(segments[0])
    repo = _clean_segment(segments[1])
    if not _is_repo_like(owner, repo):
        return None

    return _normalized(owner, repo)


def extract_github_urls(text: str) -> list[NormalizedGitHubURL]:
    """Extract repository-like GitHub URLs from free text, preserving first-seen order."""
    if not text or "github.com" not in text.lower():
        return []

    results: list[NormalizedGitHubURL] = []
    seen: set[str] = set()
    for match in GITHUB_TEXT_RE.finditer(unescape(str(text))):
        owner = _clean_segment(match.group("owner"))
        repo = _clean_segment(match.group("repo"))
        if not _is_repo_like(owner, repo):
            continue
        normalized = _normalized(owner, repo)
        if normalized.repo_key in seen:
            continue
        seen.add(normalized.repo_key)
        results.append(normalized)
    return results


def best_github_url_from_fields(fields: list[tuple[str, str]]) -> tuple[NormalizedGitHubURL, str] | None:
    """Return the first repository-like URL from ordered fields and its source field."""
    for field_name, value in fields:
        direct = normalize_github_url(value)
        if direct is not None:
            return direct, field_name

        extracted = extract_github_urls(value)
        if extracted:
            return extracted[0], field_name
    return None


def _clean_segment(segment: str) -> str:
    segment = segment.strip().strip(TRAILING_PUNCTUATION)
    if segment.endswith(".git"):
        segment = segment[:-4]
    return segment.strip(TRAILING_PUNCTUATION)


def _is_repo_like(owner: str, repo: str) -> bool:
    if not owner or not repo:
        return False
    if owner.lower() in NON_REPO_OWNERS or repo.lower() in NON_REPO_REPOS:
        return False
    if owner.lower() == "gist" or owner.lower() == "gist.github.com":
        return False
    if owner.startswith(".") or repo.startswith("."):
        return False
    if "/" in owner or "/" in repo:
        return False
    return True


def _normalized(owner: str, repo: str) -> NormalizedGitHubURL:
    repo_key = f"{owner.lower()}/{repo.lower()}"
    return NormalizedGitHubURL(
        repo_url=f"https://github.com/{owner}/{repo}",
        owner=owner,
        repo=repo,
        repo_key=repo_key,
    )
