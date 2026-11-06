# -*- coding: utf-8 -*-
"""///Summary: Measure an existing section view against the element it cuts.

Built to reverse-engineer another tool's section sizing (Naviate's "Section
From Element") from its output rather than its code: run that tool, then ask
this module what box it made. Everything is read-only.

The useful numbers are the margins: how far each side of the crop box sits
beyond the element, measured in the section's own frame (right, up, toward
the viewer). Comparing those across a few elements of different sizes shows
whether the rule is a fixed offset, a ratio, or snaps to levels.

Engine note: Python 2/3 common subset. Routes handlers may run on IronPython
2.7 (see startup.py). No f-strings.
"""

import clr  # type: ignore

try:
    clr.AddReference("RevitAPI")  # type: ignore
except Exception:
    # Already loaded by the host under both engines.
    pass

from Autodesk.Revit import DB  # type: ignore

from joolslib import eid_int

# ElementId(Int32) was removed in Revit 2026 (CLAUDE.md section 2.4).
try:
    _to_int64 = long          # noqa: F821 - IronPython 2.7 only
except NameError:
    _to_int64 = int

_SECTION_TYPES = (DB.ViewType.Section, DB.ViewType.Detail)
_PLAN_TYPES = (DB.ViewType.FloorPlan, DB.ViewType.CeilingPlan,
               DB.ViewType.EngineeringPlan)

# Cap on connectors reported per element; a big fitting can have many and the
# end connectors are all that sizing could depend on.
_MAX_CONNECTORS = 6


def _feet(value):
    """///Summary: A length rounded for JSON; full precision is noise to an LLM."""
    return round(value, 4)


def _point(xyz):
    """///Summary: An XYZ as a [x, y, z] list in feet, or None."""
    if xyz is None:
        return None
    return [_feet(xyz.X), _feet(xyz.Y), _feet(xyz.Z)]


def _element_id(value, what):
    """///Summary: A user-supplied id as an ElementId, or ValueError saying why not."""
    try:
        return DB.ElementId(_to_int64(int(value)))
    except (TypeError, ValueError):
        raise ValueError("{0} must be a whole number; got '{1}'.".format(what, value))


def _box_corners(box, outer):
    """///Summary: The 8 corners of a BoundingBoxXYZ in host world coordinates.

    `outer` is the link's transform for a linked element, identity otherwise.
    """
    corners = []
    for x in (box.Min.X, box.Max.X):
        for y in (box.Min.Y, box.Max.Y):
            for z in (box.Min.Z, box.Max.Z):
                corners.append(outer.OfPoint(box.Transform.OfPoint(DB.XYZ(x, y, z))))
    return corners


def _extents(points):
    """///Summary: [min, max] of a point list per axis, as two [x, y, z] lists."""
    return ([_feet(min(p.X for p in points)), _feet(min(p.Y for p in points)),
             _feet(min(p.Z for p in points))],
            [_feet(max(p.X for p in points)), _feet(max(p.Y for p in points)),
             _feet(max(p.Z for p in points))])


# ---------------------------------------------------------------------------
# Finding the two things to compare
# ---------------------------------------------------------------------------

def _newest_section(doc):
    """///Summary: The section or detail view with the highest id, i.e. made last."""
    newest = None
    for view in DB.FilteredElementCollector(doc).OfClass(DB.ViewSection):
        if view.IsTemplate or view.ViewType not in _SECTION_TYPES:
            continue
        if newest is None or eid_int(view.Id) > eid_int(newest.Id):
            newest = view
    if newest is None:
        raise ValueError("This model has no section views yet. Create one with "
                         "the tool being measured, then ask again.")
    return newest


def _resolve_section(doc, section_id):
    """///Summary: The requested section, or the newest one."""
    if section_id is None:
        return _newest_section(doc)
    view = doc.GetElement(_element_id(section_id, "section_id"))
    if not isinstance(view, DB.ViewSection):
        raise ValueError("Element {0} is not a section view.".format(section_id))
    return view


