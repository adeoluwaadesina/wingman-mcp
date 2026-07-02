"""Bundles UI static assets into a single self-contained HTML resource.

MCP Apps GA (SEP-1865) uses ``text/html;profile=mcp-app`` MIME-typed resources
rendered in a sandboxed iframe. The resource is **static and predeclared** -
it carries no plan data. Plan state reaches the iframe at runtime via the MCP
Apps render-data channel (the ``toolresult`` notification carrying the calling
tool's ``structuredContent``), per the ext-apps GA spec.

We inline the MCP Apps SDK, CSS, the JS controller, and Sortable.js into one
self-contained HTML string so the iframe needs zero external fetches.
"""
from __future__ import annotations

import base64
import re
import time
from functools import lru_cache
from pathlib import Path

from mcp.types import Icon

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Build marker - captured once at module import (i.e. once per server-subprocess
# start). The footer of the rendered panel shows this value verbatim. If you
# refresh the host and see the same build number after editing Python or static
# files, the subprocess wasn't restarted (or the host is serving a cached
# iframe). See README "Development troubleshooting".
BUILD_TIMESTAMP = int(time.time())

# The MIME type required by MCP Apps GA (SEP-1865, 2026-01-26). Claude Desktop's
# initialize handshake declares this exact string as the only supported UI MIME.
MCP_UI_MIME_TYPE = "text/html;profile=mcp-app"

# Static, predeclared resource URI. No template parameters -> appears in
# resources/list (not resources/templates/list) so MCP-Apps hosts can
# enumerate and prefetch it before rendering.
PANEL_URI = "ui://wingman/panel"

# Bundled SDK: @modelcontextprotocol/ext-apps 1.7.2 (app-with-deps build), MIT.
_SDK_EXPORT_RE = re.compile(r"export\s*\{([^}]*)\}\s*;?\s*$")


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def icon_svg() -> str:
    """The raw Wingman brand mark ("Manifest") SVG."""
    return _read("icon.svg")


def icon_data_uri() -> str:
    b64 = base64.b64encode(icon_svg().encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


def server_icons(base_url: str | None = None) -> list[Icon]:
    """The Wingman brand mark, declared in the MCP server's Implementation info
    so hosts render it in place of the fallback initial next to tool calls.

    When ``base_url`` is given (the hosted cloud server), the icon is referenced
    by its public HTTPS URL - more widely supported by clients than a data URI.
    Otherwise (the local stdio server, which has no HTTP host) it is inlined as
    a self-contained SVG data URI.
    """
    if base_url:
        src = base_url.rstrip("/") + "/icon.svg"
    else:
        src = icon_data_uri()
    return [Icon(src=src, mimeType="image/svg+xml", sizes=["any"])]


def _sdk_as_global() -> str:
    """Load the ext-apps ESM bundle and rewrite its single trailing ``export{}``
    into a ``globalThis.WingmanMCP = {...}`` assignment so the bundle can run as
    a classic ``<script>`` and expose ``App`` to our controller.

    The alias map is derived from the export statement itself (``X as App`` ->
    ``App: X``), so this stays correct across SDK minified-name churn.
    """
    src = _read("mcp-app.js")
    match = _SDK_EXPORT_RE.search(src)
    if not match:
        raise RuntimeError("Could not locate the ext-apps export statement to globalize")
    pairs = []
    for entry in match.group(1).split(","):
        entry = entry.strip()
        if not entry:
            continue
        if " as " in entry:
            local, exported = (p.strip() for p in entry.split(" as ", 1))
        else:
            local = exported = entry
        pairs.append(f"{exported}:{local}")
    replacement = "globalThis.WingmanMCP={" + ",".join(pairs) + "};"
    return src[: match.start()] + replacement


@lru_cache(maxsize=1)
def _panel_html() -> str:
    """Assemble the fully self-contained panel HTML (read + inlined once)."""
    html_src = _read("index.html")
    css = _read("styles.css")
    sdk = _sdk_as_global()
    sortable = _read("sortable.min.js")
    app_js = _read("app.js")
    return (
        html_src.replace("/*__WINGMAN_CSS__*/", css)
        .replace("/*__WINGMAN_MCP_SDK__*/", sdk)
        .replace("/*__WINGMAN_SORTABLE__*/", sortable)
        .replace("/*__WINGMAN_JS__*/", app_js)
        .replace("<!--__WINGMAN_BUILD__-->", str(BUILD_TIMESTAMP))
    )


def render_panel() -> str:
    """Return the static panel HTML. No plan data is baked in."""
    return _panel_html()
