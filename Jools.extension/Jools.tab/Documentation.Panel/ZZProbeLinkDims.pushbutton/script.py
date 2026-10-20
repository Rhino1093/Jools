#! python3
"""TEMPORARY probe: can the API rebuild dimensions to linked grids and fixtures?

Delete this button once the answer is known. It changes nothing in the model:
every test dimension is created inside a transaction that is always rolled back.

The question it answers: when a reference lives in a Revit link, which way of
building it does NewDimension accept on this machine, and does the resulting
dimension measure the right thing? Forum reports disagree (CreateLinkReference
output rejected, a hand-rewritten ":0:RVTLINK:" string accepted, linked family
centre planes landing at the project origin), so the only trustworthy answer is
a comparison against dimensions Revit itself made.

How to use: in a ceiling plan, draw 1-3 dimensions by hand covering the real
mix (host fixture centre to linked grid, linked fixture to linked fixture,
...). Select them, run this, copy the output back.
"""

import re
import traceback

import joolslib          # Jools.extension/lib, see CLAUDE.md section 6a
joolslib.install_events_shim()   # must precede any pyrevit import

import clr  # type: ignore
clr.AddReference("RevitAPI")     # type: ignore
clr.AddReference("RevitAPIUI")   # type: ignore

from Autodesk.Revit import DB              # type: ignore
from Autodesk.Revit.UI import TaskDialog   # type: ignore

from pyrevit import revit, script

__author__ = "Ryan Johnston"

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
logger = script.get_logger()

TOOL_TITLE = "Probe: dimensions to linked elements"

# Two measured lengths closer than this (feet, ~1/256") count as the same.
# Catches the reported failure where a linked centre plane resolves to the
# project origin: the dimension is created, but measures the wrong distance.
LENGTH_TOLERANCE = 0.0003

# CreateLinkReference yields "<linkInstUid>:0:RVTLINK/<linkTypeUid>:<id>:..."
# (observed, Revit 2025); a hand-drawn dimension stores
# "<linkInstUid>:0:RVTLINK:<id>:...". Dropping "/<linkTypeUid>" turns the first
# into the second. UniqueIds contain no colons, so "[^:]+" consumes exactly the
# link type UniqueId. Fair59 (forum thread 8424237) reported the same fix for
# older Revit, where the leading ":0" was also missing.
_RVTLINK_TYPE_SEGMENT = re.compile(r"RVTLINK/[^:]+")

# Revit dimensions to a grid through its surface, not the element: hand-drawn
# strings end "<gridUid>:0:SURFACE". The element reference (Reference(grid))
# works for host grids but not after CreateLinkReference (probe run 1).
_GRID_SURFACE_SUFFIX = ":0:SURFACE"

# Half-length (feet) used to bound an unbound dimension line. Any length works;
# NewDimension only uses the line for position and direction.
_LINE_HALF_LENGTH = 50.0


class ProbeInputError(Exception):
    """///Summary: A problem the user can fix by changing what they selected."""


def get_dimensions():
    """///Summary: The selected linear dimensions, or ProbeInputError explaining
    what to select instead."""
    selected = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    dims = [e for e in selected if isinstance(e, DB.Dimension)]
    if not dims:
        raise ProbeInputError(
            "Select one or more dimensions you drew by hand, then run this again.\n\n"
            "Draw them in a ceiling plan, covering your real cases, e.g.:\n"
            "  1. A host fixture centre to a linked grid\n"
            "  2. A linked fixture centre to another linked fixture centre\n"
            "  3. A linked fixture centre to a host grid")
    linear = [d for d in dims if isinstance(d.Curve, DB.Line)]
    if not linear:
        raise ProbeInputError(
            "None of the selected dimensions are straight (linear) dimensions.\n\n"
            "Select aligned or linear dimensions and run this again.")
    return linear


def measured_lengths(dim):
    """///Summary: Every segment length of a dimension, in feet.

    Dimension.Value is null for a multi-segment string, so the per-segment
    values are read instead.
    """
    if dim.NumberOfSegments > 1:
        return [seg.Value for seg in dim.Segments]
    return [dim.Value]


def same_lengths(a, b):
    """///Summary: True when two length lists match within LENGTH_TOLERANCE."""
    if len(a) != len(b) or None in a or None in b:
        return False
    return all(abs(x - y) <= LENGTH_TOLERANCE for x, y in zip(a, b))


def fmt_lengths(lengths):
    """///Summary: Segment lengths as a short readable string, in decimal feet."""
    return ", ".join("?" if v is None else "{:.4f}'".format(v) for v in lengths)


def bounded_line(dim):
    """///Summary: The dimension's own line, bounded so NewDimension accepts it."""
    curve = dim.Curve
    if curve.IsBound:
        return curve
    offset = curve.Direction.Multiply(_LINE_HALF_LENGTH)
    return DB.Line.CreateBound(curve.Origin.Subtract(offset), curve.Origin.Add(offset))


