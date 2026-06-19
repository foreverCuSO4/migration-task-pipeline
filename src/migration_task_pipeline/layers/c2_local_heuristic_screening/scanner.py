"""Local filesystem scanner for Stage C2."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterable

from .config import LocalScannerConfig


SOURCE_SUFFIXES = {
    ".py",
    ".c",
    ".cc",
    ".cpp",
    ".cu",
    ".cuh",
    ".h",
    ".hh",
    ".hpp",
    ".pyx",
    ".rs",
    ".go",
    ".java",
    ".js",
    ".ts",
}

TEXT_SUFFIXES = SOURCE_SUFFIXES | {
    ".md",
    ".rst",
    ".txt",
    ".toml",
    ".cfg",
    ".ini",
    ".yml",
    ".yaml",
    ".json",
    ".sh",
    ".cmake",
    ".dockerfile",
}

INTERFACE_SCRIPT_NAMES = {
    "train.py",
    "eval.py",
    "evaluate.py",
    "infer.py",
    "inference.py",
    "predict.py",
    "benchmark.py",
    "demo.py",
    "main.py",
}

INSTALL_FILES = {
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "environment.yml",
    "environment.yaml",
    "dockerfile",
    "cmakelists.txt",
}

TEST_EXAMPLE_PARTS = {"tests", "test", "examples", "example", "demo", "demos", "notebooks", "notebook"}
DOC_PARTS = {"docs", "doc", "documentation"}
VENDOR_PARTS = {"vendor", "third_party", "third-party", "external", "build", "dist", "site-packages"}


@dataclass(frozen=True)
class LocalPattern:
    group: str
    term: str
    regex: re.Pattern[str]


@dataclass(frozen=True)
class LocalHit:
    group: str
    term: str
    path: str
    line: int = 0


LOCAL_PATTERNS = [
    LocalPattern("cuda", "torch.cuda", re.compile(r"\btorch\.cuda\b")),
    LocalPattern("cuda", ".cuda(", re.compile(r"\.cuda\s*\(")),
    LocalPattern("cuda", 'device="cuda"', re.compile(r"device\s*=\s*['\"]cuda['\"]")),
    LocalPattern("cuda", "cuda:", re.compile(r"cuda\s*:")),
    LocalPattern("cuda", "CUDAExtension", re.compile(r"\bCUDAExtension\b")),
    LocalPattern("cuda", "nvcc", re.compile(r"\bnvcc\b", re.IGNORECASE)),
    LocalPattern("cuda", "nccl", re.compile(r"\bnccl\b", re.IGNORECASE)),
    LocalPattern("cuda", "cudnn", re.compile(r"\bcudnn\b", re.IGNORECASE)),
    LocalPattern("cuda", "nvidia-smi", re.compile(r"\bnvidia-smi\b", re.IGNORECASE)),
    LocalPattern("cuda", "cupy", re.compile(r"\bcupy\b")),
    LocalPattern("cuda", "numba.cuda", re.compile(r"\bnumba\.cuda\b")),
    LocalPattern("cuda", "triton.jit", re.compile(r"\btriton\.jit\b")),
    LocalPattern("interface", "console_scripts", re.compile(r"\bconsole_scripts\b")),
    LocalPattern("interface", "project.scripts", re.compile(r"\[project\.scripts\]")),
    LocalPattern("interface", "argparse.ArgumentParser", re.compile(r"\bargparse\.ArgumentParser\b")),
    LocalPattern("interface", "click.command", re.compile(r"\bclick\.command\b")),
    LocalPattern("interface", "typer.Typer", re.compile(r"\btyper\.Typer\b")),
    LocalPattern("interface", 'if __name__ == "__main__"', re.compile(r"if\s+__name__\s*==\s*['\"]__main__['\"]")),
    LocalPattern("interface", "def main", re.compile(r"\bdef\s+main\s*\(")),
    LocalPattern("test", "pytest", re.compile(r"\bpytest\b")),
    LocalPattern("reference", "--device cpu", re.compile(r"--device(?:=|\s+)cpu\b")),
    LocalPattern("reference", 'device == "cpu"', re.compile(r"device\s*==\s*['\"]cpu['\"]")),
    LocalPattern("reference", 'map_location="cpu"', re.compile(r"map_location\s*=\s*['\"]cpu['\"]")),
    LocalPattern("reference", "backend", re.compile(r"\bbackend\b", re.IGNORECASE)),
    LocalPattern("reference", "reference", re.compile(r"\breference\b", re.IGNORECASE)),
    LocalPattern("reference", "baseline", re.compile(r"\bbaseline\b", re.IGNORECASE)),
    LocalPattern("reference", "expected", re.compile(r"\bexpected\b", re.IGNORECASE)),
    LocalPattern("reference", "fixture", re.compile(r"\bfixture\b", re.IGNORECASE)),
    LocalPattern("reference", "golden", re.compile(r"\bgolden\b", re.IGNORECASE)),
    LocalPattern("risk", "download", re.compile(r"\b(download|wget|curl)\b", re.IGNORECASE)),
    LocalPattern("risk", "gdown", re.compile(r"\bgdown\b", re.IGNORECASE)),
    LocalPattern("risk", "kaggle", re.compile(r"\bkaggle\b", re.IGNORECASE)),
    LocalPattern("risk", "wandb", re.compile(r"\bwandb\b", re.IGNORECASE)),
    LocalPattern("risk", "s3://", re.compile(r"s3://", re.IGNORECASE)),
    LocalPattern("risk", "flash-attn", re.compile(r"\bflash-attn\b", re.IGNORECASE)),
    LocalPattern("risk", "deepspeed", re.compile(r"\bdeepspeed\b", re.IGNORECASE)),
]


def scan_repository(item: dict[str, Any], config: LocalScannerConfig | None = None) -> dict[str, object]:
    """Scan a C1 materialized repository and return bounded evidence."""
    config = config or LocalScannerConfig()
    payload = item.get("payload_json") or {}
    local_path = Path(str(payload.get("local_path") or ""))
    repo_key = str(item.get("repo_key") or payload.get("repo_key") or "")
    errors: list[str] = []

    evidence = {
        "repo_key": repo_key,
        "repo_url": item.get("repo_url", ""),
        "local_path": str(local_path),
        "checkout_sha": payload.get("checkout_sha", ""),
        "disk_bytes": as_int(payload.get("disk_bytes")),
        "file_count": as_int(payload.get("file_count")),
        "tree_paths": [],
        "hits": [],
        "matched_terms": [],
        "top_hit_paths": [],
        "install_files": [],
        "interface_files": [],
        "test_example_paths": [],
        "large_files": [],
        "scanned_file_count": 0,
        "scanned_bytes": 0,
        "skipped_large_file_count": 0,
        "skipped_binary_file_count": 0,
        "skipped_symlink_file_count": 0,
        "skipped_dir_count": 0,
        "truncated": False,
        "errors": errors,
    }

    if not local_path.exists() or not local_path.is_dir():
        errors.append("missing_local_path")
        return evidence

    collector = EvidenceCollector(config)
    try:
        walk_repository(local_path, collector)
    except OSError as exc:
        errors.append(f"walk_error:{exc}")

    evidence.update(collector.to_evidence())
    return evidence


class EvidenceCollector:
    def __init__(self, config: LocalScannerConfig) -> None:
        self.config = config
        self.skip_dirs = {part.lower() for part in config.skip_dirs}
        self.tree_paths: list[str] = []
        self.hits: list[LocalHit] = []
        self.install_files: set[str] = set()
        self.interface_files: set[str] = set()
        self.test_example_paths: set[str] = set()
        self.large_files: list[str] = []
        self.scanned_file_count = 0
        self.scanned_bytes = 0
        self.skipped_large_file_count = 0
        self.skipped_binary_file_count = 0
        self.skipped_symlink_file_count = 0
        self.skipped_dir_count = 0
        self.truncated = False
        self.errors: list[str] = []

    def should_skip_dir(self, name: str) -> bool:
        return name.lower() in self.skip_dirs

    def record_file(self, root: Path, path: Path) -> None:
        if self.truncated:
            return
        if path.is_symlink():
            self.skipped_symlink_file_count += 1
            return
        try:
            stat = path.stat()
        except OSError as exc:
            self.errors.append(f"stat_error:{relative_path(root, path)}:{exc}")
            return
        if not path.is_file():
            return

        rel_path = relative_path(root, path)
        self.tree_paths.append(rel_path)
        self.scanned_file_count += 1
        self.scanned_bytes += stat.st_size
        self.record_path_signals(rel_path, stat.st_size)

        if self.scanned_file_count >= self.config.max_files_per_repo:
            self.errors.append("max_files_per_repo_exceeded")
            self.truncated = True
            return
        if self.scanned_bytes >= self.config.max_repo_bytes:
            self.errors.append("max_repo_bytes_exceeded")
            self.truncated = True
            return
        if stat.st_size > self.config.max_file_size_bytes:
            self.skipped_large_file_count += 1
            self.add_limited(self.large_files, rel_path, self.config.max_paths_per_group)
            return
        if not is_text_candidate(rel_path):
            self.skipped_binary_file_count += 1
            return
        self.scan_text_file(path, rel_path)

    def record_path_signals(self, rel_path: str, size: int) -> None:
        normalized = normalize_path(rel_path)
        name = PurePosixPath(normalized).name
        suffix = lower_suffix(normalized)
        if name in INSTALL_FILES:
            self.install_files.add(rel_path)
        if name in INTERFACE_SCRIPT_NAMES:
            self.interface_files.add(rel_path)
        if has_path_part(normalized, {"cli", "cmd", "commands"}) and suffix in SOURCE_SUFFIXES:
            self.interface_files.add(rel_path)
        if has_path_part(normalized, TEST_EXAMPLE_PARTS):
            self.test_example_paths.add(rel_path)
        if suffix == ".ipynb":
            self.test_example_paths.add(rel_path)
        if suffix in {".cu", ".cuh"}:
            self.add_hit(LocalHit(group="cuda", term=f"extension:{suffix[1:]}", path=rel_path))
        if size >= self.config.max_file_size_bytes:
            self.add_limited(self.large_files, rel_path, self.config.max_paths_per_group)

    def scan_text_file(self, path: Path, rel_path: str) -> None:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            self.errors.append(f"read_error:{rel_path}:{exc}")
            return
        if "\x00" in content[:4096]:
            self.skipped_binary_file_count += 1
            return
        for line_number, line in enumerate(content.splitlines(), start=1):
            for pattern in LOCAL_PATTERNS:
                if pattern.regex.search(line):
                    self.add_hit(LocalHit(group=pattern.group, term=pattern.term, path=rel_path, line=line_number))
            if len(self.hits) >= self.config.max_hits_per_repo:
                return

    def add_hit(self, hit: LocalHit) -> None:
        if len(self.hits) < self.config.max_hits_per_repo:
            self.hits.append(hit)

    def add_limited(self, values: list[str], value: str, limit: int) -> None:
        if value not in values and len(values) < limit:
            values.append(value)

    def to_evidence(self) -> dict[str, object]:
        matched_terms = sorted({hit.term for hit in self.hits})
        top_hit_paths = sorted({hit.path for hit in self.hits})[: self.config.max_paths_per_group]
        return {
            "tree_paths": sorted(set(self.tree_paths)),
            "hits": [
                {
                    "group": hit.group,
                    "term": hit.term,
                    "path": hit.path,
                    "line": hit.line,
                }
                for hit in self.hits
            ],
            "matched_terms": matched_terms,
            "top_hit_paths": top_hit_paths,
            "install_files": sorted(self.install_files)[: self.config.max_paths_per_group],
            "interface_files": sorted(self.interface_files)[: self.config.max_paths_per_group],
            "test_example_paths": sorted(self.test_example_paths)[: self.config.max_paths_per_group],
            "large_files": self.large_files,
            "scanned_file_count": self.scanned_file_count,
            "scanned_bytes": self.scanned_bytes,
            "skipped_large_file_count": self.skipped_large_file_count,
            "skipped_binary_file_count": self.skipped_binary_file_count,
            "skipped_symlink_file_count": self.skipped_symlink_file_count,
            "skipped_dir_count": self.skipped_dir_count,
            "truncated": self.truncated,
            "errors": self.errors,
        }


def walk_repository(root: Path, collector: EvidenceCollector) -> None:
    for current_root_text, dir_names, file_names in os.walk(root, topdown=True):
        current_root = Path(current_root_text)
        kept_dirs = []
        for dir_name in dir_names:
            dir_path = current_root / dir_name
            if dir_path.is_symlink() or collector.should_skip_dir(dir_name):
                collector.skipped_dir_count += 1
            else:
                kept_dirs.append(dir_name)
        dir_names[:] = kept_dirs
        for file_name in file_names:
            collector.record_file(root, current_root / file_name)
            if collector.truncated:
                return


def is_text_candidate(path: str) -> bool:
    suffix = lower_suffix(path)
    if suffix in TEXT_SUFFIXES:
        return True
    name = PurePosixPath(normalize_path(path)).name
    return name in INSTALL_FILES or name in {"dockerfile", "makefile"}


def path_category(path: str) -> str:
    normalized = normalize_path(path)
    parts = set(normalized.split("/"))
    suffix = lower_suffix(normalized)
    if parts & VENDOR_PARTS:
        return "vendor"
    if parts & DOC_PARTS or suffix in {".md", ".rst", ".txt"}:
        return "docs"
    if parts & TEST_EXAMPLE_PARTS or suffix == ".ipynb":
        return "test_example"
    if suffix in SOURCE_SUFFIXES:
        return "source"
    return "other"


def path_weight(path: str) -> float:
    return {
        "source": 1.0,
        "test_example": 0.5,
        "docs": 0.2,
        "vendor": 0.1,
        "other": 0.4,
    }[path_category(path)]


def normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/").lower().strip("/")


def has_path_part(path: str, parts: set[str]) -> bool:
    return bool(set(normalize_path(path).split("/")) & parts)


def lower_suffix(path: str) -> str:
    return PurePosixPath(path).suffix.lower()


def relative_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def as_int(value: object) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def hits_from_evidence(evidence: dict[str, object]) -> list[LocalHit]:
    hits = []
    for item in evidence.get("hits") or []:
        if not isinstance(item, dict):
            continue
        hits.append(
            LocalHit(
                group=str(item.get("group") or ""),
                term=str(item.get("term") or ""),
                path=str(item.get("path") or ""),
                line=as_int(item.get("line")),
            )
        )
    return hits


def strings_from_evidence(evidence: dict[str, object], key: str) -> list[str]:
    return [str(item) for item in evidence.get(key) or [] if str(item)]


def unique_sorted(values: Iterable[str]) -> list[str]:
    return sorted({value for value in values if value})
