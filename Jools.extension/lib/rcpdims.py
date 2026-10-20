# -*- coding: utf-8 -*-
"""///Summary: Dimension fixtures and grids in a ceiling plan, host or linked.

The geometry engine behind the RCP tools of the Jools Routes API. The caller
(Claude, through the MCP server) chooses *what* to dimension: item keys, an
axis, an offset. Everything that needs exact geometry happens here: resolving
host vs linked elements, picking the fixture centre plane, building references
Revit accepts, and placing the dimension line. An LLM is unreliable with raw
coordinates, so it never supplies any.

Reference methods, proven by the ZZProbeLinkDims probe on Revit 2024 and 2025
(host and linked, fixtures and grids):

    fixture  FamilyInstance.GetReferences(CenterLeftRight|CenterFrontBack)[0]
    grid     host:   Reference(grid)
             linked: "<gridUid>:0:SURFACE" parsed in the link's document
    linked   then .CreateLinkReference(linkInstance)

Revit 2022, 2023 and 2026 were not probed. If NewDimension rejects linked
references, it is retried once with "RVTLINK/<linkTypeUid>" cut from their
stable strings, which is the form a hand-drawn dimension stores.

Item keys round-trip through JSON: "<elementId>" for a host element,
"<linkInstanceId>/<elementId>" for an element inside a link.

Coordinates in results are view-aligned feet: u along the view's right
direction (screen x), v along its up direction (screen y).

Engine note: Python 2/3 common subset. Routes handlers run on IronPython 2.7
(see startup.py), a pushbutton would run this on CPython 3. No f-strings.
"""

import re

import clr  # type: ignore

try:
    clr.AddReference("RevitAPI")  # type: ignore
except Exception:
    # Already loaded by the host under both engines.
    pass

import System  # type: ignore
from Autodesk.Revit import DB  # type: ignore
from System.Collections.Generic import List  # type: ignore

from joolslib import eid_int


TRANSACTION_NAME = "Jools: RCP dimension"

# The categories this tool dimensions, by the names the MCP tools accept.
CATEGORY_GROUPS = {
    "lighting": ("OST_LightingFixtures",),
    "air_terminals": ("OST_DuctTerminal",),
    "sprinklers": ("OST_Sprinklers",),
    "devices": ("OST_FireAlarmDevices", "OST_CommunicationDevices",
                "OST_DataDevices", "OST_SecurityDevices",
                "OST_NurseCallDevices"),
}

# |cos| at or above this counts as aligned (about 2.5 degrees). A fixture
# rotated further than this off the view axes has no centre plane square to
# an axis dimension, and Revit would refuse or skew it.
ALIGNED = 0.999

# A grid counts as perpendicular to the dimension when |cos| is below this.
PERPENDICULAR = 0.01

# Two positions closer than this (feet, about 1/256") are the same position.
SAME_POSITION = 0.0003

# Fixtures closer than this across the axis share a row (feet, about 1 1/4").
ROW_TOLERANCE = 0.1

# Default largest gap along a row before it is split in two (feet). A
# heuristic: a wider gap usually means a wall or another room. Claude can still
# pass any subset of a row's keys, or ask for a different max_gap.
ROW_MAX_GAP = 12.0

# See the module docstring. UniqueIds contain no colons, so "[^:]+" consumes
# exactly the link type UniqueId.
_RVTLINK_TYPE_SEGMENT = re.compile(r"RVTLINK/[^:]+")

# Revit dimensions to a grid through its surface; this is the suffix a
# hand-drawn dimension stores (probe runs 1 and 2).
_GRID_SURFACE_SUFFIX = ":0:SURFACE"

# Plan Z extent used when approximating a link's visible elements by the crop
# box. A plan's crop Z range is view depth, not where fixtures sit, so filter
# in XY only.
_Z_HALF_RANGE = 1.0e6

# Dimensions created through this module since Revit started. Only these may
# be deleted through delete_dimensions, so a bad request can never remove a
# dimension a person drew. Lives as long as the Routes engine (the session).
_CREATED_IDS = set()


class RcpDimError(ValueError):
    """///Summary: A problem the caller can fix. The message is meant for the user.

    Subclasses ValueError so startup.py's existing ValueError handling turns it
    into a 400 response carrying this text.
    """


# --- small helpers ----------------------------------------------------------


def make_element_id(value):
    """///Summary: An ElementId from an int, valid in Revit 2022-2026 on both engines.

    Revit 2026 removed ElementId(Int32). A plain int binds to Int32 first where
    both exist; IronPython may not widen it to the lone Int64 overload in 2026,
    so that case is retried with an explicit System.Int64.
    """
    value = int(value)
    try:
        return DB.ElementId(value)
    except TypeError:
        return DB.ElementId(System.Int64(value))


