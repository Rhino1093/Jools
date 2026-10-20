# -*- coding: utf-8 -*-
"""///Summary: Feasibility audit for dimensioning a reflected ceiling plan.

Before an automatic-dimensioning tool is worth writing, one question has to be
answered: for the fixtures actually in this view, can the API produce a usable
Reference to dimension *to*?

`FamilyInstance.GetReferences()` yields references only for reference planes
whose "Is Reference" property is something other than "Not a Reference".
Autodesk's own docs are explicit that it returns null otherwise. That is a
property of the *family content*, not something a script can change at run time,
so a family whose author left every reference plane unmarked cannot be
dimensioned to its centre by any means. Manufacturer content is frequently in
exactly that state, which is why this audit exists and why it runs first.

Per family type the audit reports which reference types exist, and reduces that
to a verdict:

    center   CenterLeftRight *and* CenterFrontBack both present. Dimension to
             the fixture centre along either axis. This is what an RCP wants.
    partial  Only one centre axis. Dimensionable one way only.
    edges    No centre planes, but Left/Right/Front/Back or a Strong/Weak
             reference exists. Dimensions land on the fixture extents, which
             normally reads wrong on a ceiling plan.
    none     Nothing for any type. The family has to be fixed.

Nothing here writes to the model.

Engine note: written for the Python 2/3 common subset, because pyRevit runs
extension startup scripts (and therefore Routes handlers) on IronPython 2.7,
while pushbuttons in this extension run on CPython 3. No f-strings.
"""

import clr  # type: ignore

try:
    clr.AddReference("RevitAPI")  # type: ignore
except Exception:
    # Already loaded by the host under both engines; AddReference is a no-op
    # there, but a fresh CPython engine occasionally needs it.
    pass

from Autodesk.Revit import DB  # type: ignore
from System.Collections.Generic import List  # type: ignore

# joolslib imports nothing but sys and types at module scope, so it is safe to
# pull in from either engine (CLAUDE.md section 6a).
from joolslib import eid_int


# Every FamilyInstanceReferenceType worth testing. NotAReference is excluded
# because GetReferences is documented to return null for it by definition.
REFERENCE_TYPE_NAMES = (
    "CenterLeftRight",
    "CenterFrontBack",
    "CenterElevation",
    "Left",
    "Right",
    "Front",
    "Back",
    "Top",
    "Bottom",
    "StrongReference",
    "WeakReference",
)

# Categories that carry the things an RCP dimensions. Resolved by name at run
# time so that a category absent from an older Revit degrades to "skipped"
# rather than an AttributeError on import.
CEILING_CATEGORY_NAMES = (
    "OST_LightingFixtures",
    "OST_DuctTerminal",          # Air Terminals
    "OST_Sprinklers",
    "OST_CommunicationDevices",
    "OST_FireAlarmDevices",
    "OST_SecurityDevices",
    "OST_DataDevices",
    "OST_NurseCallDevices",
    "OST_ElectricalFixtures",
    "OST_MechanicalEquipment",
    "OST_SpecialityEquipment",
    "OST_GenericModel",
)

# Plan Z extent used when filtering linked elements. A plan view's crop box Z
# range describes view depth, not where fixtures actually sit, so filtering on
# it silently drops elements. Filter in XY only.
_Z_HALF_RANGE = 1.0e6


def _element_name(element):
    """///Summary: An element's Name, on both engines.

    IronPython raises AttributeError for `.Name` on ElementType subclasses
    such as FamilySymbol (seen live: the first audit run failed on
    symbol.Name), so fall back to calling the Element.Name getter directly.
    """
    try:
        return element.Name
    except Exception:
        return DB.Element.Name.__get__(element)


def _count(collection):
    """///Summary: Length of a .NET collection that may be null.

    GetReferences returns null (None here) when a reference type is absent, and
    an IList otherwise. .Count is present on both engines; the iteration
    fallback covers anything that only exposes an enumerator.
    """
    if collection is None:
        return 0
    try:
        return int(collection.Count)
    except AttributeError:
        return sum(1 for _ in collection)


def _xyz(point, digits=4):
    """///Summary: An XYZ as a plain [x, y, z] list, in feet."""
    if point is None:
        return None
    return [round(point.X, digits), round(point.Y, digits), round(point.Z, digits)]


def _resolve_categories(names):
    """///Summary: Pair each category name with its enum member, skipping any
    the running Revit does not define."""
    resolved = []
    missing = []
    for name in names:
        member = getattr(DB.BuiltInCategory, name, None)
        if member is None:
            missing.append(name)
        else:
            resolved.append((name, member))
    return resolved, missing


def _resolve_reference_types():
    """///Summary: Pair each reference-type name with its enum member.

    FamilyInstanceReferenceType arrived in Revit 2018; every member this module
    names has been present since. Resolved defensively anyway.
    """
    resolved = []
    for name in REFERENCE_TYPE_NAMES:
        member = getattr(DB.FamilyInstanceReferenceType, name, None)
        if member is not None:
            resolved.append((name, member))
    return resolved


