#! python3
"""Report ownership, placement, and every parameter of the selected elements."""

import joolslib          # Jools.extension/lib, see CLAUDE.md section 6a
joolslib.install_events_shim()   # must precede every pyrevit import (section 2.3)

import clr  # type: ignore
clr.AddReference("RevitAPI")     # type: ignore
clr.AddReference("RevitAPIUI")   # type: ignore

from Autodesk.Revit import DB  # type: ignore
from Autodesk.Revit.UI import TaskDialog  # type: ignore
from Autodesk.Revit.UI.Selection import ObjectType  # type: ignore
from Autodesk.Revit.Exceptions import OperationCanceledException  # type: ignore

from pyrevit import revit, script

__author__ = "Tommy Mai"

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
logger = script.get_logger()

TOOL_TITLE = "Element Inspector"

# Each element prints three tables; past this the output window gets slow and
# nobody reads that far anyway.
MAX_ELEMENTS = 20


def get_targets():
    """///Summary: The selected elements, or one picked now if nothing is selected.

    Returns None after telling the user why, so the caller can just bail.
    """
    ids = list(uidoc.Selection.GetElementIds())
    if not ids:
        try:
            ref = uidoc.Selection.PickObject(
                ObjectType.Element, "Click an element to inspect  (Esc to cancel)")
        except OperationCanceledException:
            return None
        ids = [ref.ElementId]

    if len(ids) > MAX_ELEMENTS:
        TaskDialog.Show(
            TOOL_TITLE,
            "{} elements are selected; only the first {} will be reported.\n\n"
            "Select fewer elements to see them all.".format(len(ids), MAX_ELEMENTS))
        ids = ids[:MAX_ELEMENTS]

    return [doc.GetElement(i) for i in ids if doc.GetElement(i) is not None]


def element_name(element_id):
    """///Summary: The name of the element an id points to, or '' if there is none."""
    if element_id is None or element_id == DB.ElementId.InvalidElementId:
        return ""
    el = doc.GetElement(element_id)
    if el is None:
        return ""
    try:
        return el.Name
    except Exception:
        # Some element kinds throw on .Name instead of returning empty.
        return ""


def ownership_rows(el):
    """///Summary: Worksharing ownership facts for one element, as (label, value) rows."""
    if not doc.IsWorkshared:
        return [("Workshared", "No - this model has no ownership to report")]

    rows = []
    try:
        info = DB.WorksharingUtils.GetWorksharingTooltipInfo(doc, el.Id)
        requesters = ", ".join(info.GetRequesters()) or "-"
        rows += [
            ("Owner (borrowed by)", info.Owner or "Nobody"),
            ("Creator", info.Creator or "-"),
            ("Last changed by", info.LastChangedBy or "-"),
            ("Pending requests from", requesters),
        ]
    except Exception as ex:
        logger.debug("tooltip info failed for %s: %s", joolslib.eid_int(el.Id), ex)

    try:
        rows.append(("Checkout status",
                     str(DB.WorksharingUtils.GetCheckoutStatus(doc, el.Id))))
        rows.append(("Up to date with central",
                     str(DB.WorksharingUtils.GetModelUpdatesStatus(doc, el.Id))))
    except Exception as ex:
        logger.debug("checkout status failed for %s: %s", joolslib.eid_int(el.Id), ex)

    workset = doc.GetWorksetTable().GetWorkset(el.WorksetId)
    if workset is not None:
        rows.append(("Workset", workset.Name))
        # Owner lives on the WorksetPreview base class, which the API docs
        # list separately from Workset.
        rows.append(("Workset owner", getattr(workset, "Owner", "") or "Nobody"))
        rows.append(("Workset editable by you", "Yes" if workset.IsEditable else "No"))
    return rows