def _to_int(text, key):
    """///Summary: Parse one numeric part of an item key, or explain the key format."""
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        raise RcpDimError(
            "'{0}' is not a valid item key. Keys look like '123456' (host) or "
            "'7890/123456' (link id / element id), exactly as rcp_list_items "
            "returns them.".format(key))


def _name(element):
    """///Summary: An element's Name; pythonnet sometimes needs the getter called directly."""
    try:
        return element.Name
    except Exception:
        return DB.Element.Name.__get__(element)


def _xy(value):
    """///Summary: Round a view coordinate for output; 3 decimals is 1/80"."""
    return round(value, 3)


def format_length(doc, value):
    """///Summary: A length in the project's own display units, e.g. 4' - 4 1/4"."""
    if value is None:
        return "?"
    return DB.UnitFormatUtils.Format(
        doc.GetUnits(), DB.SpecTypeId.Length, value, False)


def require_plan(view):
    """///Summary: Reject anything that is not a usable plan view."""
    if view is None:
        raise RcpDimError("No view. Open the reflected ceiling plan and retry.")
    if not isinstance(view, DB.ViewPlan) or view.IsTemplate:
        raise RcpDimError(
            "'{0}' is a {1}, not a plan. Open the reflected ceiling plan you want "
            "to dimension (or pass its view_id) and retry.".format(
                _name(view), view.ViewType))


def _categories(groups):
    """///Summary: The BuiltInCategory members for the requested group names.

    `groups` is None (all groups), or a list / comma string of keys of
    CATEGORY_GROUPS. Categories absent from the running Revit are skipped.
    """
    if groups is None:
        wanted = sorted(CATEGORY_GROUPS)
    elif isinstance(groups, (list, tuple)):
        wanted = [str(g).strip() for g in groups if str(g).strip()]
    else:
        wanted = [g.strip() for g in str(groups).split(",") if g.strip()]

    unknown = [g for g in wanted if g not in CATEGORY_GROUPS]
    if unknown:
        raise RcpDimError("Unknown categories: {0}. Use any of: {1}.".format(
            ", ".join(unknown), ", ".join(sorted(CATEGORY_GROUPS))))

    members = []
    for group in wanted:
        for name in CATEGORY_GROUPS[group]:
            member = getattr(DB.BuiltInCategory, name, None)
            if member is not None:
                members.append(member)
    return members


def _category_filter(members):
    """///Summary: An ElementMulticategoryFilter. pythonnet needs a .NET List here."""
    net = List[DB.BuiltInCategory]()
    for member in members:
        net.Add(member)
    return DB.ElementMulticategoryFilter(net)


def _crop_outline(view, into_transform):
    """///Summary: The view's crop extents in another document's coordinates.

    Only used before Revit 2023.1, which lacks the link-aware collector.
    Returns None when the crop is off, meaning: do not filter.
    Same approach as rcpaudit._crop_outline.
    """
    if not view.CropBoxActive:
        return None
    bbox = view.CropBox
    corners = []
    for x in (bbox.Min.X, bbox.Max.X):
        for y in (bbox.Min.Y, bbox.Max.Y):
            corner = bbox.Transform.OfPoint(DB.XYZ(x, y, 0.0))
            corners.append(into_transform.OfPoint(corner))
    xs = [p.X for p in corners]
    ys = [p.Y for p in corners]
    return DB.Outline(DB.XYZ(min(xs), min(ys), -_Z_HALF_RANGE),
                      DB.XYZ(max(xs), max(ys), _Z_HALF_RANGE))


def _collector(doc, view, link):
    """///Summary: A collector over what the view shows from one source.

    Returns (collector, exact). For a link on Revit 2023.1+ the collector
    honours the host view's visibility, filters and worksets. Earlier versions
    lack that constructor, so the link's elements are approximated by the crop
    box and `exact` is False.
    """
    if link is None:
        return DB.FilteredElementCollector(doc, view.Id), True
    try:
        return DB.FilteredElementCollector(doc, view.Id, link.Id), True
    except Exception:
        # TypeError on 2022 / 2023.0 (no such overload). Also covers an
        # ArgumentException for a view that cannot show elements.
        collector = DB.FilteredElementCollector(link.GetLinkDocument())
        outline = _crop_outline(view, link.GetTotalTransform().Inverse)
        if outline is not None:
            collector = collector.WherePasses(DB.BoundingBoxIntersectsFilter(outline))
        return collector, False


def _visible_links(doc, view):
    """///Summary: Loaded Revit links that the view shows."""
    links = []
    for link in DB.FilteredElementCollector(doc, view.Id).OfClass(DB.RevitLinkInstance):
        if link.GetLinkDocument() is not None:
            links.append(link)
    return links