def _loaded_link(doc, link_id):
    """///Summary: (link instance, link document) for an id, or ValueError."""
    link = doc.GetElement(_element_id(link_id, "link_id"))
    if not isinstance(link, DB.RevitLinkInstance):
        raise ValueError("Element {0} is not a Revit link instance.".format(link_id))
    link_doc = link.GetLinkDocument()
    if link_doc is None:
        raise ValueError("That link is not loaded. Reload it in Revit and retry.")
    return link, link_doc


def _selected_linked_reference(doc, uidoc):
    """///Summary: (link id, element id) of a single selected linked element, or None.

    Selection.GetReferences only exists from Revit 2023; before that the API sees
    a Tab-selected linked element as its whole link instance, so this returns
    None there and the caller searches the link instead.
    """
    get_references = getattr(uidoc.Selection, "GetReferences", None)
    if get_references is None:
        return None
    references = list(get_references())
    if len(references) != 1:
        return None
    reference = references[0]
    if eid_int(reference.LinkedElementId) < 0:
        return None
    return eid_int(reference.ElementId), eid_int(reference.LinkedElementId)


def _find_crossing(source_doc, transform, section, category_name):
    """///Summary: (score, element) for each model element in the middle of the section.

    Used when the selection cannot say which element was meant: Revit 2022
    cannot report what inside a link is selected, and the selection is often
    cleared by the time this runs. A section made from an element is built
    around it, so the element passes through the vertical slab at the middle of
    the crop's width, somewhere within its depth. That is not the cut plane:
    a section along a pipe holds the pipe inside its depth.

    The score is how far the element's centre sits from the centre of the
    crop in plan, in feet; lower is a likelier match. `transform` maps
    source_doc into the host. `category_name`, when given, keeps only that
    category (case-insensitive).
    """
    crop = section.CropBox
    frame = crop.Transform
    centre_x = (crop.Min.X + crop.Max.X) / 2.0
    centre_z = (crop.Min.Z + crop.Max.Z) / 2.0
    to_source = transform.Inverse
    ends = [to_source.OfPoint(frame.OfPoint(DB.XYZ(centre_x, y, z)))
            for y in (crop.Min.Y, crop.Max.Y) for z in (crop.Min.Z, crop.Max.Z)]
    # A little slack, so an element whose box merely touches the slab counts.
    pad = 0.1
    outline = DB.Outline(
        DB.XYZ(min(p.X for p in ends) - pad, min(p.Y for p in ends) - pad,
               min(p.Z for p in ends) - pad),
        DB.XYZ(max(p.X for p in ends) + pad, max(p.Y for p in ends) + pad,
               max(p.Z for p in ends) + pad))
    to_section = frame.Inverse
    wanted = category_name.strip().lower() if category_name else None

    found = []
    for element in (DB.FilteredElementCollector(source_doc)
                    .WherePasses(DB.BoundingBoxIntersectsFilter(outline))
                    .WhereElementIsNotElementType()):
        category = element.Category
        # Only modelled objects; levels, grids, rooms and the like are not what
        # a section is made from.
        if category is None or category.CategoryType != DB.CategoryType.Model:
            continue
        if wanted and category.Name.lower() != wanted:
            continue
        # Imported DWGs and view-specific elements (detail items) pass the
        # category test but are never what a section is made from. A flat DWG
        # also has zero volume, which would sort it ahead of everything real.
        if isinstance(element, DB.ImportInstance) or eid_int(element.OwnerViewId) > 0:
            continue
        box = element.get_BoundingBox(None)
        if box is None:
            continue
        size = box.Max - box.Min
        if min(size.X, size.Y, size.Z) < 1e-6:
            continue
        middle = to_section.OfPoint(transform.OfPoint(box.Transform.OfPoint(
            box.Min.Add(box.Max).Multiply(0.5))))
        offset = DB.XYZ(middle.X - centre_x, 0.0, middle.Z - centre_z)
        found.append((offset.GetLength(), element))
    return found