def _multicategory_filter(category_members):
    """///Summary: An ElementMulticategoryFilter over the given enum members.

    The constructor takes ICollection<BuiltInCategory>. A Python list does not
    convert under pythonnet (CLAUDE.md section 3), so build a .NET List even
    though IronPython would tolerate the shortcut.
    """
    net_categories = List[DB.BuiltInCategory]()
    for member in category_members:
        net_categories.Add(member)
    return DB.ElementMulticategoryFilter(net_categories)


def reference_availability(instance, reference_types):
    """///Summary: How many references of each type this instance exposes.

    Returns a dict of type name -> count, containing only the types that
    actually produced references.
    """
    available = {}
    for name, member in reference_types:
        try:
            found = _count(instance.GetReferences(member))
        except Exception:
            # A family instance whose geometry cannot be resolved in this
            # document raises rather than returning null. Treat as absent.
            found = 0
        if found:
            available[name] = found
    return available


def verdict_for(available):
    """///Summary: Reduce a reference-availability dict to a one-word verdict.

    See the module docstring for what each verdict means for dimensioning.
    """
    has_lr = "CenterLeftRight" in available
    has_fb = "CenterFrontBack" in available
    if has_lr and has_fb:
        return "center"
    if has_lr or has_fb:
        return "partial"
    for name in ("Left", "Right", "Front", "Back",
                 "StrongReference", "WeakReference"):
        if name in available:
            return "edges"
    return "none"


def _location_point(element):
    """///Summary: An element's insertion point, or None if it is not point-based."""
    location = element.Location
    return getattr(location, "Point", None)


def collect_control_lines(doc, view):
    """///Summary: Reference planes and grids visible in the view.

    These are the things a fixture dimension measures *from*. Both expose a
    stable reference — ReferencePlane.GetReference(), and a Grid can be
    referenced as an element — so unlike fixtures they need no audit, only an
    inventory. A reference plane hidden in the view or outside its range will
    not appear here, which is itself the answer to "why can't I dimension from
    that line".
    """
    planes = []
    for plane in DB.FilteredElementCollector(doc, view.Id) \
            .OfClass(DB.ReferencePlane) \
            .ToElements():
        planes.append({
            "id": eid_int(plane.Id),
            "name": _element_name(plane),
            "direction": _xyz(plane.Direction),
        })

    grids = []
    for grid in DB.FilteredElementCollector(doc, view.Id) \
            .OfClass(DB.Grid) \
            .ToElements():
        curve = grid.Curve
        direction = None
        # Only a straight grid has a usable single direction; an arc grid is
        # reported without one so the caller can exclude it deliberately.
        if not grid.IsCurved and curve is not None:
            direction = _xyz(curve.Direction)
        grids.append({
            "id": eid_int(grid.Id),
            "name": _element_name(grid),
            "is_curved": bool(grid.IsCurved),
            "direction": direction,
        })

    return {"reference_planes": planes, "grids": grids}


def _crop_outline(view, into_transform=None):
    """///Summary: The view's crop extents as an Outline, optionally re-based.

    `into_transform` maps host coordinates into some other document's space —
    pass the inverse of a link's total transform to filter that link's elements
    by what the host view actually shows. Returns None when the view has no
    active crop, in which case the caller should not filter at all.
    """
    if not view.CropBoxActive:
        return None

    bbox = view.CropBox
    box_transform = bbox.Transform

    corners = []
    for x in (bbox.Min.X, bbox.Max.X):
        for y in (bbox.Min.Y, bbox.Max.Y):
            corner = box_transform.OfPoint(DB.XYZ(x, y, 0.0))
            if into_transform is not None:
                corner = into_transform.OfPoint(corner)
            corners.append(corner)

    xs = [p.X for p in corners]
    ys = [p.Y for p in corners]
    return DB.Outline(
        DB.XYZ(min(xs), min(ys), -_Z_HALF_RANGE),
        DB.XYZ(max(xs), max(ys), _Z_HALF_RANGE),
    )


def _audit_elements(elements, reference_types, transform=None):
    """///Summary: Group elements by family type and audit one instance of each.

    Reference planes live in the family, so every instance of a symbol exposes
    the same reference types. Testing one instance per symbol instead of all of
    them turns 11 API calls per fixture into 11 per family type — the
    difference between seconds and minutes on a real ceiling.

    `transform` maps the element's own coordinates into host space; pass a
    link's total transform when auditing a linked document.
    """
    by_symbol = {}
    skipped_non_family = 0

    for element in elements:
        if not isinstance(element, DB.FamilyInstance):
            # Ceilings, and a few system families, land in these categories
            # without being FamilyInstances. They have no GetReferences at all.
            skipped_non_family += 1
            continue

        symbol = element.Symbol
        key = eid_int(symbol.Id)

        entry = by_symbol.get(key)
        if entry is None:
            category = element.Category
            point = _location_point(element)
            if point is not None and transform is not None:
                point = transform.OfPoint(point)
            entry = {
                "symbol_id": key,
                "family": symbol.Family.Name,
                "type": _element_name(symbol),
                "category": category.Name if category is not None else None,
                "count": 0,
                "sample_point": _xyz(point),
                "references": reference_availability(element, reference_types),
            }
            entry["verdict"] = verdict_for(entry["references"])
            by_symbol[key] = entry

        entry["count"] += 1

    families = sorted(
        by_symbol.values(),
        key=lambda e: (e["category"] or "", e["family"], e["type"]),
    )
    return families, skipped_non_family


