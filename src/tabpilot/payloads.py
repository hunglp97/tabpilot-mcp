"""Load and parameterise the JavaScript payloads.

The payloads live as real ``.js`` files rather than Python string literals so
they can be linted, diffed and syntax-checked on their own. Each file holds a
single function expression; calling it with JSON-encoded options is what keeps
argument passing free of escaping bugs.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

JS_DIR = Path(__file__).parent / "js"

_MATRIX_MARKER = "/* MATRIX_COMMON */"
_LOCATE_MARKER = "(LOCATE)"


@lru_cache(maxsize=None)
def load(name: str) -> str:
    """Return the JS source for ``name`` with its includes resolved."""
    path = JS_DIR / f"{name}.js"
    try:
        source = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:  # pragma: no cover - packaging error
        raise RuntimeError(f"Missing JS payload {path}") from exc

    if _MATRIX_MARKER in source:
        source = source.replace(_MATRIX_MARKER, (JS_DIR / "matrix_common.js").read_text(encoding="utf-8"))
    if _LOCATE_MARKER in source:
        source = source.replace(_LOCATE_MARKER, f"({load('locate')})")
    return source


#: Prefixed to every built expression. It names the payload in CDP traces and in
#: any page error, which is the difference between "something threw in an eval"
#: and knowing which payload threw.
MARKER = "/*tabpilot:{name}*/"


def call(name: str, options: dict[str, Any] | None = None) -> str:
    """Build the expression that invokes payload ``name`` with ``options``."""
    encoded = json.dumps(options or {}, ensure_ascii=False)
    return f"{MARKER.format(name=name)}({load(name)})({encoded})"


def payload_name(expression: str) -> str | None:
    """Recover the payload name from an expression built by :func:`call`."""
    if not expression.startswith("/*tabpilot:"):
        return None
    end = expression.find("*/")
    return expression[len("/*tabpilot:"):end] if end != -1 else None