def _search(doc, section, links, category_name=None):
    """///Summary: Candidate elements as (element, transform, link name, score), best first.

    `links` is the link instances to search; `doc` itself is searched too when
    `links` is None, meaning "search everything".
    """
    sources = []
    if links is None:
        sources.append((doc, DB.Transform.Identity, None))
        links = list(DB.FilteredElementCollector(doc)
                     .OfClass(DB.RevitLinkInstance)
                     .WhereElementIsNotElementType())
    for link in links:
        link_doc = link.GetLinkDocument()
        if link_doc is not None:
            sources.append((link_doc, link.GetTotalTransform(), link_doc.Title))

    found = []
    for source_doc, transform, title in sources:
        for score, element in _find_crossing(source_doc, transform, section,
                                             category_name):
            found.append((score, element, transform, title))
    if not found:
        raise ValueError(
            "Nothing{0} sits in the middle of section '{1}'. Select the element "
            "it was made from (or pass element_id / link_id), then ask again."
            .format(" of category '{0}'".format(category_name) if category_name
                    else "", section.Name))
    found.sort(key=lambda row: row[0])
    return [(element, transform, title, score)
            for score, element, transform, title in found]


def _candidate_rows(matches):
    """///Summary: A short identity row per candidate, for confirming the guess.

    `off_centre` is the plan distance from the crop's centre, in feet.
    """
    rows = []
    for element, _, title, score in matches[:8]:
        rows.append({"id": eid_int(element.Id),
                     "category": element.Category.Name,
                     "type": _name_of(element.Document, element.GetTypeId()),
                     "link": title,
                     "off_centre": _feet(score)})
    return rows


def _resolve_element(doc, uidoc, element_id, link_id, section, category_name):
    """///Summary: (element, transform, link name, candidates) for the cut element.

    A linked element needs link_id as well, because its id belongs to the link's
    document. With no ids, the selection is used. When the selection cannot name
    the element (nothing selected, or a whole link selected on Revit 2022), the
    element the section passes through is searched for, and the runners-up are
    returned so the guess can be checked.
    """
    if element_id is None and link_id is None:
        linked = _selected_linked_reference(doc, uidoc)
        if linked is not None:
            link_id, element_id = linked
        else:
            ids = list(uidoc.Selection.GetElementIds())
            if len(ids) > 1:
                raise ValueError(
                    "{0} elements are selected. Select exactly the one element "
                    "the section was made from (or none, to have it found), "
                    "then ask again.".format(len(ids)))
            selected = doc.GetElement(ids[0]) if ids else None
            if selected is None:
                matches = _search(doc, section, None, category_name)
                element, transform, title, _ = matches[0]
                return element, transform, title, _candidate_rows(matches)
            if isinstance(selected, DB.RevitLinkInstance):
                link_id = eid_int(selected.Id)
            else:
                element_id = eid_int(selected.Id)

    if link_id is not None:
        link, link_doc = _loaded_link(doc, link_id)
        if element_id is None:
            matches = _search(doc, section, [link], category_name)
            element, transform, title, _ = matches[0]
            return element, transform, title, _candidate_rows(matches)
        else:
            element = link_doc.GetElement(_element_id(element_id, "element_id"))
            if element is None:
                raise ValueError("No element {0} in link '{1}'.".format(
                    element_id, link_doc.Title))
        return element, link.GetTotalTransform(), link_doc.Title, None

    element = doc.GetElement(_element_id(element_id, "element_id"))
    if element is None:
        raise ValueError("No element {0} in this model.".format(element_id))
    if isinstance(element, DB.RevitLinkInstance):
        raise ValueError("Element {0} is a whole Revit link. Pass it as link_id "
                         "instead.".format(element_id))
    return element, DB.Transform.Identity, None, None


# ---------------------------------------------------------------------------
# Describing each side
# ---------------------------------------------------------------------------

def _parameters(element):
    """///Summary: Every non-empty parameter as {name: display value}.

    Dumped wholesale on purpose: the point is to spot settings the other tool
    changes that nobody thought to ask about.
    """
    values = {}
    for parameter in element.Parameters:
        try:
            if not parameter.HasValue:
                continue
            if parameter.StorageType == DB.StorageType.String:
                text = parameter.AsString()
            else:
                text = parameter.AsValueString()
                if text is None and parameter.StorageType == DB.StorageType.ElementId:
                    text = str(eid_int(parameter.AsElementId()))
            if text not in (None, ""):
                values[parameter.Definition.Name] = text
        except Exception:
            continue
    return values


