# -*- coding: utf-8 -*-
"""///Summary: pyRevit Routes API for the Jools extension.

Turns Revit into a small HTTP server so an external process — an MCP server, a
script, curl — can ask the open model questions. pyRevit supplies the hard part:
routes/server/server.py creates one UI.ExternalEvent at module load and raises it
per request, so any handler that takes `doc`, `uidoc` or `uiapp` runs inside a
valid Revit API context (routes/server/handler.py: wants_api_context).

ENGINE — this file is the one deliberate exception to CLAUDE.md section 2.1.
pyRevit runs extension startup scripts on IronPython 2.7 and offers no way to
ask for CPython: loader/sessionmgr.py's execute_extension_startup_script()
hard-codes runtime.create_ipyengine_configs(), which is the only engine-config
factory in pyrevit/runtime/__init__.py. A `#! python3` line here would be read
as a comment and ignored, so there is none. Keep this file — and everything it
imports — on the Python 2/3 common subset. No f-strings.

SETUP — Routes is off by default. pyRevit Settings > Routes > enable, then
restart Revit. That writes `[routes] enabled = true` into
%APPDATA%/pyRevit/pyRevit_config.ini; the port is on the same page.

    GET  /jools/ping/                 connection test, names the open document
    GET  /jools/rcp/audit/            dimensioning feasibility for the active view
    GET  /jools/rcp/audit/?view_id=N  ... for a specific view
    GET  /jools/rcp/items/            fixtures and grids the view shows, host + linked
                                      (?view_id=N, ?categories=lighting,sprinklers)
    POST /jools/rcp/dimension/        one dimension string; JSON body, see rcp_dimension
    POST /jools/rcp/delete/           delete dimensions this API created; {"ids": [..]}
    GET  /jools/fab/info/             fabrication config, services, buttons (?service_id=N)
    GET  /jools/fab/connectors/       open connectors (?selected=1, ?element_ids=1,2, ?domain=piping)
    POST /jools/fab/route/            stretch a run between two connectors; dry_run by default

Every route answers on 127.0.0.1 only once `host = "127.0.0.1"` is set under
[routes] in pyRevit_config.ini; the default binds every network interface.
"""

from pyrevit import routes

from Autodesk.Revit import DB  # type: ignore

# <extension>/lib is placed on sys.path for startup scripts by
# loader/sessionmgr.py (it inserts ext.library_path before running this file),
# so these need no path setup.
from joolslib import eid_int
import rcpaudit
import rcpdims
import fabroute


api = routes.API('jools')

BAD_REQUEST = 400

# ElementId(Int32) was removed in Revit 2026; only the Int64 overload remains
# (CLAUDE.md section 2.4). Under IronPython 2.7 a Python int marshals to
# System.Int32, so it has to be widened with long(); under CPython 3 int is
# already arbitrary-precision and marshals to Int64 on its own.
try:
    _to_int64 = long          # noqa: F821 - IronPython 2.7 only
except NameError:
    _to_int64 = int


def _fail(message, status=BAD_REQUEST):
    """///Summary: An error response carrying a sentence the user can act on.

    Routes will happily serialise a raw traceback, which is no use to whoever
    called the endpoint. Every foreseeable failure here returns this instead.
    """
    return routes.make_response({"error": message}, status=status)


def _resolve_view(doc, view_id):
    """///Summary: The requested view, or the active one when none was asked for.

    Raises ValueError with a user-facing message; the caller turns that into a
    response.
    """
    if view_id is None:
        return doc.ActiveView

    try:
        wanted = int(view_id)
    except (TypeError, ValueError):
        raise ValueError(
            "view_id must be a whole number; got '{0}'.".format(view_id))

    element = doc.GetElement(DB.ElementId(_to_int64(wanted)))
    if element is None:
        raise ValueError("No element in this model has id {0}.".format(wanted))
    if not isinstance(element, DB.View):
        raise ValueError("Element {0} is not a view.".format(wanted))
    return element


@api.route('/ping/', methods=['GET'])
def ping(doc):
    """///Summary: Connection test. Names the open document and active view."""
    return {
        "document": doc.Title,
        "active_view": doc.ActiveView.Name,
        "active_view_id": eid_int(doc.ActiveView.Id),
        "active_view_type": str(doc.ActiveView.ViewType),
    }