def _item_key(element, link):
    """///Summary: The JSON-safe key for an element; see the module docstring."""
    if link is None:
        return str(eid_int(element.Id))
    return "{0}/{1}".format(eid_int(link.Id), eid_int(element.Id))


def _source_name(link):
    """///Summary: 'host', or the link's file name without the instance suffix."""
    if link is None:
        return "host"
    return link.GetLinkDocument().Title


def _first_reference(instance, ref_type):
    """///Summary: The first reference of a type, or None when the family has none.

    GetReferences returns null when no reference plane has that "Is Reference"
    value. That is a property of the family content (see rcpaudit.py).
    """
    refs = instance.GetReferences(ref_type)
    if refs is None or refs.Count == 0:
        return None
    return refs[0]


def _center_planes(instance):
    """///Summary: Which centre planes a family exposes: both, LR, FB or none."""
    has_lr = _first_reference(
        instance, DB.FamilyInstanceReferenceType.CenterLeftRight) is not None
    has_fb = _first_reference(
        instance, DB.FamilyInstanceReferenceType.CenterFrontBack) is not None
    if has_lr and has_fb:
        return "both"
    if has_lr:
        return "LR"
    if has_fb:
        return "FB"
    return "none"


def _oriented(vector, transform):
    """///Summary: A direction in host coordinates."""
    return transform.OfVector(vector) if transform is not None else vector


def _located(point, transform):
    """///Summary: A point in host coordinates."""
    return transform.OfPoint(point) if transform is not None else point


def _fixture_point(instance):
    """///Summary: Where a fixture sits, in its own document's coordinates, or None.

    Most fixtures are point-based. Line-based ones (linear lights) carry a
    LocationCurve instead; its midpoint stands in for ordering and for placing
    the dimension line, while the reference itself still comes from the
    family's centre plane.
    """
    location = instance.Location
    point = getattr(location, "Point", None)
    if point is not None:
        return point
    curve = getattr(location, "Curve", None)
    if curve is not None:
        return curve.Evaluate(0.5, True)
    return None


def _grid_line(grid, transform):
    """///Summary: A straight grid's start point and unit direction, in host coordinates."""
    curve = grid.Curve
    start = _located(curve.GetEndPoint(0), transform)
    end = _located(curve.GetEndPoint(1), transform)
    return start, end.Subtract(start).Normalize()


# --- listing ----------------------------------------------------------------


def _usable_axes(hand, facing, right, up, planes):
    """///Summary: The axes ("x", "y") along which dimension_items can use a fixture.

    Mirrors _plan_fixture exactly: the centre plane is chosen by alignment, and
    the family must expose it. Listing only usable axes means a row Claude is
    offered never fails for a reason the listing already knew.
    """
    axes = []
    for axis, along in (("x", right), ("y", up)):
        along_hand = abs(along.DotProduct(hand))
        along_facing = abs(along.DotProduct(facing))
        if max(along_hand, along_facing) < ALIGNED:
            continue
        needed = "LR" if along_hand >= along_facing else "FB"
        if planes in ("both", needed):
            axes.append(axis)
    return axes


def _grid_type_filter(grid_types):
    """///Summary: The lower-cased grid type names to keep, or None to keep every grid.

    Accepts a list or a comma string. Empty means no filter.
    """
    if grid_types in (None, ""):
        return None
    if isinstance(grid_types, (list, tuple)):
        names = [str(t).strip() for t in grid_types]
    else:
        names = str(grid_types).split(",")
    wanted = set(n.strip().lower() for n in names if n.strip())
    return wanted or None


