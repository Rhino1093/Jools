#! python3
"""Create a section through a picked object, sized from the object and the plan's view range."""

import traceback

import clr  # type: ignore
clr.AddReference("RevitAPI")     # type: ignore
clr.AddReference("RevitAPIUI")   # type: ignore

from Autodesk.Revit import DB  # type: ignore
from Autodesk.Revit.UI import TaskDialog  # type: ignore
from Autodesk.Revit.UI.Selection import ObjectType  # type: ignore
from Autodesk.Revit.Exceptions import OperationCanceledException  # type: ignore

import joolslib          # Jools.extension/lib, see CLAUDE.md section 6a
joolslib.install_events_shim()   # must precede any pyrevit import

from pyrevit import revit, script

__author__ = "Ryan Johnston"

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
logger = script.get_logger()

TOOL_TITLE = "Section by Object"

# Plan views this makes sense in. Anything else has no view range to read a top
# and bottom out of.
PLAN_VIEW_TYPES = (
    DB.ViewType.FloorPlan,
    DB.ViewType.CeilingPlan,
    DB.ViewType.EngineeringPlan,
)

# Integer values behind the sentinel ElementIds that PlanViewRange.GetLevelId
# returns in place of a real level (DB.PlanViewRangeLevel). Every real level id
# is positive, so a negative value is always one of these.
PLAN_RANGE_UNLIMITED = -1
PLAN_RANGE_ABOVE = -2
PLAN_RANGE_CURRENT = -3
PLAN_RANGE_BELOW = -4

# Fallback when Shift+Click has never been used. Feet.
DEFAULT_OFFSET_FT = 1.0

# Revit rejects a section box whose near and far bounds are "too close to each
# other". Anything thinner than this in any direction gets padded out to it.
MIN_EXTENT_FT = 0.5

# Log the computed box instead of creating the view.
DRY_RUN = False


class SectionInputError(Exception):
    """///Summary: The picked object or view cannot produce a section.

    Carries a message written for the user, not a stack trace: main() shows it in
    a TaskDialog and stops.
    """


# ---------------------------------------------------------------------------
# Settings, shared with config.py via the bundle's config section
# ---------------------------------------------------------------------------

def load_settings():
    """///Summary: Offset and section type chosen in the Shift+Click panel.

    script.get_config() keys off the command name, which is the same for
    script.py and config.py in one bundle, so both see the same section.
    """
    settings = {"offset_ft": DEFAULT_OFFSET_FT, "view_type_name": ""}
    try:
        config = script.get_config()
        settings["offset_ft"] = float(
            config.get_option("offset_ft", DEFAULT_OFFSET_FT))
        settings["view_type_name"] = config.get_option("view_type_name", "")
    except Exception as ex:
        logger.debug("could not read saved settings: %s", ex)
    return settings


def section_view_types(host_doc):
    """///Summary: Every ViewFamilyType that CreateSection will accept."""
    types = [vft for vft in
             DB.FilteredElementCollector(host_doc).OfClass(DB.ViewFamilyType)
             if vft.ViewFamily == DB.ViewFamily.Section]
    types.sort(key=lambda t: t.Name.lower())
    return types


def resolve_view_type(host_doc, wanted_name):
    """///Summary: The saved section type, or the first one in the project."""
    types = section_view_types(host_doc)
    if not types:
        raise SectionInputError(
            "This project has no Section view type.\n\nAdd one under View > "
            "View Types, or duplicate an existing section type, then run this "
            "again.")
    if wanted_name:
        for view_type in types:
            if view_type.Name == wanted_name:
                return view_type
        logger.debug("saved section type %r is gone; using the first instead",
                     wanted_name)
    return types[0]


# ---------------------------------------------------------------------------
# Reading the plan's view range
# ---------------------------------------------------------------------------

def _levels_sorted(host_doc):
    """///Summary: Every level in the document, lowest first."""
    levels = list(DB.FilteredElementCollector(host_doc)
                  .OfClass(DB.Level)
                  .WhereElementIsNotElementType())
    levels.sort(key=lambda lv: lv.Elevation)
    return levels