def placement_rows(el):
    """///Summary: Identity, view, phase, and group facts for one element."""
    category = el.Category.Name if el.Category else "-"
    rows = [
        ("Category", category),
        ("Class", el.GetType().Name),
        ("Unique Id", el.UniqueId),
        ("Type", element_name(el.GetTypeId()) or "-"),
        ("Pinned", "Yes" if el.Pinned else "No"),
        ("View-specific", "Yes" if el.ViewSpecific else "No (model element)"),
    ]
    if el.ViewSpecific:
        rows.append(("Owner view", element_name(el.OwnerViewId) or "-"))

    rows.append(("Phase created", element_name(el.CreatedPhaseId) or "-"))
    rows.append(("Phase demolished", element_name(el.DemolishedPhaseId) or "-"))
    rows.append(("Design option",
                 el.DesignOption.Name if el.DesignOption else "Main Model"))
    rows.append(("In group", element_name(el.GroupId) or "No"))
    return rows


# --- Visibility: what is controlling how the element looks in the active view ---

# Graphic properties Revit resolves one at a time. For each, the first layer
# that sets it wins: element override > filters (top of the list first) >
# category override in V/G > Object Styles.
# (label, OverrideGraphicSettings property, kind)
GRAPHIC_PROPERTIES = [
    ("Projection line color", "ProjectionLineColor", "color"),
    ("Projection line weight", "ProjectionLineWeight", "pen"),
    ("Projection line pattern", "ProjectionLinePatternId", "id"),
    ("Surface pattern (foreground)", "SurfaceForegroundPatternId", "id"),
    ("Surface color (foreground)", "SurfaceForegroundPatternColor", "color"),
    ("Surface pattern (background)", "SurfaceBackgroundPatternId", "id"),
    ("Surface color (background)", "SurfaceBackgroundPatternColor", "color"),
    ("Cut line color", "CutLineColor", "color"),
    ("Cut line weight", "CutLineWeight", "pen"),
    ("Cut line pattern", "CutLinePatternId", "id"),
    ("Cut pattern (foreground)", "CutForegroundPatternId", "id"),
    ("Cut color (foreground)", "CutForegroundPatternColor", "color"),
    ("Cut pattern (background)", "CutBackgroundPatternId", "id"),
    ("Cut color (background)", "CutBackgroundPatternColor", "color"),
    ("Transparency", "Transparency", "percent"),
    ("Halftone", "Halftone", "bool"),
    ("Detail level", "DetailLevel", "detail"),
]

# View template settings that decide who owns each visibility layer.
TEMPLATE_PARAMS = {
    "model": DB.BuiltInParameter.VIS_GRAPHICS_MODEL,
    "annotation": DB.BuiltInParameter.VIS_GRAPHICS_ANNOTATION,
    "filters": DB.BuiltInParameter.VIS_GRAPHICS_FILTERS,
    "worksets": DB.BuiltInParameter.VIS_GRAPHICS_WORKSETS,
    "phase_filter": DB.BuiltInParameter.VIEW_PHASE_FILTER,
}


def is_set(value, kind):
    """///Summary: True if an override value is actually set, not left at default."""
    if value is None:
        return False
    if kind == "color":
        return value.IsValid
    if kind == "pen":
        return value > 0  # OverrideGraphicSettings.InvalidPenNumber is -1
    if kind == "id":
        return value != DB.ElementId.InvalidElementId
    if kind == "percent":
        return value > 0
    if kind == "bool":
        return bool(value)
    if kind == "detail":
        return value != DB.ViewDetailLevel.Undefined
    return False


def format_value(value, kind):
    """///Summary: An override value written the way the V/G dialog shows it."""
    if kind == "color":
        return "RGB {}, {}, {}".format(value.Red, value.Green, value.Blue)
    if kind == "id":
        # Built-in patterns like Solid have negative ids and no element.
        return element_name(value) or "Built-in pattern ({})".format(joolslib.eid_int(value))
    if kind == "percent":
        return "{}%".format(value)
    if kind == "bool":
        return "Yes" if value else "No"
    return str(value)