def list_items(doc, view, categories=None, detail="rows", max_gap=None,
               grid_types=None):
    """///Summary: Every fixture and grid the view shows, host and linked.

    Read-only. `detail` "rows" (default) returns the compact form built by
    compact_listing: fixtures pre-grouped into rows with their nearest grids,
    small enough to hand an LLM. "full" returns one entry per fixture, for
    debugging. Fixtures carry their view position (u, v), which centre planes
    their family exposes, and the axes they can be dimensioned along. Grids
    that appear in more than one model are listed once.

    `grid_types` keeps only grids of those type names (case-insensitive), so
    rows tie to the grid system the drawings use, e.g. "GC Control Line".
    """
    require_plan(view)
    if detail not in ("rows", "full"):
        raise RcpDimError("detail must be 'rows' or 'full'; got '{0}'.".format(detail))
    try:
        max_gap = ROW_MAX_GAP if max_gap in (None, "") else float(max_gap)
    except (TypeError, ValueError):
        raise RcpDimError("max_gap must be a number of feet, e.g. 12.")
    wanted_grid_types = _grid_type_filter(grid_types)
    category_filter = _category_filter(_categories(categories))
    right = view.RightDirection
    up = view.UpDirection
    links = _visible_links(doc, view)

    fixtures = []
    grids = []
    notes = []
    planes_by_symbol = {}

    for link in [None] + links:
        transform = link.GetTotalTransform() if link is not None else None

        collector, exact = _collector(doc, view, link)
        if not exact:
            notes.append(
                "{0}: this Revit cannot ask which linked elements the view shows, "
                "so they were filtered by the crop box only; hidden ones may be "
                "listed.".format(_source_name(link)))

        for element in collector.WherePasses(category_filter).WhereElementIsNotElementType():
            # Nested shared components (lamps inside a fixture) are their own
            # FamilyInstances; dimensioning them would double up the parent.
            if not isinstance(element, DB.FamilyInstance) or element.SuperComponent is not None:
                continue
            point = _fixture_point(element)
            if point is None:
                continue
            point = _located(point, transform)

            # Centre planes belong to the family, so test once per symbol.
            symbol_key = (eid_int(link.Id) if link is not None else 0,
                          eid_int(element.Symbol.Id))
            if symbol_key not in planes_by_symbol:
                planes_by_symbol[symbol_key] = _center_planes(element)

            hand = _oriented(element.HandOrientation, transform)
            facing = _oriented(element.FacingOrientation, transform)
            planes = planes_by_symbol[symbol_key]
            entry = {
                "key": _item_key(element, link),
                "source": _source_name(link),
                "category": (element.Category.Name
                             if element.Category is not None else None),
                "family": element.Symbol.Family.Name,
                "type": _name(element.Symbol),
                "u": _xy(point.DotProduct(right)),
                "v": _xy(point.DotProduct(up)),
                "center_planes": planes,
                "axes": _usable_axes(hand, facing, right, up, planes),
            }
            if max(abs(hand.DotProduct(right)), abs(hand.DotProduct(up))) < ALIGNED:
                entry["rotated"] = True
            fixtures.append(entry)

        collector, _ = _collector(doc, view, link)
        for grid in collector.OfClass(DB.Grid):
            _add_grid(grids, grid, link, transform, right, up)

    if wanted_grid_types is not None:
        present = sorted(set(g["grid_type"] for g in grids))
        grids = [g for g in grids if g["grid_type"].lower() in wanted_grid_types]
        if not grids:
            notes.append("No grids of type {0} are visible here, so rows have no grid "
                         "ties. Grid types in this view: {1}.".format(
                             ", ".join(sorted(wanted_grid_types)), ", ".join(present)))

    curved = [g for g in grids if g["orientation"] == "curved"]
    if curved:
        notes.append("{0} curved grid(s) listed but cannot be dimensioned.".format(len(curved)))

    view_info = {
        "id": eid_int(view.Id),
        "name": _name(view),
        "view_type": str(view.ViewType),
        "scale": view.Scale,
    }
    if detail == "rows":
        return compact_listing(view_info, fixtures, grids, notes, max_gap)
    return {
        "view": view_info,
        "coordinates": "feet; u = along view right (screen x), v = along view up (screen y)",
        "fixtures": fixtures,
        "grids": grids,
        "notes": notes,
    }


# --- compact listing ----------------------------------------------------------
# Pure Python on plain dicts, no Revit API: testable outside Revit, and the
# same on both engines.


def _grid_lines(grids, orientation):
    """///Summary: Straight grids of one orientation, merged by position.

    Returns [(position, [[name, key], ...]), ...] sorted by position. Two
    differently named grids on the same line (overlapping grid systems) share
    one entry, so a row reports both names and the user picks the system.
    """
    coordinate = "u" if orientation == "vertical" else "v"
    lines = []
    for grid in sorted((g for g in grids if g["orientation"] == orientation),
                       key=lambda g: g[coordinate]):
        if lines and abs(grid[coordinate] - lines[-1][0]) < 0.01:
            lines[-1][1].append([grid["name"], grid["key"]])
        else:
            lines.append((grid[coordinate], [[grid["name"], grid["key"]]]))
    return lines


def _nearest_line(lines, position, side):
    """///Summary: The nearest grid line strictly before (-1) or after (+1) a position.

    "Strictly": a grid within SAME_POSITION of the fixture would make a
    zero-length segment, so it does not count. Returns [distance, "46/58"]
    (every grid name on that line, so the user can pick the grid system), or
    None when there is no grid on that side. Keys are in the grids table.
    """
    best = None
    for line_position, names in lines:
        offset = (line_position - position) * side
        if offset > SAME_POSITION and (best is None or offset < best[0]):
            best = (offset, names)
    if best is None:
        return None
    return [_xy(best[0]), "/".join(name for name, _ in best[1])]