def _neighbour_level(host_doc, base_level, direction):
    """///Summary: The next level above (+1) or below (-1) base_level.

    None when base_level is already the topmost or bottommost, which is the case
    the level-above/level-below fallback cannot cover.
    """
    if base_level is None:
        return None
    levels = _levels_sorted(host_doc)
    base_elevation = base_level.Elevation
    if direction > 0:
        higher = [lv for lv in levels if lv.Elevation > base_elevation + 1e-9]
        return higher[0] if higher else None
    lower = [lv for lv in levels if lv.Elevation < base_elevation - 1e-9]
    return lower[-1] if lower else None


class PlaneResult(object):
    """///Summary: One resolved view-range plane, and how it was resolved."""

    def __init__(self, elevation, note=None):
        self.elevation = elevation
        self.note = note


def resolve_plane(host_doc, view, view_range, plane, direction, fallback):
    """///Summary: Elevation of one view-range plane, in internal units.

    GetLevelId hands back a sentinel rather than a level for the relative
    settings, so each one is mapped to a real level first:

        Current       -> the plan's own level
        Level Above    -> the next level up
        Level Below    -> the next level down
        Unlimited      -> the neighbour level in `direction`, and if the plan sits
                          on the topmost or bottommost level there is no such
                          level, so `fallback` (the object's own extent) is used

    `direction` is +1 for the top plane and -1 for the bottom.
    """
    level_id = view_range.GetLevelId(plane)
    offset = view_range.GetOffset(plane)
    raw = joolslib.eid_int(level_id)
    note = None

    if raw >= 0:
        level = host_doc.GetElement(level_id)
    elif raw == PLAN_RANGE_CURRENT:
        level = view.GenLevel
    elif raw == PLAN_RANGE_ABOVE:
        level = _neighbour_level(host_doc, view.GenLevel, 1)
    elif raw == PLAN_RANGE_BELOW:
        level = _neighbour_level(host_doc, view.GenLevel, -1)
    elif raw == PLAN_RANGE_UNLIMITED:
        level = _neighbour_level(host_doc, view.GenLevel, direction)
        # Unlimited carries no meaningful offset of its own.
        offset = 0.0
        if level is None:
            return PlaneResult(
                fallback,
                "{} clip is Unlimited and {} is the {} level in the project, so "
                "the object's own extent plus the offset was used instead."
                .format("Top" if direction > 0 else "Bottom",
                        _view_level_name(view),
                        "highest" if direction > 0 else "lowest"))
        note = ("{} clip is Unlimited, so the level {} ({}) was used."
                .format("Top" if direction > 0 else "Bottom",
                        "above" if direction > 0 else "below",
                        level.Name))
    else:
        level = None

    if level is None:
        return PlaneResult(
            fallback,
            "{} clip could not be resolved to a level, so the object's own "
            "extent plus the offset was used instead."
            .format("Top" if direction > 0 else "Bottom"))

    return PlaneResult(level.Elevation + offset, note)


def _view_level_name(view):
    """///Summary: Name of the plan's level, for a message."""
    level = view.GenLevel
    return level.Name if level else "this view"


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def _flatten(vector):
    """///Summary: A vector projected onto the XY plane and normalised.

    Returns None when the vector is essentially vertical, which leaves no
    horizontal direction to build a section from.
    """
    flat = DB.XYZ(vector.X, vector.Y, 0)
    if flat.GetLength() < 1e-9:
        return None
    return flat.Normalize()


def _bbox_corners(bbox, link_transform):
    """///Summary: The 8 corners of a BoundingBoxXYZ, in host coordinates.

    Two transforms stack here: the box's own, and the link instance's for an
    element that lives in a linked document. The second is the identity for a
    host element.
    """
    transform = bbox.Transform
    low, high = bbox.Min, bbox.Max
    corners = []
    for x in (low.X, high.X):
        for y in (low.Y, high.Y):
            for z in (low.Z, high.Z):
                corners.append(
                    link_transform.OfPoint(transform.OfPoint(DB.XYZ(x, y, z))))
    return corners