def resolve_target(reference):
    """///Summary: The element a hand-drawn reference points at, and its link.

    Returns (element, link_instance). link_instance is None for host elements.
    A reference into a link is owned by the RevitLinkInstance; the real target
    is LinkedElementId inside that link's document.
    """
    owner = doc.GetElement(reference.ElementId)
    linked_id = joolslib.eid_int(reference.LinkedElementId)
    if isinstance(owner, DB.RevitLinkInstance) and linked_id >= 0:
        link_doc = owner.GetLinkDocument()
        if link_doc is None:
            return None, owner
        return link_doc.GetElement(reference.LinkedElementId), owner
    return owner, None


def center_reference_type(instance, link, direction):
    """///Summary: The fixture centre plane perpendicular to the dimension line.

    A dimension along `direction` measures between planes whose normals lie
    along it. CenterLeftRight separates left from right, so its normal is the
    instance's HandOrientation; CenterFrontBack's is FacingOrientation. A
    linked instance's orientation is in link coordinates and has to be mapped
    into the host before comparing.
    """
    hand = instance.HandOrientation
    facing = instance.FacingOrientation
    if link is not None:
        transform = link.GetTotalTransform()
        hand = transform.OfVector(hand)
        facing = transform.OfVector(facing)
    if abs(direction.DotProduct(hand)) >= abs(direction.DotProduct(facing)):
        return DB.FamilyInstanceReferenceType.CenterLeftRight
    return DB.FamilyInstanceReferenceType.CenterFrontBack


def first_reference(instance, ref_type):
    """///Summary: The first reference of a type, or None when the family has none."""
    refs = instance.GetReferences(ref_type)
    if refs is None or refs.Count == 0:
        return None
    return refs[0]


def api_candidates(element, link, direction):
    """///Summary: The reference the future engine would build for this element.

    Returns (kind, native_reference, note). native_reference is in the
    element's own document; None means the engine could not build one.
    """
    if isinstance(element, DB.Grid):
        if link is None:
            # Proven in probe run 1 (host grid, Revit 2025).
            return "Grid", DB.Reference(element), "element reference"
        # Parsed in the link's own document so CreateLinkReference can wrap it,
        # the same route that works for linked fixtures.
        surface = element.UniqueId + _GRID_SURFACE_SUFFIX
        try:
            ref = DB.Reference.ParseFromStableRepresentation(element.Document, surface)
        except Exception as ex:
            return "Grid", None, "surface parse failed: {}".format(one_line(ex))
        return "Grid", ref, "surface reference"

    if isinstance(element, DB.FamilyInstance):
        picked = center_reference_type(element, link, direction)
        ref = first_reference(element, picked)
        if ref is None:
            return "Fixture", None, "family has no {} plane".format(picked)
        return "Fixture", ref, "{} (picked by direction)".format(picked)

    kind = type(element).__name__ if element is not None else "unresolved"
    return kind, None, "not a grid or fixture; hand-drawn reference reused"


def one_line(ex):
    """///Summary: An exception as single-line text.

    Revit exception messages can contain line breaks, and a line break inside a
    markdown table cell splits the row; run 1 of this probe lost two result
    rows that way, most likely.
    """
    return "{}: {}".format(type(ex).__name__, " ".join(str(ex).split()))[:200]


def normalized(stable_string):
    """///Summary: A stable string with the trailing ":SURFACE" dropped.

    Run 1 showed hand-drawn fixture strings end "...:INSTANCE:<id>:4" while the
    API's end "...:INSTANCE:<id>:4:SURFACE", and both dimension correctly. The
    suffix is noise for the "same reference?" question.
    """
    if stable_string.endswith(":SURFACE"):
        return stable_string[:-len(":SURFACE")]
    return stable_string


def stable(reference):
    """///Summary: A host-side reference as its stable string, or the error text."""
    try:
        return reference.ConvertToStableRepresentation(doc)
    except Exception as ex:
        return "ERROR " + one_line(ex)


def to_host(native_ref, link):
    """///Summary: Turn a native reference into the two host-side variants under test.

    Returns (raw, rewritten, rewritten_string). For a host element both
    variants are the reference itself. For a linked one, raw is
    CreateLinkReference as-is and rewritten is Fair59's string fix re-parsed.
    """
    if link is None:
        return native_ref, native_ref, None

    raw = native_ref.CreateLinkReference(link)
    raw_string = stable(raw)
    rewritten_string = _RVTLINK_TYPE_SEGMENT.sub("RVTLINK", raw_string, count=1)
    try:
        rewritten = DB.Reference.ParseFromStableRepresentation(doc, rewritten_string)
    except Exception as ex:
        logger.debug("parse failed for %s: %s", rewritten_string, ex)
        rewritten = None
    return raw, rewritten, rewritten_string