def controller_name(view, setting):
    """///Summary: Who owns a V/G setting: the view template, or this view itself."""
    template = doc.GetElement(view.ViewTemplateId)
    if template is None:
        return "this view's V/G"
    loose = set(joolslib.eid_int(i) for i in template.GetNonControlledTemplateParameterIds())
    if joolslib.eid_int(DB.ElementId(TEMPLATE_PARAMS[setting])) in loose:
        return "this view's V/G (template '{}' leaves it unlocked)".format(template.Name)
    return "view template '{}'".format(template.Name)


def filter_applies(filter_el, el):
    """///Summary: True if a view filter's rules pick up this element."""
    if isinstance(filter_el, DB.SelectionFilterElement):
        return filter_el.Contains(el.Id)
    if isinstance(filter_el, DB.ParameterFilterElement):
        if el.Category is None:
            return False
        cat_ids = set(joolslib.eid_int(i) for i in filter_el.GetCategories())
        if joolslib.eid_int(el.Category.Id) not in cat_ids:
            return False
        rules = filter_el.GetElementFilter()
        # No rules means "every element in these categories".
        return rules is None or rules.PassesFilter(el)
    return False


def applying_filters(view, el):
    """///Summary: (filter, enabled, visible) for each view filter catching el, top first."""
    found = []
    for fid in view.GetOrderedFilters():
        filter_el = doc.GetElement(fid)
        try:
            if filter_el is None or not filter_applies(filter_el, el):
                continue
        except Exception as ex:
            logger.debug("filter %s check failed: %s", element_name(fid), ex)
            continue
        found.append((filter_el, view.GetIsFilterEnabled(fid), view.GetFilterVisibility(fid)))
    return found


def hidden_checks(view, el, filters):
    """///Summary: Every reason the element could be hidden, as (check, result, controlled by)."""
    rows = []

    try:
        rows.append(("Hide in View > Elements",
                     "HIDDEN" if el.IsHidden(view) else "ok", "this view"))
    except Exception:
        pass  # element types have no per-view visibility

    cat = el.Category
    if cat is not None:
        setting = "annotation" if cat.CategoryType == DB.CategoryType.Annotation else "model"
        for c in ([cat, cat.Parent] if cat.Parent else [cat]):
            if view.CanCategoryBeHidden(c.Id):
                rows.append(("Category '{}' in V/G".format(c.Name),
                             "HIDDEN" if view.GetCategoryHidden(c.Id) else "ok",
                             controller_name(view, setting)))

    for filter_el, enabled, visible in filters:
        if not enabled:
            result = "ok (filter is disabled)"
        else:
            result = "ok" if visible else "HIDDEN"
        rows.append(("Filter '{}'".format(filter_el.Name), result,
                     controller_name(view, "filters")))

    if doc.IsWorkshared:
        ws_name = doc.GetWorksetTable().GetWorkset(el.WorksetId).Name
        setting = view.GetWorksetVisibility(el.WorksetId)
        if setting == DB.WorksetVisibility.UseGlobalSetting:
            defaults = DB.WorksetDefaultVisibilitySettings.GetWorksetDefaultVisibilitySettings(doc)
            visible = defaults.IsWorksetVisible(el.WorksetId)
            where = "workset default 'Visible in all views' (Manage > Worksets)"
        else:
            visible = setting == DB.WorksetVisibility.Visible
            where = controller_name(view, "worksets")
        rows.append(("Workset '{}'".format(ws_name), "ok" if visible else "HIDDEN", where))

    phase_row = phase_check(view, el)
    if phase_row:
        rows.append(phase_row)

    if view.IsInTemporaryViewMode(DB.TemporaryViewMode.TemporaryHideIsolate):
        visible = view.IsElementVisibleInTemporaryViewMode(
            DB.TemporaryViewMode.TemporaryHideIsolate, el.Id)
        rows.append(("Temporary Hide/Isolate (sunglasses icon)",
                     "ok" if visible else "HIDDEN", "temporary - reset in the view bar"))
    return rows


