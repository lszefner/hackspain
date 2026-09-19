"""Tiny stdlib template loader for webui/frontend -- no new dependency.

Uses string.Template ($placeholder) on cached file contents. Repeated rows
(table rows, check cards) are rendered per-item then joined by the caller
before substitution, same as a real template engine's loop would produce.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from string import Template

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "frontend" / "templates"


@lru_cache(maxsize=None)
def _load(name: str) -> Template:
    return Template((TEMPLATES_DIR / name).read_text("utf-8"))


def render(name: str, **values: str) -> str:
    return _load(name).safe_substitute(**values)