def audit_view(doc, view, category_names=None):
    """///Summary: Audit every ceiling fixture the view shows, host and linked.

    Read-only. Returns a JSON-serialisable dict; see the module docstring for
    what the per-family verdicts mean.

    Raises ValueError with a message meant for the user when the view cannot be
    audited — the caller is expected to surface that text rather than a
    traceback.
    """
    if view is None:
        raise ValueError("No view was supplied. Open a ceiling plan and retry.")
    if not isinstance(view, DB.ViewPlan):
        raise ValueError(
            "This audit needs a plan view; '{0}' is a {1}. Open the reflected "
            "ceiling plan you want to dimension and run it again.".format(
                _element_name(view), view.ViewType)
        )

    categories, missing_categories = _resolve_categories(
        category_names or CEILING_CATEGORY_NAMES)
    if not categories:
        raise ValueError(
            "None of the requested categories exist in this version of Revit: "
            "{0}".format(", ".join(missing_categories))
        )

    reference_types = _resolve_reference_types()
    category_filter = _multicategory_filter([member for _, member in categories])

    sources = []

    # --- host document -----------------------------------------------------
    # Scoping the collector to the view means view filters, worksets and
    # visibility settings are all honoured — the audit sees what the drawing
    # sees, which is the only count that matters.
    host_elements = DB.FilteredElementCollector(doc, view.Id) \
        .WherePasses(category_filter) \
        .WhereElementIsNotElementType() \
        .ToElements()

    host_families, host_skipped = _audit_elements(host_elements, reference_types)
    sources.append({
        "source": "host",
        "link_name": None,
        "link_id": None,
        "scoped_to_crop": True,
        "families": host_families,
        "non_family_instances": host_skipped,
    })

    # --- linked documents --------------------------------------------------
    link_instances = DB.FilteredElementCollector(doc, view.Id) \
        .OfClass(DB.RevitLinkInstance) \
        .ToElements()

    for link in link_instances:
        link_doc = link.GetLinkDocument()
        if link_doc is None:
            # Unloaded link. Report it so an empty fixture list is not mistaken
            # for a family problem.
            sources.append({
                "source": "link",
                "link_name": _element_name(link),
                "link_id": eid_int(link.Id),
                "scoped_to_crop": False,
                "families": [],
                "non_family_instances": 0,
                "note": "Link is not loaded; nothing could be audited.",
            })
            continue

        transform = link.GetTotalTransform()
        # A linked collector cannot be scoped to a host view, so approximate it
        # with the crop extents expressed in the link's own coordinates.
        outline = _crop_outline(view, into_transform=transform.Inverse)

        collector = DB.FilteredElementCollector(link_doc) \
            .WherePasses(category_filter) \
            .WhereElementIsNotElementType()
        if outline is not None:
            collector = collector.WherePasses(
                DB.BoundingBoxIntersectsFilter(outline))

        link_families, link_skipped = _audit_elements(
            collector.ToElements(), reference_types, transform=transform)

        sources.append({
            "source": "link",
            "link_name": _element_name(link),
            "link_id": eid_int(link.Id),
            "scoped_to_crop": outline is not None,
            "families": link_families,
            "non_family_instances": link_skipped,
        })

    # --- summary -----------------------------------------------------------
    tally = {"center": 0, "partial": 0, "edges": 0, "none": 0}
    fixtures = {"center": 0, "partial": 0, "edges": 0, "none": 0}
    for source in sources:
        for family in source["families"]:
            tally[family["verdict"]] += 1
            fixtures[family["verdict"]] += family["count"]

    control_lines = collect_control_lines(doc, view)

    return {
        "view": {
            "id": eid_int(view.Id),
            "name": _element_name(view),
            "view_type": str(view.ViewType),
            "is_ceiling_plan": str(view.ViewType) == "CeilingPlan",
            "scale": view.Scale,
            "crop_active": bool(view.CropBoxActive),
        },
        "control_lines": control_lines,
        "sources": sources,
        "summary": {
            "family_types_by_verdict": tally,
            "fixtures_by_verdict": fixtures,
            "control_line_count": (len(control_lines["reference_planes"])
                                   + len(control_lines["grids"])),
            "categories_not_in_this_revit": missing_categories,
        },
    }
