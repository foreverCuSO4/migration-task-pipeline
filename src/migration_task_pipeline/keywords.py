"""Keyword matching helpers."""

from __future__ import annotations

import re
from collections.abc import Iterable


def normalize_keywords(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return ";".join(part.strip() for part in re.split(r"[,;]", value) if part.strip())
    if isinstance(value, Iterable):
        return ";".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def match_keywords(values: Iterable[object], keywords: Iterable[str]) -> list[str]:
    haystack = "\n".join(str(value or "") for value in values).lower()
    matched = []
    for keyword in keywords:
        normalized = keyword.strip().lower()
        if normalized and normalized in haystack:
            matched.append(keyword)
    return sorted(set(matched), key=lambda item: item.lower())

