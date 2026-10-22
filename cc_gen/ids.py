"""Stable migration identifiers.

Canvas stores every imported object's identifier as its ``migration_id``. Re-importing a
package with the same identifiers updates objects in place instead of duplicating them, so
identifiers are derived deterministically from ``(namespace, kind, key)`` and never from build order.

The namespace comes from the theme (``theme.yaml`` ``namespace``), so two themes imported into one
Canvas account never collide. It is scoped with ``use_namespace`` rather than set globally, so a
process can load several themes (tests, ``cc matrix --compare``) without mixing their ids.
"""

import hashlib
import re
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar

_namespace: ContextVar[str | None] = ContextVar("cc_namespace", default=None)


@contextmanager
def use_namespace(ns: str):
    token = _namespace.set(ns)
    try:
        yield
    finally:
        _namespace.reset(token)


def mid(kind: str, key: str) -> str:
    ns = _namespace.get()
    if ns is None:
        raise RuntimeError("mid() called outside use_namespace(); load/build the course through its theme")
    return "g" + hashlib.md5(f"{ns}:{kind}:{key}".encode()).hexdigest()


def slugify(text: str, max_len: int = 100) -> str:
    """Canvas-style URL slug (mirrors ``String#to_url`` closely enough for page/file names)."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return (text or "untitled")[:max_len].rstrip("-")
