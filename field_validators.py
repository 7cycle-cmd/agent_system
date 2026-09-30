"""Field-level validators for hard (non-LLM) payload validation.

Each validator returns None = pass, or an error string = fail.
These are dispatched from validate_task_payload() in skill_field_registry.py.
v1 scope: dispatch is HARDCODED to validate_phone (rule-driven dispatch from
field_tdd_rule is deferred until >1 field exists).
"""

from __future__ import annotations


def validate_phone(val) -> str | None:
    """Return None = pass; return error string = fail."""
    # type(True) is bool -> bool auto-rejected; do NOT use isinstance
    if type(val) is not int:
        return f"type mismatch: expect int, got {type(val).__name__}"
    # guard: rejects YAML octal/hex/leading-zero corruption
    if not (10_000_000 <= val <= 999_999_999_999_999):
        return "value out of range (8-15 digits)"
    return None