def _runs(fixtures, axis, max_gap, stacked):
    """///Summary: Rows (axis x) or columns (axis y) of same-type fixtures.

    Fixtures of one type whose cross-axis positions chain within
    ROW_TOLERANCE form a line; a gap above `max_gap` along the line splits it.
    Fixtures at the same position as the previous one (stacked duplicates)
    are left out of the run and recorded in `stacked`, because the dimension
    would refuse a zero-length segment. Runs of one fixture are dropped.
    """
    along, across = ("u", "v") if axis == "x" else ("v", "u")
    by_type = {}
    for fixture in fixtures:
        if axis in fixture["axes"]:
            by_type.setdefault(fixture["_t"], []).append(fixture)

    runs = []
    for group in by_type.values():
        group.sort(key=lambda f: f[across])
        lines = [[group[0]]]
        for previous, current in zip(group, group[1:]):
            if current[across] - previous[across] <= ROW_TOLERANCE:
                lines[-1].append(current)
            else:
                lines.append([current])

        for line in lines:
            line.sort(key=lambda f: f[along])
            run = [line[0]]
            for current in line[1:]:
                gap = current[along] - run[-1][along]
                if gap < SAME_POSITION:
                    stacked.add(tuple(sorted((run[-1]["key"], current["key"]))))
                elif gap > max_gap:
                    runs.append(run)
                    run = [current]
                else:
                    run.append(current)
            runs.append(run)

    return [run for run in runs if len(run) >= 2]


def compact_listing(view_info, fixtures, grids, notes, max_gap=None):
    """///Summary: The listing grouped into rows, about a tenth the size of "full".

    Every usable fixture appears in at least one row (or column) or in
    "loose". Types are listed once and referenced by index, and rows are
    arrays under one shared header: in the first real test (705 fixtures),
    repeated field names and per-row grid keys were most of the payload, not
    the fixture keys. Each row names the nearest grid line before and after it
    along its own axis; grids falling inside a row can be found from the grids
    table's positions.
    """
    max_gap = ROW_MAX_GAP if max_gap is None else max_gap
    types, type_index = [], {}
    for fixture in fixtures:
        signature = (fixture["source"], fixture["category"], fixture["family"],
                     fixture["type"], fixture["center_planes"])
        if signature not in type_index:
            type_index[signature] = len(types)
            types.append(list(signature))
        fixture["_t"] = type_index[signature]

    lines_for = {"x": _grid_lines(grids, "vertical"),
                 "y": _grid_lines(grids, "horizontal")}
    stacked = set()
    rows = []
    in_a_row = set()
    for axis in ("x", "y"):
        along, across = ("u", "v") if axis == "x" else ("v", "u")
        for run in _runs(fixtures, axis, max_gap, stacked):
            start, end = run[0][along], run[-1][along]
            rows.append([
                axis,
                run[0]["_t"],
                _xy(sum(f[across] for f in run) / float(len(run))),
                start,
                end,
                [f["key"] for f in run],
                _nearest_line(lines_for[axis], start, -1),
                _nearest_line(lines_for[axis], end, 1),
            ])
            in_a_row.update(f["key"] for f in run)

    loose = [[f["key"], f["_t"], f["u"], f["v"]] for f in fixtures
             if f["axes"] and f["key"] not in in_a_row]
    skipped = [[f["key"], "rotated" if f.get("rotated") else "no usable centre plane"]
               for f in fixtures if not f["axes"]]
    if stacked:
        notes = notes + ["{0} pair(s) of fixtures sit at the same position "
                         "(likely duplicates); one of each pair was left out of "
                         "its row. See 'stacked'.".format(len(stacked))]

    return {
        "view": view_info,
        "coordinates": ("feet. u = screen x, v = screen y. A row's 'at' is its "
                        "cross-axis position; 'from'/'to' run along its axis."),
        "types": types,
        "type_fields": ["source", "category", "family", "type", "center_planes"],
        "rows": rows,
        "row_fields": ["axis", "type", "at", "from", "to", "keys",
                       "grid_before [dist, names]", "grid_after [dist, names]"],
        "loose": loose,
        "loose_fields": ["key", "type", "u", "v"],
        "skipped": skipped,
        "stacked": [list(pair) for pair in sorted(stacked)],
        "grids": [[g["key"], g["name"], g["orientation"], g.get("u", g.get("v")),
                   g["grid_type"]] for g in grids],
        "grid_fields": ["key", "name", "orientation", "position", "grid_type"],
        "notes": notes,
    }