def _name_of(doc, element_id):
    """///Summary: The name of the element an id points to, or None."""
    if element_id is None or eid_int(element_id) < 0:
        return None
    element = doc.GetElement(element_id)
    return getattr(element, "Name", None) if element is not None else None


def _connectors(element):
    """///Summary: Connector origins and directions, for parts with no location line.

    Fabrication parts carry no LocationCurve, so their run direction has to be
    read off the connectors at either end.
    """
    manager = getattr(element, "ConnectorManager", None)
    if manager is None:
        mep_model = getattr(element, "MEPModel", None)
        manager = getattr(mep_model, "ConnectorManager", None)
    if manager is None:
        return None
    rows = []
    for connector in manager.Connectors:
        if len(rows) >= _MAX_CONNECTORS:
            break
        try:
            rows.append({"origin": _point(connector.Origin),
                         "direction": _point(connector.CoordinateSystem.BasisZ)})
        except Exception:
            # Logical connectors have no position.
            continue
    return rows


def _describe_element(element, transform, link_name):
    """///Summary: The element's identity, world box and location, in host coordinates."""
    owner = element.Document
    category = element.Category
    info = {
        "id": eid_int(element.Id),
        "class": type(element).__name__,
        "category": category.Name if category is not None else None,
        "type": _name_of(owner, element.GetTypeId()),
        "level": _name_of(owner, element.LevelId),
        "link": link_name,
    }

    box = element.get_BoundingBox(None)
    corners = _box_corners(box, transform) if box is not None else []
    if corners:
        info["bbox_min"], info["bbox_max"] = _extents(corners)

    location = element.Location
    curve = getattr(location, "Curve", None)
    point = getattr(location, "Point", None)
    if curve is not None:
        info["location_line"] = [_point(transform.OfPoint(curve.GetEndPoint(0))),
                                 _point(transform.OfPoint(curve.GetEndPoint(1)))]
        info["location_kind"] = type(curve).__name__
    elif point is not None:
        info["location_point"] = _point(transform.OfPoint(point))

    if isinstance(element, DB.Wall):
        info["wall_width"] = _feet(element.Width)

    connectors = _connectors(element)
    if connectors:
        for row in connectors:
            row["origin"] = _point(transform.OfPoint(DB.XYZ(*row["origin"])))
            row["direction"] = _point(transform.OfVector(DB.XYZ(*row["direction"])))
        info["connectors"] = connectors
    return info, corners


def _describe_section(doc, section):
    """///Summary: The section's frame, crop box and far clip, local and world."""
    crop = section.CropBox
    frame = crop.Transform
    world_corners = _box_corners(crop, DB.Transform.Identity)
    world_min, world_max = _extents(world_corners)
    view_type = doc.GetElement(section.GetTypeId())
    return {
        "id": eid_int(section.Id),
        "name": section.Name,
        "view_type": str(section.ViewType),
        "family_type": getattr(view_type, "Name", None),
        "template": _name_of(doc, section.ViewTemplateId),
        "scale": section.Scale,
        "crop_active": section.CropBoxActive,
        "crop_visible": section.CropBoxVisible,
        # Local frame: X runs along the section line, Y is up, Z is the view
        # depth axis. Min/Max are in that frame.
        "frame_origin": _point(frame.Origin),
        "frame_x": _point(frame.BasisX),
        "frame_y": _point(frame.BasisY),
        "frame_z": _point(frame.BasisZ),
        "crop_min_local": _point(crop.Min),
        "crop_max_local": _point(crop.Max),
        "size": {"width": _feet(crop.Max.X - crop.Min.X),
                 "height": _feet(crop.Max.Y - crop.Min.Y),
                 "depth": _feet(crop.Max.Z - crop.Min.Z)},
        "crop_world_min": world_min,
        "crop_world_max": world_max,
        "view_origin": _point(section.Origin),
        "view_direction": _point(section.ViewDirection),
        "right_direction": _point(section.RightDirection),
        "parameters": _parameters(section),
    }, frame