def element_axis(picked):
    """///Summary: The element's own horizontal axis, if it has one.

    A wall, beam, pipe or duct carries a LocationCurve, and a section across one
    of those should run parallel to it — that is what makes a 10 ft wall produce a
    12 ft section rather than a section of its diagonal bounding box. Curved
    location lines have no single direction, so they fall back to None and the
    caller uses the bounding box axes instead.

    OfVector rather than OfPoint: a direction is not a position, and running it
    through the link's translation as well would skew the axis.
    """
    location = getattr(picked.element, "Location", None)
    curve = getattr(location, "Curve", None)
    if curve is None:
        return None
    if not isinstance(curve, DB.Line):
        logger.debug("location curve is %s, not a Line; using bbox axes",
                     type(curve).__name__)
        return None
    return _flatten(picked.transform.OfVector(curve.Direction))


def choose_view_direction(candidates, centre, click):
    """///Summary: Which candidate direction looks from the click at the object.

    The viewer stands where the user clicked, so the direction the section looks
    is the reverse of centre-to-click. Snapping that to the candidate axes is
    what keeps the section square to the object instead of skewed by a slightly
    off click.
    """
    to_click = _flatten(click - centre)
    if to_click is None:
        # Clicked dead on the object's centre: nothing to infer a side from.
        return candidates[0]
    looking = to_click.Negate()
    return max(candidates, key=lambda c: c.DotProduct(looking))


def build_section_box(picked, view, click, offset, host_doc):
    """///Summary: The BoundingBoxXYZ that defines the new section.

    Assembles a right-handed frame where BasisX runs along the section line,
    BasisY is world up, and BasisZ is the direction the section looks. Returns
    the box plus the notes describing any view-range bound that was substituted.
    """
    bbox = picked.element.get_BoundingBox(None)
    if bbox is None:
        raise SectionInputError(
            "That element has no geometry to measure, so there is nothing to "
            "size a section from.\n\nPick a modelled object such as a wall, "
            "duct or piece of equipment.")

    corners = _bbox_corners(bbox, picked.transform)
    centre = DB.XYZ(
        sum(c.X for c in corners) / len(corners),
        sum(c.Y for c in corners) / len(corners),
        sum(c.Z for c in corners) / len(corners))

    # Candidate view directions: perpendicular to the element's own axis when it
    # has one, otherwise the four world-aligned bounding box faces.
    axis = element_axis(picked)
    if axis is not None:
        perpendicular = _flatten(DB.XYZ.BasisZ.CrossProduct(axis))
        candidates = [perpendicular, perpendicular.Negate()]
    else:
        candidates = [DB.XYZ.BasisX, DB.XYZ.BasisX.Negate(),
                      DB.XYZ.BasisY, DB.XYZ.BasisY.Negate()]

    view_dir = choose_view_direction(candidates, centre, click)

    # BasisZ is the direction the section looks, running from the side clicked
    # into the object. Reading View.ViewDirection ("the direction towards the
    # viewer") suggests the opposite, but the section box does not use that
    # convention: built the other way round, every section came out facing away
    # from the side that was clicked.
    basis_z = view_dir
    basis_y = DB.XYZ.BasisZ
    basis_x = basis_y.CrossProduct(basis_z).Normalize()

    # Extent along the section line. For an element with a location line the
    # line itself is the honest length; the world bounding box of an angled wall
    # is its diagonal and would overshoot.
    along = _extent_along(picked, corners, centre, basis_x, axis)
    across = _extent_along(picked, corners, centre, basis_z, None)
    across = _wall_thickness_override(picked.element, across)

    half_width = _pad(along) + offset
    half_depth = _pad(across) + offset

    # Vertical extent comes from the plan's view range, with the object's own
    # top and bottom as the fallback for an unresolvable bound.
    view_range = view.GetViewRange()
    object_top = max(c.Z for c in corners)
    object_bottom = min(c.Z for c in corners)
    top = resolve_plane(host_doc, view, view_range, DB.PlanViewPlane.TopClipPlane,
                        1, object_top + offset)
    bottom = resolve_plane(host_doc, view, view_range,
                           DB.PlanViewPlane.BottomClipPlane, -1,
                           object_bottom - offset)

    top_z, bottom_z = top.elevation, bottom.elevation
    if top_z < bottom_z:
        top_z, bottom_z = bottom_z, top_z
    if top_z - bottom_z < MIN_EXTENT_FT:
        middle = (top_z + bottom_z) / 2.0
        top_z, bottom_z = middle + MIN_EXTENT_FT / 2.0, middle - MIN_EXTENT_FT / 2.0

    origin = DB.XYZ(centre.X, centre.Y, (top_z + bottom_z) / 2.0)
    half_height = (top_z - bottom_z) / 2.0

    transform = DB.Transform.Identity
    transform.Origin = origin
    transform.BasisX = basis_x
    transform.BasisY = basis_y
    transform.BasisZ = basis_z

    box = DB.BoundingBoxXYZ()
    box.Transform = transform
    # The depth range is symmetric about the object on purpose. Which end of it
    # Revit treats as the near clip is a convention worth not depending on;
    # centring the object means the section is right either way, and BasisZ is
    # what actually decides the side it is viewed from.
    box.Min = DB.XYZ(-half_width, -half_height, -half_depth)
    box.Max = DB.XYZ(half_width, half_height, half_depth)

    notes = [plane.note for plane in (top, bottom) if plane.note]
    return box, notes, {
        "width": half_width * 2.0,
        "height": half_height * 2.0,
        "depth": half_depth * 2.0,
        "top": top_z,
        "bottom": bottom_z,
    }