def phase_check(view, el):
    """///Summary: How the view's phase filter treats this element, as one row."""
    phase_param = view.get_Parameter(DB.BuiltInParameter.VIEW_PHASE)
    filter_param = view.get_Parameter(DB.BuiltInParameter.VIEW_PHASE_FILTER)
    if phase_param is None or filter_param is None:
        return None
    phase_filter = doc.GetElement(filter_param.AsElementId())
    phase_id = phase_param.AsElementId()
    if phase_filter is None or phase_id == DB.ElementId.InvalidElementId:
        return None
    try:
        status = el.GetPhaseStatus(phase_id)
        shown = phase_filter.GetPhaseStatusPresentation(status)
    except Exception:
        return None  # annotation and other unphased elements
    label = "Phase filter '{}' (element is {} in '{}')".format(
        phase_filter.Name, status, element_name(phase_id))
    if shown == DB.PhaseStatusPresentation.DontShow:
        return (label, "HIDDEN", controller_name(view, "phase_filter"))
    if shown == DB.PhaseStatusPresentation.ShowOverriden:
        return (label, "ok - but drawn with Phase graphic overrides",
                "Manage > Phases > Graphic Overrides")
    return (label, "ok", controller_name(view, "phase_filter"))


def object_style_layer(cat):
    """///Summary: The category's Object Styles defaults, keyed like OverrideGraphicSettings."""
    projection = DB.GraphicsStyleType.Projection
    cut = DB.GraphicsStyleType.Cut
    layer = {
        "ProjectionLineColor": cat.LineColor,
        "CutLineColor": cat.LineColor,  # Object Styles has one color for both
        "ProjectionLineWeight": cat.GetLineWeight(projection),
        "CutLineWeight": cat.GetLineWeight(cut),
        "ProjectionLinePatternId": cat.GetLinePatternId(projection),
        "CutLinePatternId": cat.GetLinePatternId(cut),
    }
    return layer


def graphic_rows(view, el, filters):
    """///Summary: For each graphic property, the winning value and who set it."""
    layers = [("Element override (Override Graphics in View > By Element)",
               view.GetElementOverrides(el.Id))]
    for filter_el, enabled, visible in filters:
        if enabled and visible:
            layers.append(("Filter '{}' ({})".format(
                filter_el.Name, controller_name(view, "filters")),
                view.GetFilterOverrides(filter_el.Id)))
    cat = el.Category
    if cat is not None:
        setting = "annotation" if cat.CategoryType == DB.CategoryType.Annotation else "model"
        layers.append(("Category '{}' override ({})".format(
            cat.Name, controller_name(view, setting)),
            view.GetCategoryOverrides(cat.Id)))

    styles = object_style_layer(cat) if cat is not None else {}

    rows = []
    for label, prop, kind in GRAPHIC_PROPERTIES:
        winner = None
        for source, ogs in layers:
            value = getattr(ogs, prop)
            if is_set(value, kind):
                winner = (format_value(value, kind), source)
                break
        if winner is None and is_set(styles.get(prop), kind):
            winner = (format_value(styles[prop], kind), "Object Styles (Manage > Object Styles)")
        if winner is None:
            winner = ("-", "not overridden - family / material / default")
        rows.append((label,) + winner)
    return rows


