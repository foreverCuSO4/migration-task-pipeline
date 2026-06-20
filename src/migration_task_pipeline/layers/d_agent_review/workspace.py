"""Review workspace creation for Stage D OpenCode runs."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any


@dataclass(frozen=True)
class ReviewWorkspace:
    root: Path
    candidate_link: Path
    mace_link: Path
    input_json: Path
    readme: Path
    repo_slug: str


def repo_slug(repo_key: str) -> str:
    value = repo_key.strip().lower().replace("/", "__")
    value = re.sub(r"[^a-z0-9_.-]+", "-", value)
    value = value.strip(".-")
    return value or "unknown-repo"


def create_review_workspace(
    *,
    workspace_root: str | Path,
    item: dict[str, Any],
    repo_path: str | Path,
    mace_reference_path: str | Path,
    review_input: dict[str, Any],
) -> ReviewWorkspace:
    repo_key_value = str(item.get("repo_key") or review_input.get("repo_key") or "")
    slug = repo_slug(repo_key_value)
    root = Path(workspace_root) / slug
    candidate_path = Path(repo_path).resolve()
    mace_path = Path(mace_reference_path).resolve()
    if not candidate_path.is_dir():
        raise FileNotFoundError(f"Candidate repository path does not exist: {candidate_path}")
    if not mace_path.is_dir():
        raise FileNotFoundError(f"MACE reference path does not exist: {mace_path}")

    root.mkdir(parents=True, exist_ok=True)
    candidate_link = root / "candidate_repo"
    mace_link = root / "mace_reference"
    ensure_directory_symlink(candidate_path, candidate_link)
    ensure_directory_symlink(mace_path, mace_link)

    input_json = root / "review-input.json"
    readme = root / "README.md"
    input_payload = dict(review_input)
    input_payload["workspace"] = {
        "candidate_repo": "candidate_repo",
        "mace_reference": "mace_reference",
    }
    input_json.write_text(json.dumps(input_payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    readme.write_text(workspace_readme(repo_key_value), encoding="utf-8")
    return ReviewWorkspace(
        root=root,
        candidate_link=candidate_link,
        mace_link=mace_link,
        input_json=input_json,
        readme=readme,
        repo_slug=slug,
    )


def ensure_directory_symlink(target: Path, link: Path) -> None:
    if link.is_symlink():
        current = Path(os.readlink(link))
        if not current.is_absolute():
            current = (link.parent / current).resolve()
        if current.resolve() == target.resolve():
            return
        link.unlink()
    elif link.exists():
        if link.is_dir():
            shutil.rmtree(link)
        else:
            link.unlink()
    os.symlink(target, link, target_is_directory=True)


def workspace_readme(repo_key_value: str) -> str:
    return f"""# Stage D Review Workspace

Repository: {repo_key_value}

Files:

- `review-input.json`: metadata, C2 scores, C2 evidence, and output requirements.
- `candidate_repo/`: symlink to the candidate repository checkout.
- `mace_reference/`: symlink to the local MACE G4 reference task.

The reviewer must output exactly one `g4_review.v1` YAML object and must not modify files.
"""