def _add_grid(grids, grid, link, transform, right, up):
    """///Summary: Append a grid to the listing, or note it on an equal grid already listed."""
    grid_type = grid.Document.GetElement(grid.GetTypeId())
    entry = {"key": _item_key(grid, link), "source": _source_name(link),
             "name": _name(grid),
             "grid_type": _name(grid_type) if grid_type is not None else ""}

    if grid.IsCurved:
        entry["orientation"] = "curved"
        grids.append(entry)
        return

    start, direction = _grid_line(grid, transform)
    if abs(direction.DotProduct(up)) >= ALIGNED:
        # Runs up the screen, so a constant u locates it; it belongs in axis
        # "x" (horizontal) dimension strings.
        entry["orientation"] = "vertical"
        entry["u"] = _xy(start.DotProduct(right))
    elif abs(direction.DotProduct(right)) >= ALIGNED:
        entry["orientation"] = "horizontal"
        entry["v"] = _xy(start.DotProduct(up))
    else:
        entry["orientation"] = "angled"

    # Copy/monitored and multi-link grids are the same line drawn twice. Keep
    # the first (host is collected first) and record where else it appears.
    # Type is part of the match so the grid-type filter cannot lose a
    # GC Control Line grid behind a same-named bubble grid.
    for listed in grids:
        if (listed["name"] == entry["name"]
                and listed["grid_type"] == entry["grid_type"]
                and listed["orientation"] == entry["orientation"]
                and abs(listed.get("u", 0) - entry.get("u", 0)) < 0.01
                and abs(listed.get("v", 0) - entry.get("v", 0)) < 0.01):
            listed.setdefault("also_in", []).append(entry["key"])
            return
    grids.append(entry)


# --- dimensioning -----------------------------------------------------------


def _resolve(doc, key):
    """///Summary: (element, link) for an item key. link is None for host elements."""
    text = str(key).strip()
    if "/" in text:
        link_part, element_part = text.split("/", 1)
        link = doc.GetElement(make_element_id(_to_int(link_part, key)))
        if not isinstance(link, DB.RevitLinkInstance):
            raise RcpDimError("Key '{0}': {1} is not a Revit link in this model. "
                              "Run rcp_list_items again and use its keys.".format(key, link_part))
        link_doc = link.GetLinkDocument()
        if link_doc is None:
            raise RcpDimError("Key '{0}': that link is not loaded. Reload it in "
                              "Manage Links and retry.".format(key))
        element = link_doc.GetElement(make_element_id(_to_int(element_part, key)))
    else:
        link = None
        element = doc.GetElement(make_element_id(_to_int(text, key)))

    if element is None:
        raise RcpDimError("Key '{0}' does not match any element. Run rcp_list_items "
                          "again and use its keys.".format(key))
    return element, link


def _plan_fixture(key, instance, link, along, axis):
    """///Summary: Position and native reference for one fixture in this dimension."""
    transform = link.GetTotalTransform() if link is not None else None
    hand = _oriented(instance.HandOrientation, transform)
    facing = _oriented(instance.FacingOrientation, transform)
    along_hand = abs(along.DotProduct(hand))
    along_facing = abs(along.DotProduct(facing))

    if max(along_hand, along_facing) < ALIGNED:
        raise RcpDimError(
            "Fixture {0} is rotated off the view axes, so neither centre plane is "
            "square to an axis-'{1}' dimension. Leave it out.".format(key, axis))

    # CenterLeftRight separates left from right, so its normal is the hand
    # direction: it is the plane to use when the dimension runs along it.
    if along_hand >= along_facing:
        ref_type, label = DB.FamilyInstanceReferenceType.CenterLeftRight, "Center (Left/Right)"
    else:
        ref_type, label = DB.FamilyInstanceReferenceType.CenterFrontBack, "Center (Front/Back)"

    native = _first_reference(instance, ref_type)
    if native is None:
        raise RcpDimError(
            "Fixture {0} ({1}: {2}) has no '{3}' reference plane, so it cannot be "
            "dimensioned to its centre along axis '{4}'. In the family, set that "
            "plane's 'Is Reference' to {3}, reload the family, and retry, or leave "
            "this fixture out.".format(
                key, instance.Symbol.Family.Name, _name(instance.Symbol), label, axis))

    point = _fixture_point(instance)
    if point is None:
        raise RcpDimError("Fixture {0} has no location in the model, so it cannot "
                          "be placed in a dimension string.".format(key))
    point = _located(point, transform)
    return {"key": key, "fixture": True, "native": native, "link": link,
            "s": point.DotProduct(along), "point": point}


