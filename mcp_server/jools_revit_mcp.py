"""///Summary: MCP server that lets Claude dimension Revit ceiling plans.

A thin translator: each MCP tool is one HTTP call to the Jools pyRevit Routes
API (Jools.extension/startup.py), which runs inside Revit. All Revit logic and
geometry live there, in Jools.extension/lib/rcpdims.py; nothing here touches
the Revit API.

Runs outside Revit on ordinary CPython, in the venv next to this file:

    mcp_server\\.venv\\Scripts\\python.exe mcp_server\\jools_revit_mcp.py

Written against the `mcp` SDK 2.x (MCPServer, formerly FastMCP). In 2.x an
ordinary exception raised in a tool reaches the client as a bare failure with
its text withheld, so every message meant for the user is raised as ToolError.

Environment:
    JOOLS_ROUTES_URL   base URL of the Routes API, e.g. http://127.0.0.1:48884/jools.
                       Unset (the normal case): the port is found by scanning,
                       see _discover_base_url.
"""

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations


FIXED_BASE_URL = os.environ.get("JOOLS_ROUTES_URL", "").rstrip("/") or None

# pyRevit 7 starts at the configured port (48884) and counts up past any port a
# registered server still holds (routes/server/serverinfo.py
# _get_next_available_port), so a stale registration after a Reload, or a
# second Revit, moves it. Observed on 48885.
ROUTES_HOST = "127.0.0.1"
PORT_FIRST = 48884
PORT_COUNT = 10

_base_url = FIXED_BASE_URL

# Generous, because pyRevit only runs the request when Revit is idle. A
# request that times out may still run later, which is why the timeout message
# tells Claude to check before retrying a write.
TIMEOUT_SECONDS = 120

# The user's drafting standard: dimensions tie to "GC Control Line" grids;
# the 1/4" bubble grid types are ignored.
DEFAULT_GRID_TYPES = ["GC Control Line"]

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False)
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False)
DELETES = ToolAnnotations(read_only_hint=False, destructive_hint=True)

INSTRUCTIONS = """\
Dimension fixtures in Revit reflected ceiling plans (RCPs). Fixtures and grids
may live in the open model or in Revit links; the tools treat both the same.

Workflow:
1. rcp_list_items for the view, one category at a time on a big view. Each
   row is [axis, type, at, from, to, keys, grid_before, grid_after]; type
   indexes "types"; grid_before/after are [distance_ft, "names"]. Rows only
   contain fixtures that can be dimensioned along that row's axis.
2. The user's drafting convention: in each row, tie the FIRST fixture to the
   nearest grid, then dimension fixture-to-fixture spacing. That is ONE
   rcp_dimension call per row, with the row's axis and keys = [grid key] +
   the row's keys. Use the nearer of grid_before / grid_after; look the key up
   in "grids" by name.
3. Rows tie only to "GC Control Line" grids by default (the user's standard).
   If a note says none are visible, tell the user rather than switching type.
   A name like "46/58" means two such grids share a line; ask which to use.
4. Use dry_run=true to preview when unsure; it returns real segment lengths and
   leaves nothing in the model.
5. "loose" fixtures belong to no row; ask the user how to handle them.
   "stacked" pairs sit at the same spot (likely duplicates); report them.
   "skipped" fixtures cannot be dimensioned to a centre; report them.
6. Placement: offset_in is paper inches from the fixtures (default 0.375);
   positive puts the line above an axis-"x" row or right of an axis-"y" column.

Only dimensions created through these tools since Revit started can be removed
with rcp_delete_dimensions. Report the dimension ids you create.

Route fabrication piping (proof of concept):
1. fab_info to confirm a fabrication configuration is loaded.
2. Ask the user to select the two parts to join, then fab_list_connectors
   with selected=true. Pick the open ends facing each other.
3. fab_route with dry_run=true. Report `result`; on failure relay `hint`.
4. Only after a Success dry run and the user's go-ahead, repeat with dry_run=false.
Revit routes only what lines up as it sits: no pathfinding, no clash avoidance.
"""

server = MCPServer(name="jools-revit", instructions=INSTRUCTIONS)


def _discover_base_url():
    """///Summary: The Routes base URL of the first port with a listener, or None.

    A bare TCP connect rather than a /ping/ request: ping waits for Revit to be
    idle, so a busy Revit would look absent. Only localhost is scanned.
    """
    for port in range(PORT_FIRST, PORT_FIRST + PORT_COUNT):
        try:
            with socket.create_connection((ROUTES_HOST, port), timeout=0.5):
                return "http://{}:{}/jools".format(ROUTES_HOST, port)
        except OSError:
            continue
    return None


