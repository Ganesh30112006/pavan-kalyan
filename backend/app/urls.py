"""URL parsing that never raises.

`urllib.parse.urlsplit` raises ValueError for some malformed input (for example an unclosed IPv6 bracket,
`https://[::1`), and so do the `.hostname` and `.port` properties of its result (a port out of range).
Addresses come from users and from provider responses, so every caller needs the "not a URL" case."""

from __future__ import annotations

from urllib.parse import SplitResult, urlsplit


def split_url(value: str) -> SplitResult | None:
    """The parsed URL, or None when it cannot be parsed. `.hostname` and `.port` are safe to read on the result."""
    try:
        parts = urlsplit(value)
        _ = parts.hostname, parts.port
    except ValueError:
        return None
    return parts