@api.route('/rcp/audit/', methods=['GET'])
def rcp_audit(doc, view_id=None):
    """///Summary: Can the fixtures in this ceiling plan actually be dimensioned?

    Read-only. Reports, per family type, which FamilyInstanceReferenceType
    values the family exposes, and reduces each to a verdict of center /
    partial / edges / none. See lib/rcpaudit.py for what those mean.

    Optional query parameter `view_id` audits a view other than the active one,
    which matters because the active view is whatever the user last clicked.
    """
    try:
        view = _resolve_view(doc, view_id)
        return rcpaudit.audit_view(doc, view)
    except ValueError as user_error:
        return _fail(str(user_error))


def _json_body(request):
    """///Summary: The request's JSON object, or ValueError saying how to send one.

    pyRevit parses the body only when Content-Type is application/json
    (routes/server/server.py _prepare_request); otherwise it arrives as bytes.
    """
    body = request.data
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise ValueError("Send a JSON object with Content-Type: application/json.")
    return body


def _as_bool(value):
    """///Summary: JSON true/false, tolerating the strings a hand-made request sends."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return bool(value)


@api.route('/rcp/items/', methods=['GET'])
def rcp_items(doc, view_id=None, categories=None, detail="rows", max_gap=None,
              grid_types=None):
    """///Summary: Every fixture and grid the view shows, host and linked, with keys.

    Read-only. The keys are what /rcp/dimension/ takes. `categories` is a comma
    list of lighting, air_terminals, sprinklers, devices (default: all).
    `detail` is "rows" (compact, grouped) or "full" (one entry per fixture).
    `max_gap` (feet) is the largest gap before a row is split; default 12.
    `grid_types` is a comma list of grid type names to keep (default: all).
    """
    try:
        view = _resolve_view(doc, view_id)
        return rcpdims.list_items(doc, view, categories, detail=detail,
                                  max_gap=max_gap, grid_types=grid_types)
    except ValueError as user_error:
        return _fail(str(user_error))


@api.route('/rcp/dimension/', methods=['POST'])
def rcp_dimension(doc, request):
    """///Summary: Create (or preview) one dimension string.

    JSON body: keys (list, required), axis ("x" or "y", required), view_id,
    offset_in (paper inches, default 0.375), dimension_type (type name),
    dry_run (default false). See rcpdims.dimension_items.
    """
    try:
        body = _json_body(request)
        view = _resolve_view(doc, body.get("view_id"))
        offset_in = body.get("offset_in")
        return rcpdims.dimension_items(
            doc, view,
            keys=body.get("keys"),
            axis=body.get("axis"),
            offset_in=0.375 if offset_in is None else offset_in,
            dimension_type=body.get("dimension_type"),
            dry_run=_as_bool(body.get("dry_run", False)),
        )
    except ValueError as user_error:
        return _fail(str(user_error))


@api.route('/rcp/delete/', methods=['POST'])
def rcp_delete(doc, request):
    """///Summary: Delete dimensions this API created since Revit started; refuse others."""
    try:
        body = _json_body(request)
        return rcpdims.delete_dimensions(doc, body.get("ids"))
    except ValueError as user_error:
        return _fail(str(user_error))


@api.route('/fab/info/', methods=['GET'])
def fab_info(doc, service_id=None):
    """///Summary: Fabrication config and services the project has loaded. Read-only."""
    try:
        return fabroute.fab_info(doc, service_id)
    except ValueError as user_error:
        return _fail(str(user_error))


@api.route('/fab/connectors/', methods=['GET'])
def fab_connectors(doc, uidoc, view_id=None, element_ids=None, selected=None,
                   domain=None, include_connected=None):
    """///Summary: Open connectors on fabrication parts, from ids, the selection or the view.

    Read-only. `element_ids` is a comma list; `selected` uses the current Revit
    selection; otherwise every fabrication part visible in the view.
    """
    try:
        view = _resolve_view(doc, view_id)
        ids = [i for i in str(element_ids).split(",") if i.strip()] if element_ids else None
        return fabroute.list_connectors(
            doc, uidoc, view, element_ids=ids, selected=_as_bool(selected),
            domain=domain, include_connected=_as_bool(include_connected))
    except ValueError as user_error:
        return _fail(str(user_error))


@api.route('/fab/route/', methods=['POST'])
def fab_route(doc, request):
    """///Summary: Route between two open connectors. dry_run defaults to TRUE.

    JSON body: from_key, to_key (from /fab/connectors/), dry_run (default true).
    """
    try:
        body = _json_body(request)
        return fabroute.route_between(
            doc, body.get("from_key"), body.get("to_key"),
            dry_run=_as_bool(body.get("dry_run", True)))
    except ValueError as user_error:
        return _fail(str(user_error))