def _call(method, path, query=None, body=None, _retried=False):
    """///Summary: One request to the Routes API, as parsed JSON.

    Finds the port on first use and again whenever the connection is refused.
    A refused connection means nothing was sent, so retrying once is safe even
    for a request that writes.
    """
    global _base_url
    if _base_url is None:
        _base_url = _discover_base_url()
        if _base_url is None:
            raise ToolError(
                "Revit is not answering on ports {}-{}. Open Revit with a project, "
                "and check that pyRevit Routes is enabled (pyRevit Settings > "
                "Routes) and that pyRevit loaded the Jools extension.".format(
                    PORT_FIRST, PORT_FIRST + PORT_COUNT - 1))
    try:
        return _send(_base_url, method, path, query, body)
    except ConnectionRefusedError:
        if FIXED_BASE_URL:
            raise ToolError(
                "Revit is not answering at {} (set by JOOLS_ROUTES_URL). Unset it "
                "to let the port be found automatically.".format(FIXED_BASE_URL))
        _base_url = None
        if _retried:
            raise ToolError(
                "Revit stopped answering mid-request. Check Revit is still open "
                "and retry.")
        # The server moved, usually after a pyRevit Reload.
        return _call(method, path, query, body, _retried=True)


def _send(base_url, method, path, query, body):
    """///Summary: Send one request to base_url; ConnectionRefusedError passes through.

    Turns every other failure into a ToolError carrying what went wrong and what
    to do about it, so the message survives the MCP SDK and reaches the user.
    """
    url = base_url + path
    if query:
        cleaned = {k: v for k, v in query.items() if v is not None}
        if cleaned:
            url += "?" + urllib.parse.urlencode(cleaned)

    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as error:
        raise ToolError(_http_error_text(error)) from error
    except (socket.timeout, TimeoutError) as error:
        raise ToolError(
            "Revit did not answer within {} s. It may be busy, or a dialog may be "
            "open; finish or cancel it in Revit. The request may still run once "
            "Revit is free, so check the view before retrying anything that "
            "creates or deletes.".format(TIMEOUT_SECONDS)) from error
    except urllib.error.URLError as error:
        if isinstance(error.reason, ConnectionRefusedError):
            raise error.reason
        raise ToolError(
            "Revit is not answering at {}. Open Revit with a project, and check "
            "that pyRevit Routes is enabled (pyRevit Settings > Routes) and that "
            "pyRevit loaded the Jools extension. Detail: {}".format(
                base_url, error.reason)) from error


def _compact(data):
    """///Summary: Tool output as compact JSON text.

    Returning a dict makes the SDK pretty-print it with indentation; the row
    listing is mostly short arrays, and one item per indented line roughly
    doubled its size, which Claude pays for in tokens.
    """
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def _http_error_text(error):
    """///Summary: The user-facing text from a Routes error response.

    Jools routes answer 400 with {"error": "..."}. Anything pyRevit itself
    raised comes back as {"exception": {"message": "..."}}.
    """
    try:
        payload = json.loads(error.read().decode("utf-8"))
    except Exception:
        payload = None
    if isinstance(payload, dict):
        if "error" in payload:
            return str(payload["error"])
        exception = payload.get("exception")
        if isinstance(exception, dict) and "message" in exception:
            message = str(exception["message"])
            # pyRevit answers an unregistered route with HTTP 500, not 404
            # (observed): the usual cause is startup.py changed since Revit
            # loaded it.
            if "RouteHandlerNotDefinedException" in message:
                return ("This Revit session does not have that Jools endpoint yet. "
                        "Click pyRevit > Reload in Revit so startup.py re-registers "
                        "the routes, then retry.")
            return "Revit raised an error: {}".format(message.split("\n")[0])
    return "Routes API answered HTTP {}.".format(error.code)


@server.tool(annotations=READ_ONLY, structured_output=False)
def revit_ping() -> str:
    """Check the connection to Revit. Returns the open document and active view."""
    return _compact(_call("GET", "/ping/"))


@server.tool(annotations=READ_ONLY, structured_output=False)
def rcp_audit(view_id: int | None = None) -> str:
    """Report, per fixture family in a ceiling plan, whether it can be dimensioned
    to its centre (verdict: center, partial, edges, none). Host and linked.
    view_id: the view to audit; omit for the active view."""
    return _compact(_call("GET", "/rcp/audit/", query={"view_id": view_id}))


@server.tool(annotations=READ_ONLY, structured_output=False)
def rcp_list_items(view_id: int | None = None,
                   categories: list[str] | None = None,
                   detail: str = "rows",
                   max_gap_ft: float | None = None,
                   grid_types: list[str] | None = None) -> str:
    """List the fixtures and grids a plan view shows, host and linked, with the
    keys rcp_dimension takes. Default output groups fixtures into rows (axis x)
    and columns (axis y) of one type; arrays are described by the *_fields
    entries. Positions are view-aligned feet (u = screen x, v = screen y).
    view_id: omit for the active view.
    categories: any of lighting, air_terminals, sprinklers, devices (default:
      all). A big view is large; ask for one category at a time.
    detail: "rows" (compact) or "full" (one entry per fixture; large).
    max_gap_ft: split a row where fixtures are further apart than this (default 12).
    grid_types: grid type names rows may tie to. Default: the user's
      ["GC Control Line"]; pass [] to include every grid type."""
    if grid_types is None:
        grid_types = DEFAULT_GRID_TYPES
    return _compact(_call("GET", "/rcp/items/", query={
        "view_id": view_id,
        "categories": ",".join(categories) if categories else None,
        "detail": detail,
        "max_gap": max_gap_ft,
        "grid_types": ",".join(grid_types) if grid_types else None,
    }))