def report_visibility(el):
    """///Summary: Print the 'why does it look like this' section for the active view."""
    view = doc.ActiveView
    output.print_md("### Visibility in '{}'".format(view.Name))
    if not view.AreGraphicsOverridesAllowed():
        output.print_md("_This kind of view (schedule, sheet, etc.) has no V/G to inspect. "
                        "Open a plan, section, or 3D view and run again._")
        return

    template = doc.GetElement(view.ViewTemplateId)
    output.print_md("View template: **{}**".format(template.Name if template else "none"))

    filters = applying_filters(view, el)
    checks = hidden_checks(view, el, filters)
    blockers = [c for c in checks if c[1] == "HIDDEN"]
    if blockers:
        output.print_md("**Hidden** by: " + "; ".join(
            "{} ({})".format(c[0], c[2]) for c in blockers))
    else:
        output.print_md("**Visible** - nothing below is hiding it. If you still can't see it, "
                        "check crop region, view range, and design options.")
    output.print_md(joolslib.markdown_table(checks, ["Check", "Result", "Controlled by"]))

    output.print_md("#### Which setting wins, per graphic property")
    output.print_md(joolslib.markdown_table(graphic_rows(view, el, filters),
                                            ["Property", "Value", "Set by"]))


def parameter_value(param):
    """///Summary: A parameter's value as the user would read it in Properties."""
    if not param.HasValue:
        return ""
    storage = param.StorageType
    if storage == DB.StorageType.String:
        return param.AsString() or ""
    if storage == DB.StorageType.ElementId:
        # AsValueString gives the display name for most id parameters (Level,
        # Phase); fall back to looking the element up ourselves.
        return (param.AsValueString() or element_name(param.AsElementId())
                or str(joolslib.eid_int(param.AsElementId())))
    text = param.AsValueString()
    if text:
        return text
    if storage == DB.StorageType.Integer:
        return str(param.AsInteger())
    if storage == DB.StorageType.Double:
        return str(param.AsDouble())
    return ""


def parameter_group(param):
    """///Summary: The Properties-palette group heading a parameter sits under."""
    try:
        return DB.LabelUtils.GetLabelForGroup(param.Definition.GetGroupTypeId())
    except Exception:
        return "Other"


def parameter_rows(el):
    """///Summary: Every parameter on el as (group, name, value, kind) rows, sorted."""
    rows = []
    for param in el.Parameters:
        kind = "Shared" if param.IsShared else (
            "Built-in" if param.Id is not None and joolslib.eid_int(param.Id) < 0
            else "Project")
        if param.IsReadOnly:
            kind += ", read-only"
        rows.append((parameter_group(param), param.Definition.Name,
                     parameter_value(param), kind))
    rows.sort(key=lambda r: (r[0].lower(), r[1].lower()))
    return rows


def report(el):
    """///Summary: Print every section for one element to the output window."""
    output.print_md("---")
    output.print_md("## {} {}".format(element_name(el.Id) or el.GetType().Name,
                                      output.linkify(el.Id)))

    output.print_md("### Ownership")
    output.print_md(joolslib.markdown_table(ownership_rows(el), ["Item", "Value"]))

    output.print_md("### Element & view")
    output.print_md(joolslib.markdown_table(placement_rows(el), ["Item", "Value"]))

    try:
        report_visibility(el)
    except Exception as ex:
        # Keep going: the parameter tables below are still worth having.
        logger.error("Visibility check failed for %s: %s", joolslib.eid_int(el.Id), ex)

    columns = ["Group", "Parameter", "Value", "Kind"]
    output.print_md("### Instance parameters")
    output.print_md(joolslib.markdown_table(parameter_rows(el), columns))

    el_type = doc.GetElement(el.GetTypeId())
    if el_type is not None:
        output.print_md("### Type parameters ({})".format(element_name(el_type.Id)))
        output.print_md(joolslib.markdown_table(parameter_rows(el_type), columns))


def main():
    targets = get_targets()
    if not targets:
        return

    output.set_title(TOOL_TITLE)
    output.print_md("# {}: {} element(s)".format(TOOL_TITLE, len(targets)))
    for el in targets:
        try:
            report(el)
        except Exception as ex:
            logger.error("Could not report element %s: %s",
                         joolslib.eid_int(el.Id), ex)


if __name__ == "__main__":
    main()
