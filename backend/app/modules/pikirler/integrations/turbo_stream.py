"""Decoder for React Router "single fetch" answers (``*.data``, ``text/x-script``).

makalam.com delivers its route data this way. The payload's first line is a
flat JSON array: objects are ``{"_<key index>": <value index>}``, arrays hold
value indices, negative indices are sentinels (null, undefined, NaN...).
Only that first line is read; deferred promises are not used by the routes
Pikirler needs.
"""

from __future__ import annotations

import json
from typing import Any

_NEG_ZERO = -4
_SENTINELS = {-1, -2, -3, -4, -5, -6, -7}
_MAX_DEPTH = 200


class Redirect(Exception):
    """A redirect answer (a result, not a failure)."""

    def __init__(self, target: str) -> None:
        super().__init__(target)
        self.target = target


def decode(text: str) -> Any:
    """The decoded value. Raises ``Redirect`` for a redirect answer and
    ``ValueError`` for anything that is not turbo-stream."""
    line = next((part for part in text.split("\n") if part.strip()), "")
    table = json.loads(line)
    if not isinstance(table, list) or not table:
        raise ValueError("not a turbo-stream answer")
    cache: dict[int, Any] = {}

    def value(index: Any, depth: int = 0) -> Any:
        if not isinstance(index, int) or isinstance(index, bool):
            return None
        if index in _SENTINELS:
            return 0 if index == _NEG_ZERO else None
        if not 0 <= index < len(table) or depth > _MAX_DEPTH:
            return None
        if index in cache:
            return cache[index]
        raw = table[index]
        result: Any
        if isinstance(raw, dict):
            result = {}
            for key, ref in raw.items():
                if key.startswith("_") and key[1:].isdigit():
                    key_index = int(key[1:])
                    if 0 <= key_index < len(table) and isinstance(table[key_index], str):
                        result[table[key_index]] = value(ref, depth + 1)
        elif isinstance(raw, list):
            if raw and isinstance(raw[0], str):
                tag = raw[0]
                if tag == "SingleFetchRedirect" and len(raw) > 1:
                    result = {"__redirect": value(raw[1], depth + 1)}
                elif tag == "D" and len(raw) > 1:  # a date, as epoch milliseconds
                    result = raw[1]
                else:  # promises, maps, sets, errors...: not needed here
                    result = None
            else:
                result = [value(item, depth + 1) for item in raw]
        else:
            result = raw
        cache[index] = result
        return result

    root = value(0)
    if isinstance(root, dict) and isinstance(root.get("__redirect"), dict):
        target = root["__redirect"].get("redirect")
        raise Redirect(target if isinstance(target, str) else "/")
    return root


def route_data(root: Any, route: str) -> dict[str, Any] | None:
    """``root[route]["data"]`` when it is an object."""
    entry = root.get(route) if isinstance(root, dict) else None
    data = entry.get("data") if isinstance(entry, dict) else None
    return data if isinstance(data, dict) else None


def find(value: Any, keys: set[str], depth: int = 0) -> dict[str, Any] | None:
    """The first object (depth-first) that has any of ``keys``."""
    if depth > 20:
        return None
    if isinstance(value, dict):
        if keys & value.keys():
            return value
        children: Any = value.values()
    elif isinstance(value, list):
        children = value
    else:
        return None
    for child in children:
        found = find(child, keys, depth + 1)
        if found is not None:
            return found
    return None