def _extent_along(picked, corners, centre, direction, axis):
    """///Summary: Half the element's size along `direction`, measured from centre.

    Uses the location line when `axis` is the direction being measured, because a
    world bounding box overstates the length of anything not running square to
    the project.
    """
    if axis is not None and abs(direction.DotProduct(axis)) > 0.999:
        location = getattr(picked.element, "Location", None)
        curve = getattr(location, "Curve", None)
        if curve is not None:
            ends = [picked.transform.OfPoint(curve.GetEndPoint(0)),
                    picked.transform.OfPoint(curve.GetEndPoint(1))]
            spans = [(point - centre).DotProduct(direction) for point in ends]
            return max(abs(min(spans)), abs(max(spans)))
    spans = [(corner - centre).DotProduct(direction) for corner in corners]
    return max(abs(min(spans)), abs(max(spans)))


def _wall_thickness_override(element, across):
    """///Summary: A wall's real thickness beats its world bounding box.

    An angled wall's bounding box is the diagonal box around it, so measuring
    depth off the box makes the section deeper than the wall. Wall.Width is the
    actual thickness.
    """
    if not isinstance(element, DB.Wall):
        return across
    try:
        return min(across, element.Width / 2.0)
    except Exception as ex:
        logger.debug("could not read Wall.Width: %s", ex)
        return across


def _pad(half_extent):
    """///Summary: Keep a half extent above Revit's minimum for a section box."""
    return max(half_extent, MIN_EXTENT_FT / 2.0)


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

def require_plan_view(view):
    """///Summary: Stop unless the active view is a plan with a view range."""
    if not isinstance(view, DB.ViewPlan) or view.ViewType not in PLAN_VIEW_TYPES:
        raise SectionInputError(
            "This works from a floor plan, ceiling plan or structural plan.\n\n"
            "The active view is a {}. Open a plan view and run it again."
            .format(view.ViewType))
    if view.IsTemplate:
        raise SectionInputError(
            "The active view is a view template, not a view.\n\nOpen a real "
            "plan view and run this again.")


