"""Optional LLM explanation hook — SCAFFOLD ONLY, OFF by default, not wired.

The deterministic templates (why/templates.py) are always the source of truth; an LLM,
if ever enabled, may only *rephrase* the same metrics — never replace the math.
"""
from __future__ import annotations

ENABLED = False


def explain(context: dict) -> str | None:
    """Return an LLM-rephrased summary, or None when disabled (the v1 default)."""
    if not ENABLED:
        return None
    raise NotImplementedError("LLM explanation hook is scaffolded but not wired in v1")
