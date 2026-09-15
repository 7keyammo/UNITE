from __future__ import annotations

import re
from typing import Any

from events.models import Event


class ConditionError(ValueError):
    """Raised when a rule condition is malformed at definition time."""


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _compare(actual: Any, expected: Any, op: str) -> bool:
    """Numeric comparison that fails closed on non-numeric input.

    A threshold rule that cannot read a number must not fire; treating an
    unparseable reading as "below threshold" would invent an observation.
    """
    left, right = _as_number(actual), _as_number(expected)
    if left is None or right is None:
        return False
    if op == "lt":
        return left < right
    if op == "lte":
        return left <= right
    if op == "gt":
        return left > right
    return left >= right


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _eq(actual: Any, expected: Any) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return bool(actual) == bool(expected)
    left, right = _as_number(actual), _as_number(expected)
    if left is not None and right is not None:
        return left == right
    return _text(actual).strip().lower() == _text(expected).strip().lower()


def _as_list(expected: Any) -> list[Any]:
    if isinstance(expected, (list, tuple, set)):
        return list(expected)
    return [expected]


OPERATORS: dict[str, Any] = {
    "eq": lambda a, e: _eq(a, e),
    "ne": lambda a, e: not _eq(a, e),
    "lt": lambda a, e: _compare(a, e, "lt"),
    "lte": lambda a, e: _compare(a, e, "lte"),
    "gt": lambda a, e: _compare(a, e, "gt"),
    "gte": lambda a, e: _compare(a, e, "gte"),
    "contains": lambda a, e: _text(e).lower() in _text(a).lower(),
    "not_contains": lambda a, e: _text(e).lower() not in _text(a).lower(),
    "startswith": lambda a, e: _text(a).lower().startswith(_text(e).lower()),
    "endswith": lambda a, e: _text(a).lower().endswith(_text(e).lower()),
    "in": lambda a, e: any(_eq(a, item) for item in _as_list(e)),
    "not_in": lambda a, e: not any(_eq(a, item) for item in _as_list(e)),
    "exists": lambda a, e: a is not None,
    "missing": lambda a, e: a is None,
    "truthy": lambda a, e: bool(a),
    "falsy": lambda a, e: not bool(a),
}

# Regex is applied to a bounded string with a pre-compiled pattern so a rule
# cannot turn into an expensive scan over arbitrary event text.
_MAX_MATCH_CHARS = 4096

VALUELESS_OPERATORS = {"exists", "missing", "truthy", "falsy"}


def validate_condition(condition: Any) -> dict[str, Any]:
    """Normalize and validate one condition, raising on anything malformed.

    Validation happens when a rule is defined, not when an event arrives, so a
    broken rule can never silently stop matching in production.
    """
    if not isinstance(condition, dict):
        raise ConditionError("Each condition must be an object")
    path = str(condition.get("path") or condition.get("attribute") or "").strip()
    if not path:
        raise ConditionError("Condition requires a 'path'")
    op = str(condition.get("op") or condition.get("operator") or "eq").strip().lower()
    if op not in OPERATORS and op != "matches":
        raise ConditionError(f"Unsupported condition operator: {op}")
    normalized: dict[str, Any] = {"path": path, "op": op}
    if op == "matches":
        pattern = str(condition.get("value") or "")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConditionError(f"Invalid regular expression: {exc}") from exc
        normalized["value"] = pattern
    elif op not in VALUELESS_OPERATORS:
        if "value" not in condition:
            raise ConditionError(f"Operator '{op}' requires a 'value'")
        normalized["value"] = condition["value"]
    return normalized


def evaluate_condition(event: Event, condition: dict[str, Any]) -> bool:
    op = condition.get("op", "eq")
    actual = event.get(condition.get("path", ""))
    expected = condition.get("value")
    if op == "matches":
        try:
            return re.search(str(expected), _text(actual)[:_MAX_MATCH_CHARS]) is not None
        except re.error:
            return False
    handler = OPERATORS.get(op)
    if handler is None:
        return False
    try:
        return bool(handler(actual, expected))
    except Exception:
        # An operator that cannot decide is not a match.
        return False


def evaluate_all(event: Event, conditions: list[dict[str, Any]]) -> bool:
    """All conditions must hold. An empty condition list matches."""
    return all(evaluate_condition(event, condition) for condition in conditions)