class PickedObject(object):
    """///Summary: The element to section, and its transform into host coordinates.

    A linked element's geometry is expressed in its own document's coordinates,
    so every point and direction measured off it has to pass through the link
    instance's total transform before it can size a section in the host. For a
    host element that transform is the identity and nothing moves.
    """

    def __init__(self, element, transform, link_name=None):
        self.element = element
        self.transform = transform
        self.link_name = link_name

    @property
    def is_linked(self):
        return self.link_name is not None


def _as_linked(reference):
    """///Summary: Resolve a reference that lands inside a Revit link.

    Returns None when the reference is not one — either it is a host element, or
    it is the link instance as a whole rather than something inside it.
    """
    link_instance = doc.GetElement(reference.ElementId)
    if not isinstance(link_instance, DB.RevitLinkInstance):
        return None
    if joolslib.eid_int(reference.LinkedElementId) < 0:
        return None

    link_doc = link_instance.GetLinkDocument()
    if link_doc is None:
        raise SectionInputError(
            "That link is not loaded, so its geometry cannot be measured.\n\n"
            "Reload the link, then run this again.")
    element = link_doc.GetElement(reference.LinkedElementId)
    if element is None:
        return None
    return PickedObject(element, link_instance.GetTotalTransform(),
                        link_doc.Title)


def _pick_inside_link():
    """///Summary: Second pick, restricted to the contents of links.

    ObjectType.Element treats a link as one object, so clicking a link selects
    the whole model. ObjectType.LinkedElement is the only object type that
    reaches the elements inside one, and it reaches nothing else — hence the
    two-step pick rather than one.
    """
    try:
        reference = uidoc.Selection.PickObject(
            ObjectType.LinkedElement,
            "Now click the element inside that link")
    except OperationCanceledException:
        return None
    picked = _as_linked(reference)
    if picked is None:
        raise SectionInputError(
            "That click did not land on an element inside a link.\n\nRun it "
            "again and click directly on a modelled object in the link.")
    return picked


def pick_element():
    """///Summary: The object to section, from the host model or a link.

    Returns None if the user presses Escape.
    """
    preselected = list(uidoc.Selection.GetElementIds())
    if len(preselected) > 1:
        raise SectionInputError(
            "{} objects are selected.\n\nThis sections one object at a time. "
            "Clear the selection, or select just one, then run it again."
            .format(len(preselected)))
    if len(preselected) == 1:
        element = doc.GetElement(preselected[0])
        # A selected link is the whole linked model, not an object inside it, so
        # fall through to picking rather than sectioning an entire building.
        if element is not None and not isinstance(element, DB.RevitLinkInstance):
            logger.debug("using pre-selected element %s",
                         joolslib.eid_int(element.Id))
            return PickedObject(element, DB.Transform.Identity)

    try:
        reference = uidoc.Selection.PickObject(
            ObjectType.Element,
            "Select the object to section, or click a link to pick inside it")
    except OperationCanceledException:
        return None

    # Some picks on link geometry already carry the element inside it; those
    # need no second click.
    picked = _as_linked(reference)
    if picked is not None:
        return picked

    element = doc.GetElement(reference.ElementId)
    if isinstance(element, DB.RevitLinkInstance):
        return _pick_inside_link()
    if element is None:
        raise SectionInputError(
            "That pick did not resolve to an element.\n\nRun it again and click "
            "directly on a modelled object.")
    return PickedObject(element, DB.Transform.Identity)


def pick_side(picked):
    """///Summary: A point on the side the section should look from.

    Returns None if the user presses Escape.
    """
    prompt = "Click the side of the {} the section should look from".format(
        _element_label(picked.element))
    try:
        return uidoc.Selection.PickPoint(prompt)
    except OperationCanceledException:
        return None
    except Exception as ex:
        # PickPoint needs a work plane. A plan view always has one, so this is a
        # genuinely odd view rather than a user mistake.
        raise SectionInputError(
            "Revit would not accept a point pick in this view.\n\n{}".format(ex))