def _margins(frame, crop, element_corners):
    """///Summary: How far the crop box extends past the element on each side.

    Measured in the section's own frame, so "left" and "right" are as seen on
    screen in the section. Positive means the crop box is larger than the
    element on that side.
    """
    if not element_corners:
        return None
    inverse = frame.Inverse
    local = [inverse.OfPoint(p) for p in element_corners]
    low_x, high_x = min(p.X for p in local), max(p.X for p in local)
    low_y, high_y = min(p.Y for p in local), max(p.Y for p in local)
    low_z, high_z = min(p.Z for p in local), max(p.Z for p in local)
    return {
        "element_local_min": [_feet(low_x), _feet(low_y), _feet(low_z)],
        "element_local_max": [_feet(high_x), _feet(high_y), _feet(high_z)],
        "left": _feet(low_x - crop.Min.X),
        "right": _feet(crop.Max.X - high_x),
        "below": _feet(low_y - crop.Min.Y),
        "above": _feet(crop.Max.Y - high_y),
        "depth_min_side": _feet(low_z - crop.Min.Z),
        "depth_max_side": _feet(crop.Max.Z - high_z),
    }


def _levels(doc):
    """///Summary: Every level as [name, elevation], lowest first."""
    levels = list(DB.FilteredElementCollector(doc).OfClass(DB.Level)
                  .WhereElementIsNotElementType())
    levels.sort(key=lambda level: level.Elevation)
    return [[level.Name, _feet(level.Elevation)] for level in levels]


def _view_range(doc, plan):
    """///Summary: The plan's four view-range planes as raw level id + offset.

    Raw on purpose: negative ids are the Current / Above / Below / Unlimited
    sentinels, and seeing which one is set is part of what is being compared.
    """
    view_range = plan.GetViewRange()
    planes = {}
    for key, plane in (("top", DB.PlanViewPlane.TopClipPlane),
                       ("cut", DB.PlanViewPlane.CutPlane),
                       ("bottom", DB.PlanViewPlane.BottomClipPlane),
                       ("depth", DB.PlanViewPlane.ViewDepthPlane)):
        level_id = view_range.GetLevelId(plane)
        planes[key] = {"level_id": eid_int(level_id),
                       "level": _name_of(doc, level_id),
                       "offset": _feet(view_range.GetOffset(plane))}
    return {"id": eid_int(plan.Id), "name": plan.Name,
            "level": plan.GenLevel.Name if plan.GenLevel else None,
            "level_elevation": _feet(plan.GenLevel.Elevation) if plan.GenLevel else None,
            "planes": planes}


def _resolve_plan(doc, plan_view_id):
    """///Summary: The plan the section was cut from, or None if it cannot be told.

    After the other tool runs, the active view is usually the new section, so
    the plan is only guessed from the active view when that is a plan.
    """
    if plan_view_id is not None:
        view = doc.GetElement(_element_id(plan_view_id, "plan_view_id"))
        if not isinstance(view, DB.ViewPlan):
            raise ValueError("Element {0} is not a plan view.".format(plan_view_id))
        return view
    active = doc.ActiveView
    if isinstance(active, DB.ViewPlan) and active.ViewType in _PLAN_TYPES:
        return active
    return None


def inspect_section(doc, uidoc, section_id=None, element_id=None, link_id=None,
                    plan_view_id=None, category=None):
    """///Summary: The section, the element, and the margins between them.

    `category` narrows the search used when the selection cannot name the
    element. Raises ValueError with a user-facing message on bad input.
    """
    section = _resolve_section(doc, section_id)
    element, transform, link_name, candidates = _resolve_element(
        doc, uidoc, element_id, link_id, section, category)

    section_info, frame = _describe_section(doc, section)
    element_info, corners = _describe_element(element, transform, link_name)

    plan = _resolve_plan(doc, plan_view_id)
    return {
        "section": section_info,
        "element": element_info,
        # Set only when the element was found by searching a selected link:
        # check element.id is the one intended before trusting the margins.
        "candidates": candidates,
        "margins": _margins(frame, section.CropBox, corners),
        "plan": _view_range(doc, plan) if plan is not None else None,
        "levels": _levels(doc),
    }