def _plan_grid(key, grid, link, along, axis):
    """///Summary: Position and native reference for one grid in this dimension."""
    name = _name(grid)
    if grid.IsCurved:
        raise RcpDimError("Grid {0} is curved; only straight grids can be "
                          "dimensioned.".format(name))

    transform = link.GetTotalTransform() if link is not None else None
    start, direction = _grid_line(grid, transform)
    if abs(direction.DotProduct(along)) > PERPENDICULAR:
        raise RcpDimError(
            "Grid {0} does not run square across an axis-'{1}' dimension. Vertical "
            "grids belong in axis 'x' strings, horizontal grids in axis 'y' "
            "strings.".format(name, axis))

    if link is None:
        native = DB.Reference(grid)
    else:
        # Parsed in the link's own document so CreateLinkReference can wrap it.
        try:
            native = DB.Reference.ParseFromStableRepresentation(
                grid.Document, grid.UniqueId + _GRID_SURFACE_SUFFIX)
        except Exception as ex:
            raise RcpDimError(
                "Grid {0} in {1} could not be referenced ({2}). Use the same grid "
                "from another model if rcp_list_items shows one under "
                "'also_in'.".format(name, _source_name(link), ex))
    return {"key": key, "fixture": False, "native": native, "link": link,
            "s": start.DotProduct(along), "point": start}


def _plan(doc, key, along, axis):
    """///Summary: Resolve one key and work out how it joins the dimension."""
    element, link = _resolve(doc, key)
    if isinstance(element, DB.FamilyInstance):
        return _plan_fixture(key, element, link, along, axis)
    if isinstance(element, DB.Grid):
        return _plan_grid(key, element, link, along, axis)
    raise RcpDimError("Key '{0}' is a {1}; only fixtures and grids can be "
                      "dimensioned.".format(key, type(element).__name__))


def _host_reference(item):
    """///Summary: The reference as the host document sees it."""
    if item["link"] is None:
        return item["native"]
    return item["native"].CreateLinkReference(item["link"])


def _rewritten_reference(doc, item):
    """///Summary: The fallback form of a linked reference; see the module docstring."""
    host_ref = _host_reference(item)
    if item["link"] is None:
        return host_ref
    text = host_ref.ConvertToStableRepresentation(doc)
    return DB.Reference.ParseFromStableRepresentation(
        doc, _RVTLINK_TYPE_SEGMENT.sub("RVTLINK", text, 1))


def _new_dimension(doc, view, line, references, dimension_type):
    """///Summary: One NewDimension call over the given references."""
    array = DB.ReferenceArray()
    for reference in references:
        array.Append(reference)
    if dimension_type is None:
        return doc.Create.NewDimension(view, line, array)
    return doc.Create.NewDimension(view, line, array, dimension_type)


def _create(doc, view, line, items, dimension_type):
    """///Summary: Create the dimension, retrying once with rewritten link references.

    Returns (dimension, method name). Raises RcpDimError with Revit's own
    reason when every attempt fails.
    """
    try:
        refs = [_host_reference(item) for item in items]
        return _new_dimension(doc, view, line, refs, dimension_type), "standard"
    except Exception as first:
        if not any(item["link"] is not None for item in items):
            raise RcpDimError("Revit rejected the dimension: {0}".format(first))
        try:
            refs = [_rewritten_reference(doc, item) for item in items]
            return _new_dimension(doc, view, line, refs, dimension_type), "rvtlink-rewrite"
        except Exception as second:
            raise RcpDimError("Revit rejected the dimension both ways. Standard: {0} "
                              "Rewritten: {1}".format(first, second))


def _segment_lengths(dimension):
    """///Summary: Every segment length, in feet. Value is null on a multi-segment string."""
    if dimension.NumberOfSegments > 1:
        return [segment.Value for segment in dimension.Segments]
    return [dimension.Value]


def _find_dimension_type(doc, name):
    """///Summary: The linear dimension type with this exact name, or an error listing the options."""
    available = []
    for dim_type in DB.FilteredElementCollector(doc).OfClass(DB.DimensionType):
        if dim_type.StyleType != DB.DimensionStyleType.Linear:
            continue
        type_name = _name(dim_type)
        if type_name == name:
            return dim_type
        available.append(type_name)
    raise RcpDimError("No linear dimension type named '{0}'. Available: {1}.".format(
        name, ", ".join(sorted(set(available)))))


def _transaction(doc):
    """///Summary: A transaction that discards its warnings silently if rolled back.

    Without ClearAfterRollback, the API docs say failures posted by a
    rolled-back transaction "may still be displayed to the user", which would
    stall a request made from outside Revit.
    """
    transaction = DB.Transaction(doc, TRANSACTION_NAME)
    options = transaction.GetFailureHandlingOptions()
    options.SetClearAfterRollback(True)
    transaction.SetFailureHandlingOptions(options)
    return transaction


