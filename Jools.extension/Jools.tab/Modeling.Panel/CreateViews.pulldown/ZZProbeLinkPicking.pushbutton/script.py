#! python3
"""TEMPORARY probe: which ObjectType can actually pick geometry inside a link?

Delete this button once the answer is known. It changes nothing in the model; it
only asks Revit to let you click a linked object five different ways and reports
what came back each time.
"""

import traceback

import clr  # type: ignore
clr.AddReference("RevitAPI")     # type: ignore
clr.AddReference("RevitAPIUI")   # type: ignore

from Autodesk.Revit import DB  # type: ignore
from Autodesk.Revit.UI import TaskDialog, SelectionUIOptions  # type: ignore
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter  # type: ignore
from Autodesk.Revit.Exceptions import OperationCanceledException  # type: ignore

import joolslib          # Jools.extension/lib, see CLAUDE.md section 6a
joolslib.install_events_shim()   # must precede any pyrevit import

from pyrevit import revit, script

__author__ = "Ryan Johnston"

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
logger = script.get_logger()

TOOL_TITLE = "Probe: picking inside links"

_NS = joolslib.unique_namespace("ZZProbeLinkPicking")


class AllowAllFilter(ISelectionFilter):
    """///Summary: Accepts everything, including RevitLinkInstance."""

    __namespace__ = _NS

    def AllowElement(self, element):
        return True

    def AllowReference(self, reference, position):
        return True


def _probes():
    """///Summary: The object types worth trying, in order."""
    return [
        ("Edge, no filter", ObjectType.Edge, False),
        ("Edge, with filter", ObjectType.Edge, True),
        ("Face, with filter", ObjectType.Face, True),
        ("PointOnElement, with filter", ObjectType.PointOnElement, True),
        ("LinkedElement, no filter", ObjectType.LinkedElement, False),
    ]


def _describe(reference):
    """///Summary: What a returned Reference actually points at."""
    owner = doc.GetElement(reference.ElementId)
    owner_kind = type(owner).__name__ if owner is not None else "None"
    linked_raw = joolslib.eid_int(reference.LinkedElementId)

    detail = "owner={} ({}), LinkedElementId={}, refType={}".format(
        joolslib.eid_int(reference.ElementId), owner_kind, linked_raw,
        reference.ElementReferenceType)

    if isinstance(owner, DB.RevitLinkInstance) and linked_raw >= 0:
        link_doc = owner.GetLinkDocument()
        element = link_doc.GetElement(reference.LinkedElementId) if link_doc else None
        detail += ", linked element={}".format(
            type(element).__name__ if element is not None else "None")
        # The geometry lookup the real tool depends on for its axis.
        try:
            geometry = element.GetGeometryObjectFromReference(
                reference.CreateReferenceInLink())
            detail += ", geometry={}".format(
                type(geometry).__name__ if geometry is not None else "None")
        except Exception as ex:
            detail += ", geometry FAILED: {}".format(type(ex).__name__)
    return detail


def _toggle_report():
    """///Summary: The selection toggles as the user currently has them."""
    rows = []
    try:
        options = SelectionUIOptions.GetSelectionUIOptions()
        for name in ("SelectLinks", "SelectPinned", "SelectFaces",
                     "SelectUnderlay"):
            rows.append([name, str(getattr(options, name))])
    except Exception as ex:
        rows.append(["could not read options", str(ex)])
    return rows


def _link_report():
    """///Summary: Every link instance, whether loaded, and whether pinned."""
    rows = []
    for instance in (DB.FilteredElementCollector(doc)
                     .OfClass(DB.RevitLinkInstance)
                     .WhereElementIsNotElementType()):
        try:
            link_doc = instance.GetLinkDocument()
            title = link_doc.Title if link_doc else "(not loaded)"
        except Exception as ex:
            title = "(unreadable: {})".format(ex)
        try:
            pinned = str(instance.Pinned)
        except Exception:
            pinned = "?"
        try:
            as_pinned = str(SelectionUIOptions.GetSelectionUIOptions()
                            .ElementSelectsAsPinned(doc, instance))
        except Exception:
            as_pinned = "?"
        rows.append([title, pinned, as_pinned])
    return rows


def main():
    if doc is None:
        TaskDialog.Show(TOOL_TITLE, "Open a project first.")
        return

    output.print_md("## {}".format(TOOL_TITLE))
    output.print_md("Revit **{}**, view **{}** ({})".format(
        doc.Application.VersionNumber, doc.ActiveView.Name,
        doc.ActiveView.ViewType))

    output.print_md("### Selection toggles, as you have them now")
    output.print_md(joolslib.markdown_table(
        _toggle_report(), ["Toggle", "Value"]))

    links = _link_report()
    output.print_md("### Link instances")
    if links:
        output.print_md(joolslib.markdown_table(
            links, ["Link", "Pinned", "Selects as pinned"]))
    else:
        output.print_md("No Revit link instances in this project — "
                        "nothing to probe. Open a model with a link.")
        return

    TaskDialog.Show(
        TOOL_TITLE,
        "5 probes follow.\n\nFor each one, try to click a LINKED object "
        "(something in the architectural model).\n\nIf nothing highlights, "
        "press Escape and the probe moves on.\n\nAll selection toggles are "
        "forced ON for the duration and restored at the end.")

    # Force every toggle on so the probes measure the ObjectType, not the
    # toggles. The user's settings are restored in the finally below.
    options = None
    previous = {}
    try:
        try:
            options = SelectionUIOptions.GetSelectionUIOptions()
            for name in ("SelectLinks", "SelectPinned"):
                previous[name] = getattr(options, name)
                setattr(options, name, True)
        except Exception as ex:
            logger.debug("could not force the toggles: %s", ex)

        results = []
        for index, (label, object_type, use_filter) in enumerate(_probes(), 1):
            prompt = "Probe {}/5 - {} - click a LINKED object, or Esc".format(
                index, label)
            try:
                if use_filter:
                    reference = uidoc.Selection.PickObject(
                        object_type, AllowAllFilter(), prompt)
                else:
                    reference = uidoc.Selection.PickObject(object_type, prompt)
            except OperationCanceledException:
                results.append([label, "ESCAPED - nothing pickable"])
                continue
            except Exception as ex:
                results.append([label, "ERROR {}: {}".format(
                    type(ex).__name__, str(ex)[:120])])
                continue
            try:
                results.append([label, _describe(reference)])
            except Exception as ex:
                results.append([label, "picked, but describe failed: {}".format(ex)])
    finally:
        for name, value in previous.items():
            try:
                setattr(options, name, value)
            except Exception:
                pass

    output.print_md("### Probe results")
    output.print_md(joolslib.markdown_table(results, ["Probe", "Result"]))
    output.print_md("Copy this whole panel back to Claude.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        output.print_md("### {} failed".format(TOOL_TITLE))
        output.print_code(traceback.format_exc())
        raise
