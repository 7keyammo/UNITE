from __future__ import annotations

import json
import re
from typing import Any, Callable


# Sensor payload parsers are pure functions so they can be regression-tested
# without any hardware attached.

_NUMBER_RE = re.compile(r"^-?\d+(?:\.\d+)?$")

_MAX_FIELDS = 64
_MAX_VALUE_CHARS = 512


def _coerce(value: Any) -> Any:
    """Turn a textual reading into a number or bool when it clearly is one.

    Rule conditions compare numerically, so "23.5" has to become 23.5. Anything
    ambiguous stays a string rather than being guessed at.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if _NUMBER_RE.match(text):
        number = float(text)
        return int(number) if number.is_integer() and "." not in text else number
    lowered = text.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    return text[:_MAX_VALUE_CHARS]


def _limit(fields: dict[str, Any]) -> dict[str, Any]:
    return dict(list(fields.items())[:_MAX_FIELDS])


def parse_line(raw: str, *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Treat the payload as a single opaque reading."""
    text = str(raw or "").strip()
    return {"value": _coerce(text), "raw": text[:_MAX_VALUE_CHARS]}


def parse_json(raw: str, *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    text = str(raw or "").strip()
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Payload is not valid JSON: {exc}") from exc
    if isinstance(data, dict):
        return _limit({str(key): _coerce(value) for key, value in data.items()})
    return {"value": data}


def parse_csv(raw: str, *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Split on a delimiter, naming columns from config when provided."""
    settings = dict(config or {})
    delimiter = str(settings.get("delimiter", ","))
    columns = [str(name) for name in (settings.get("columns") or [])]
    parts = [part.strip() for part in str(raw or "").split(delimiter)]
    fields: dict[str, Any] = {}
    for index, part in enumerate(parts[:_MAX_FIELDS]):
        key = columns[index] if index < len(columns) else f"field_{index}"
        fields[key] = _coerce(part)
    return fields


def parse_keyvalue(raw: str, *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Parse 'temp=23.5 humidity=41' style payloads."""
    settings = dict(config or {})
    pair_sep = str(settings.get("pair_separator", "="))
    fields: dict[str, Any] = {}
    for token in re.split(r"[\s,;]+", str(raw or "").strip()):
        if not token or pair_sep not in token:
            continue
        key, _, value = token.partition(pair_sep)
        key = key.strip()
        if key:
            fields[key] = _coerce(value)
    if not fields:
        raise ValueError("No key/value pairs found in payload")
    return _limit(fields)


PARSERS: dict[str, Callable[..., dict[str, Any]]] = {
    "line": parse_line,
    "raw": parse_line,
    "json": parse_json,
    "csv": parse_csv,
    "keyvalue": parse_keyvalue,
}


def get_parser(name: str) -> Callable[..., dict[str, Any]]:
    key = str(name or "line").strip().lower()
    parser = PARSERS.get(key)
    if parser is None:
        raise ValueError(f"Unknown parser '{name}'. Available: {', '.join(sorted(PARSERS))}")
    return parser


def parse(raw: str, parser: str = "line", *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    return get_parser(parser)(raw, config=config)