def dimension_items(doc, view, keys, axis, offset_in=0.375,
                    dimension_type=None, dry_run=False):
    """///Summary: One dimension string through the given fixtures and grids.

    `axis` "x" runs the string horizontally on screen, "y" vertically. The
    items are sorted along the axis, so key order does not matter. The
    dimension line sits `offset_in` paper inches from the fixtures' mean
    position, positive toward screen up (axis x) or screen right (axis y).
    Paper inches times the view scale keeps the gap constant on the sheet.

    `dry_run` creates the dimension, measures it, and rolls it back: a preview
    with real segment lengths and nothing left in the model.

    The "first fixture to a grid, then fixture spacing" convention is one call:
    the grid's key plus every fixture in the row.
    """
    require_plan(view)
    axis = str(axis).strip().lower()
    if axis not in ("x", "y"):
        raise RcpDimError("axis must be 'x' (horizontal on screen) or 'y' "
                          "(vertical on screen); got '{0}'.".format(axis))
    if not keys or len(keys) < 2:
        raise RcpDimError("A dimension needs at least two item keys.")
    if len(set(str(k).strip() for k in keys)) != len(keys):
        raise RcpDimError("The same item key appears twice; list each item once.")

    along = view.RightDirection if axis == "x" else view.UpDirection
    across = view.UpDirection if axis == "x" else view.RightDirection

    items = sorted((_plan(doc, key, along, axis) for key in keys),
                   key=lambda item: item["s"])

    fixtures = [item for item in items if item["fixture"]]
    if not fixtures:
        raise RcpDimError("Include at least one fixture; the dimension line is "
                          "placed relative to the fixtures.")

    for first, second in zip(items, items[1:]):
        if second["s"] - first["s"] < SAME_POSITION:
            raise RcpDimError(
                "{0} and {1} sit at the same position along axis '{2}', which would "
                "make a zero-length segment. Leave one of them out.".format(
                    first["key"], second["key"], axis))

    try:
        offset_ft = float(offset_in) / 12.0 * view.Scale
    except (TypeError, ValueError):
        raise RcpDimError("offset_in must be a number of paper inches, e.g. 0.375.")

    # The line runs along the axis at the fixtures' mean cross-axis position
    # plus the offset. Right and Up are horizontal unit vectors in a plan, so
    # rebuilding a point from (s, t) is exact in XY; Z is the view's plane.
    t_line = sum(item["point"].DotProduct(across) for item in fixtures) / len(fixtures)
    t_line += offset_ft
    z_axis = DB.XYZ(0.0, 0.0, view.Origin.Z)

    def at(s):
        return along.Multiply(s).Add(across.Multiply(t_line)).Add(z_axis)

    line = DB.Line.CreateBound(at(items[0]["s"]), at(items[-1]["s"]))
    chosen_type = _find_dimension_type(doc, dimension_type) if dimension_type else None

    transaction = _transaction(doc)
    transaction.Start()
    try:
        dimension, method = _create(doc, view, line, items, chosen_type)
        if dimension is None:
            raise RcpDimError("Revit returned no dimension for these items.")
        doc.Regenerate()
        lengths = _segment_lengths(dimension)
        dimension_id = eid_int(dimension.Id)

        if dry_run:
            transaction.RollBack()
        else:
            status = transaction.Commit()
            if status != DB.TransactionStatus.Committed:
                raise RcpDimError("Revit did not keep the dimension (transaction "
                                  "status: {0}).".format(status))
            _CREATED_IDS.add(dimension_id)
    finally:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()

    return {
        "dry_run": bool(dry_run),
        "dimension_id": None if dry_run else dimension_id,
        "axis": axis,
        "order": [item["key"] for item in items],
        "segments": [format_length(doc, value) for value in lengths],
        "reference_method": method,
    }


def delete_dimensions(doc, ids):
    """///Summary: Delete dimensions this module created since Revit started.

    Anything else is refused, never deleted: a mistaken id cannot remove a
    dimension a person drew. Undo in Revit still works for everything.
    """
    if not ids:
        raise RcpDimError("Give at least one dimension id to delete.")

    deleted, refused = [], []
    targets = []
    for raw in ids:
        number = _to_int(raw, raw)
        element = doc.GetElement(make_element_id(number))
        if number not in _CREATED_IDS or not isinstance(element, DB.Dimension):
            refused.append(number)
        else:
            targets.append((number, element.Id))

    if targets:
        transaction = _transaction(doc)
        transaction.Start()
        try:
            for _, element_id in targets:
                doc.Delete(element_id)
            status = transaction.Commit()
        finally:
            if transaction.HasStarted() and not transaction.HasEnded():
                transaction.RollBack()
        # Only a committed transaction actually removed anything.
        if status != DB.TransactionStatus.Committed:
            raise RcpDimError("Revit did not delete the dimensions (transaction "
                              "status: {0}).".format(status))
        for number, _ in targets:
            deleted.append(number)
            _CREATED_IDS.discard(number)

    result = {"deleted": deleted, "refused": refused}
    if refused:
        result["note"] = ("Refused ids were not created by this tool since Revit "
                          "started, or no longer exist. Delete those by hand or "
                          "with Undo.")
    return result