@server.tool(annotations=WRITES, structured_output=False)
def rcp_dimension(keys: list[str],
                  axis: str,
                  offset_in: float = 0.375,
                  view_id: int | None = None,
                  dimension_type: str | None = None,
                  dry_run: bool = False) -> str:
    """Create one dimension string through fixtures and grids (host or linked),
    measured to each fixture's centre plane. Key order does not matter.
    keys: item keys from rcp_list_items (at least two, at least one fixture).
    axis: "x" = horizontal string, "y" = vertical string (on screen).
    offset_in: paper inches from the fixtures; + is up (x) or right (y).
    view_id: omit for the active view.
    dimension_type: exact name of a linear dimension type; omit for the default.
    dry_run: true = measure and roll back, nothing kept.
    Returns the dimension id, the order used, and each segment length."""
    return _compact(_call("POST", "/rcp/dimension/", body={
        "keys": keys,
        "axis": axis,
        "offset_in": offset_in,
        "view_id": view_id,
        "dimension_type": dimension_type,
        "dry_run": dry_run,
    }))


@server.tool(annotations=DELETES, structured_output=False)
def rcp_delete_dimensions(ids: list[int]) -> str:
    """Delete dimensions that rcp_dimension created since Revit started. Any other
    id is refused, never deleted."""
    return _compact(_call("POST", "/rcp/delete/", body={"ids": ids}))


@server.tool(annotations=READ_ONLY, structured_output=False)
def fab_info(service_id: int | None = None) -> str:
    """List the fabrication services loaded in the project (id, name, domain,
    palette/button counts, parts already placed). Call this first: it also
    fails with instructions if no fabrication configuration is loaded.
    service_id: return that one service's palettes and buttons in full."""
    return _compact(_call("GET", "/fab/info/", query={"service_id": service_id}))


@server.tool(annotations=READ_ONLY, structured_output=False)
def fab_list_connectors(selected: bool = False,
                        element_ids: list[int] | None = None,
                        domain: str | None = None,
                        view_id: int | None = None,
                        include_connected: bool = False) -> str:
    """List open connectors on fabrication parts (and on equipment when selected
    or named), with the keys fab_route takes. Each row has `stretchable`: a route
    can only START at a fitting's open end (not a straight, tap or hanger); any
    open connector can be the END.
    selected: use what is selected in Revit right now (best way to pick two ends).
    element_ids: connectors of these elements only.
    domain: "piping" or "hvac"; default both.
    view_id: otherwise every fabrication part visible in this view (active if omitted).
    include_connected: also list connectors that are already joined."""
    return _compact(_call("GET", "/fab/connectors/", query={
        "selected": 1 if selected else None,
        "element_ids": ",".join(str(i) for i in element_ids) if element_ids else None,
        "domain": domain,
        "view_id": view_id,
        "include_connected": 1 if include_connected else None,
    }))


@server.tool(annotations=WRITES, structured_output=False)
def fab_route(from_key: str, to_key: str, dry_run: bool = True) -> str:
    """Route a run from one open connector to another using Revit's own Route and
    Fill engine (StretchAndFit). It fills straights and fittings from the part's
    service; it does NOT pathfind or avoid clashes, so it succeeds only when the
    two ends can be joined as they sit. On failure, `hint` says what to move.
    from_key: connector key on a fabrication FITTING (stretchable: true).
    to_key: any open connector in the same domain.
    dry_run: true (default) = run and roll back; set false only after a
    dry run returned result "Success" and the user agrees.
    Returns result, kept, and new_part_ids."""
    return _compact(_call("POST", "/fab/route/", body={
        "from_key": from_key,
        "to_key": to_key,
        "dry_run": dry_run,
    }))


@server.tool(annotations=READ_ONLY, structured_output=False)
def section_inspect(section_id: int | None = None,
                    element_id: int | None = None,
                    link_id: int | None = None,
                    plan_view_id: int | None = None,
                    category: str | None = None) -> str:
    """Measure a section view against the element it was cut from. Returns the
    section's frame and crop box (local and world), all its parameters, the
    element's world box/location/connectors, `margins` (how far the crop extends
    past the element left/right/below/above/depth, in the section's frame), the
    plan's view range, and every level. All lengths in feet. Read-only.
    section_id: omit for the newest section in the model.
    element_id: omit to use the single selected element.
    link_id: the RevitLinkInstance id when the element is inside a link.
    plan_view_id: the plan the section was made from; omit to use the active
      view if it is a plan.
    category: with nothing selected the element is searched for (Revit 2022
      cannot report a selected linked element); this exact category name, e.g.
      "Pipes", narrows the search. Check `candidates` before trusting margins."""
    return _compact(_call("GET", "/section/inspect/", query={
        "section_id": section_id,
        "element_id": element_id,
        "link_id": link_id,
        "plan_view_id": plan_view_id,
        "category": category,
    }))


if __name__ == "__main__":
    server.run()