def _element_label(element):
    """///Summary: Something readable to call the element in a prompt."""
    category = element.Category
    if category is not None:
        name = category.Name
        # Singularise by dropping one trailing "s" only. rstrip("s") would turn
        # "Glass" into "Gla" and "Trusses" into "Trusse".
        if name.endswith("s"):
            name = name[:-1]
        return name.lower()
    return "object"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if doc is None:
        TaskDialog.Show(TOOL_TITLE, "Open a project first, then run this again.")
        return

    view = doc.ActiveView
    settings = load_settings()

    try:
        require_plan_view(view)
        view_type = resolve_view_type(doc, settings["view_type_name"])

        picked = pick_element()
        if picked is None:
            return
        click = pick_side(picked)
        if click is None:
            return

        box, notes, measured = build_section_box(
            picked, view, click, settings["offset_ft"], doc)
    except SectionInputError as ex:
        TaskDialog.Show(TOOL_TITLE, str(ex))
        return

    if DRY_RUN:
        logger.info("DRY_RUN: %s section %.2f w x %.2f h x %.2f d, top %.2f, "
                    "bottom %.2f", view_type.Name,
                    measured["width"], measured["height"], measured["depth"],
                    measured["top"], measured["bottom"])
        return

    try:
        with revit.Transaction(TOOL_TITLE):
            section = DB.ViewSection.CreateSection(doc, view_type.Id, box)
            # The extents only actually bound the view once the crop is on, and
            # the section type's own template may leave it off.
            section.CropBoxActive = True
    except Exception as ex:
        logger.error("CreateSection failed: %s", ex)
        TaskDialog.Show(
            TOOL_TITLE,
            "Revit would not create the section, so nothing was changed.\n\n"
            "{}\n\nThis usually means the object is too thin in one direction "
            "for a section box. Try a larger offset from Shift+Click on this "
            "button.".format(ex))
        return

    # Outside the transaction: the view has to exist and be committed before
    # Revit will switch to it.
    uidoc.RequestViewChange(section)

    _report(section, view, picked, view_type, measured, notes)


def _report(section, source_view, picked, view_type, measured, notes):
    """///Summary: What was made, in the pyRevit output window."""
    output.print_md("## {}".format(TOOL_TITLE))
    output.print_md("- Created **{}** ({}) from **{}**.".format(
        section.Name,
        view_type.Name,
        source_view.Name))
    if picked.is_linked:
        # No linkify: a linked element's id belongs to the link's document, and
        # a link built from it would select the wrong element in this one.
        output.print_md("- Sectioned {} id {} in link **{}**.".format(
            _element_label(picked.element),
            joolslib.eid_int(picked.element.Id), picked.link_name))
    else:
        output.print_md("- Sectioned {} {}.".format(
            _element_label(picked.element),
            output.linkify(picked.element.Id)))
    output.print_md("- Extents: {} wide, {} tall, {} deep.".format(
        _feet_inches(measured["width"]),
        _feet_inches(measured["height"]),
        _feet_inches(measured["depth"])))
    output.print_md("- Top at {}, bottom at {}.".format(
        _feet_inches(measured["top"]), _feet_inches(measured["bottom"])))
    for note in notes:
        output.print_md("- **Note:** {}".format(note))


def _feet_inches(value):
    """///Summary: Internal feet as a plain feet-and-inches string."""
    negative = value < 0
    total_inches = round(abs(value) * 12.0)
    feet, inches = divmod(int(total_inches), 12)
    text = "{}'-{}\"".format(feet, inches)
    return "-" + text if negative else text


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # A .NET exception on its way back out of the engine otherwise reaches
        # the user as a bare "Object reference not set to an instance of an
        # object" naming no file and no line. Print the real stack first.
        output.print_md("### {} failed".format(TOOL_TITLE))
        output.print_code(traceback.format_exc())
        logger.error("%s failed\n%s", TOOL_TITLE, traceback.format_exc())
        raise
