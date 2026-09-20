"""The document from `model.build`, put into one HTML file that needs nothing else.

No server, no CDN, no network: the file is handed to people who have neither the tools nor a route to the
database, and it has to open from a mail attachment. So the data is embedded, and the page is plain JavaScript.

## The data is text the page never trusts

It holds SQL written by other people. Embedded in a `<script>`, a `</script>` inside a string literal would end
the block and the rest would be parsed as HTML; so `<`, `>` and `&` go in as `\\uXXXX` escapes, which JSON reads
back as the same characters. On the other side the page builds every node with `textContent` -- nothing from the
data is ever parsed as markup.
"""

from __future__ import annotations

import json
from pathlib import Path

TEMPLATE = Path(__file__).with_name("template.html")
PLACEHOLDER = "/*__DATA__*/"
_ESCAPES = {"<": "\\u003c", ">": "\\u003e", "&": "\\u0026", " ": "\\u2028", " ": "\\u2029"}


def embed(data: dict) -> str:
    text = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "".join(_ESCAPES.get(ch, ch) for ch in text)


def extract(html: str) -> dict:
    """The embedded document, read back. What a test -- or anyone doubting the page -- checks against."""
    start = html.index('<script id="data" type="application/json">') + len('<script id="data" type="application/json">')
    return json.loads(html[start:html.index("</script>", start)])


def render(data: dict) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count(PLACEHOLDER) != 1:
        raise RuntimeError(f"{TEMPLATE} must hold {PLACEHOLDER} exactly once")
    return template.replace(PLACEHOLDER, embed(data))


def write(data: dict, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(data), encoding="utf-8")
    return path