def try_dimension(view, line, references, dim_type):
    """///Summary: Attempt one dimension and roll it back. Returns (ok, lengths_or_error).

    ClearAfterRollback is set because, per the API docs, failures posted by a
    rolled-back transaction "may still be displayed to the user" otherwise.
    """
    if any(r is None for r in references):
        return False, "a reference could not be built"

    array = DB.ReferenceArray()
    for r in references:
        array.Append(r)

    transaction = DB.Transaction(doc, TOOL_TITLE)
    options = transaction.GetFailureHandlingOptions()
    options.SetClearAfterRollback(True)
    transaction.SetFailureHandlingOptions(options)
    transaction.Start()
    try:
        dim = doc.Create.NewDimension(view, line, array, dim_type)
        if dim is None:
            return False, "NewDimension returned nothing"
        doc.Regenerate()
        return True, measured_lengths(dim)
    except Exception as ex:
        return False, one_line(ex)
    finally:
        transaction.RollBack()


def probe_dimension(dim):
    """///Summary: Report one hand-drawn dimension and rebuild it three ways."""
    view = doc.GetElement(dim.OwnerViewId)
    line = bounded_line(dim)
    direction = dim.Curve.Direction
    expected = measured_lengths(dim)

    output.print_md("### Dimension {} in '{}' ({})".format(
        joolslib.eid_int(dim.Id), view.Name, view.ViewType))
    output.print_md("Hand-drawn lengths: **{}**".format(fmt_lengths(expected)))

    control, raw_refs, rewritten_refs = [], [], []
    rows = []
    for index, hand_ref in enumerate(dim.References, 1):
        hand_string = stable(hand_ref)
        element, link = resolve_target(hand_ref)
        kind, native_ref, note = api_candidates(element, link, direction)

        # The control rebuilds from Revit's own strings: if it fails, the test
        # harness (line, view, type) is at fault, not the reference method.
        # A string that will not parse becomes None, which fails only the
        # control row instead of aborting this dimension.
        try:
            control.append(DB.Reference.ParseFromStableRepresentation(doc, hand_string))
        except Exception as ex:
            logger.debug("control parse failed for %s: %s", hand_string, ex)
            control.append(None)

        if native_ref is None:
            raw_refs.append(hand_ref)
            rewritten_refs.append(hand_ref)
            raw_string = rewritten_string = "-"
            matches = "n/a"
        else:
            raw, rewritten, rewritten_string = to_host(native_ref, link)
            raw_refs.append(raw)
            rewritten_refs.append(rewritten)
            raw_string = stable(raw)
            rewritten_string = rewritten_string or "(host: same as raw)"
            matches = "yes" if normalized(hand_string) in (
                normalized(raw_string), normalized(rewritten_string)) else "no"

        rows.append([
            index,
            link.Name if link is not None else "host",
            "{} {}".format(kind, joolslib.eid_int(element.Id)
                           if element is not None else "?"),
            note,
            "`{}`".format(hand_string),
            "`{}`".format(raw_string),
            "`{}`".format(rewritten_string),
            matches,
        ])

    output.print_md(joolslib.markdown_table(rows, [
        "#", "Source", "Element", "Engine choice", "Hand-drawn string",
        "API raw string", "API rewritten string", "Matches hand-drawn"]))

    results = []
    for label, refs in (("Control (hand-drawn strings)", control),
                        ("API raw (CreateLinkReference)", raw_refs),
                        ("API rewritten (:0:RVTLINK:)", rewritten_refs)):
        ok, detail = try_dimension(view, line, refs, dim.DimensionType)
        if not ok:
            results.append([label, "FAILED", detail])
        elif same_lengths(detail, expected):
            results.append([label, "OK", "same lengths: " + fmt_lengths(detail)])
        else:
            results.append([label, "WRONG LENGTH", fmt_lengths(detail)])

    output.print_md(joolslib.markdown_table(results, ["Method", "Result", "Detail"]))


def main():
    try:
        dims = get_dimensions()
    except ProbeInputError as user_error:
        TaskDialog.Show(TOOL_TITLE, str(user_error))
        return

    output.print_md("## {}".format(TOOL_TITLE))
    output.print_md("Revit **{}**, {} dimension(s). Nothing is kept in the model.".format(
        doc.Application.VersionNumber, len(dims)))

    for dim in dims:
        try:
            probe_dimension(dim)
        except Exception:
            output.print_md("Dimension {} could not be probed:".format(
                joolslib.eid_int(dim.Id)))
            output.print_code(traceback.format_exc())

    output.print_md("Copy this whole panel back to Claude.")


if __name__ == "__main__":
    main()
