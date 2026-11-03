#! python3
"""Move view templates between projects through a file, in any Revit version.

Revit files never open in an older Revit, so templates can't be copied from a
2025 project into a 2022 one directly. Export writes each template's settings as
plain text (parameters, category visibility and overrides, filters and the line
and fill patterns they use); Import rebuilds them in another project. Anything
the target has no equivalent for is skipped and listed at the end.
"""

import joolslib
joolslib.install_events_shim()   # must precede every pyrevit import (CLAUDE.md 2.3)

import json
import datetime

import clr  # type: ignore
clr.AddReference("RevitAPI")              # type: ignore
clr.AddReference("RevitAPIUI")            # type: ignore
clr.AddReference("PresentationFramework") # type: ignore
clr.AddReference("PresentationCore")      # type: ignore
clr.AddReference("WindowsBase")           # type: ignore

from Autodesk.Revit import DB                     # type: ignore
from Autodesk.Revit.UI import (TaskDialog, TaskDialogCommandLinkId,
                               TaskDialogResult)  # type: ignore
import System                                     # type: ignore
from System.Windows.Markup import XamlReader      # type: ignore
from System.Windows.Interop import WindowInteropHelper  # type: ignore
from System.Collections.Generic import List       # type: ignore
from Microsoft.Win32 import OpenFileDialog, SaveFileDialog  # type: ignore

from pyrevit import revit, script

eid_int = joolslib.eid_int

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
logger = script.get_logger()

TOOL_TITLE = "View Template Transfer"
FILE_FORMAT = "jools-view-templates"
FILE_VERSION = 1
DRY_RUN = False   # True: import does everything, reports it, then rolls the model back

# (json key, getter, setter, kind). Export reads with the getter and writes only
# values that differ from "no override"; Import replays them with the setter.
OVERRIDES = [
    ("halftone", "Halftone", "SetHalftone", "flag"),
    ("detail_level", "DetailLevel", "SetDetailLevel", "detail"),
    ("transparency", "Transparency", "SetSurfaceTransparency", "count"),
    ("projection_line_weight", "ProjectionLineWeight", "SetProjectionLineWeight", "count"),
    ("projection_line_color", "ProjectionLineColor", "SetProjectionLineColor", "color"),
    ("projection_line_pattern", "ProjectionLinePatternId", "SetProjectionLinePatternId", "line"),
    ("cut_line_weight", "CutLineWeight", "SetCutLineWeight", "count"),
    ("cut_line_color", "CutLineColor", "SetCutLineColor", "color"),
    ("cut_line_pattern", "CutLinePatternId", "SetCutLinePatternId", "line"),
    ("surface_fg_pattern", "SurfaceForegroundPatternId", "SetSurfaceForegroundPatternId", "fill"),
    ("surface_fg_color", "SurfaceForegroundPatternColor", "SetSurfaceForegroundPatternColor", "color"),
    ("surface_fg_visible", "IsSurfaceForegroundPatternVisible", "SetSurfaceForegroundPatternVisible", "visible"),
    ("surface_bg_pattern", "SurfaceBackgroundPatternId", "SetSurfaceBackgroundPatternId", "fill"),
    ("surface_bg_color", "SurfaceBackgroundPatternColor", "SetSurfaceBackgroundPatternColor", "color"),
    ("surface_bg_visible", "IsSurfaceBackgroundPatternVisible", "SetSurfaceBackgroundPatternVisible", "visible"),
    ("cut_fg_pattern", "CutForegroundPatternId", "SetCutForegroundPatternId", "fill"),
    ("cut_fg_color", "CutForegroundPatternColor", "SetCutForegroundPatternColor", "color"),
    ("cut_fg_visible", "IsCutForegroundPatternVisible", "SetCutForegroundPatternVisible", "visible"),
    ("cut_bg_pattern", "CutBackgroundPatternId", "SetCutBackgroundPatternId", "fill"),
    ("cut_bg_color", "CutBackgroundPatternColor", "SetCutBackgroundPatternColor", "color"),
    ("cut_bg_visible", "IsCutBackgroundPatternVisible", "SetCutBackgroundPatternVisible", "visible"),
]

PICKER_XAML = """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="TITLE" Height="560" Width="480"
        WindowStartupLocation="CenterOwner" Background="#F0F0F0">
    <Grid Margin="15">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>
        <TextBlock Grid.Row="0" x:Name="Prompt" FontWeight="Bold" Margin="0,0,0,8" TextWrapping="Wrap"/>
        <ListBox Grid.Row="1" x:Name="Items" Margin="0,0,0,12">
            <ListBox.ItemTemplate>
                <DataTemplate>
                    <CheckBox Content="{Binding Label}" IsChecked="{Binding IsChecked, Mode=TwoWay}"/>
                </DataTemplate>
            </ListBox.ItemTemplate>
        </ListBox>
        <DockPanel Grid.Row="2">
            <Button DockPanel.Dock="Left" x:Name="BtnAll" Content="Select All" Width="80" Height="28"/>
            <Button DockPanel.Dock="Left" x:Name="BtnNone" Content="Select None" Width="80" Height="28" Margin="8,0,0,0"/>
            <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
                <Button x:Name="BtnOk" Width="110" Height="28" Margin="0,0,8,0" IsDefault="True"/>
                <Button x:Name="BtnCancel" Content="Cancel" Width="80" Height="28" IsCancel="True"/>
            </StackPanel>
        </DockPanel>
    </Grid>
</Window>
"""


class PickItem(object):
    """///Summary: One checkbox row; WPF binds Label and IsChecked."""

    def __init__(self, label, payload):
        self.Label = label
        self.IsChecked = False
        self.Payload = payload


def pick_items(items, title, prompt, ok_text):
    """///Summary: Checkbox list. Returns the chosen payloads, or None on cancel."""
    window = XamlReader.Parse(PICKER_XAML.replace("TITLE", title))
    WindowInteropHelper(window).Owner = uidoc.Application.MainWindowHandle
    window.FindName("Prompt").Text = prompt
    window.FindName("BtnOk").Content = ok_text
    listbox = window.FindName("Items")

    def fill():
        # A fresh .NET list each time: reassigning is what makes WPF redraw the checks.
        net_items = List[System.Object]()
        for item in items:
            net_items.Add(item)
        listbox.ItemsSource = net_items

    def set_all(state):
        for item in items:
            item.IsChecked = state
        fill()

    result = {"ok": False}

    def on_ok(sender, e):
        if not any(item.IsChecked for item in items):
            TaskDialog.Show(title, "Tick at least one template, or press Cancel.")
            return
        result["ok"] = True
        window.Close()

    window.FindName("BtnAll").Click += lambda s, e: set_all(True)
    window.FindName("BtnNone").Click += lambda s, e: set_all(False)
    window.FindName("BtnOk").Click += on_ok
    window.FindName("BtnCancel").Click += lambda s, e: window.Close()
    fill()
    window.ShowDialog()
    if not result["ok"]:
        return None
    return [item.Payload for item in items if item.IsChecked]


def enum_name(enum_type, value):
    """///Summary: The enum member name for an integer id, or None if it has no name."""
    name = str(System.Enum.ToObject(enum_type, value))
    return None if name.lstrip("-").isdigit() else name


def category_key(category):
    """///Summary: "OST_Walls" or "OST_Walls|Common Edges"; None for imported (DWG) categories.

    Names are used rather than ids because ids are not stable between projects
    or Revit versions. Export writes these keys and Import matches on them.
    """
    parent = category.Parent
    top = parent if parent is not None else category
    top_value = eid_int(top.Id)
    if top_value >= 0:
        return None
    bic = enum_name(DB.BuiltInCategory, top_value)
    if bic is None:
        return None
    return bic if parent is None else "{}|{}".format(bic, category.Name)


def all_categories():
    """///Summary: (key, Category) for every category and subcategory in the project."""
    found = []
    for cat in doc.Settings.Categories:
        key = category_key(cat)
        if key is None:
            continue
        found.append((key, cat))
        for sub in cat.SubCategories:
            sub_key = category_key(sub)
            if sub_key is not None:
                found.append((sub_key, sub))
    return found


# ==== Export ==================================================================

class Unsupported(Exception):
    """///Summary: A filter rule this tool can't write down; the message says which."""


def param_key(param_id):
    """///Summary: How to find a parameter again by name: built-in, shared (GUID), or project."""
    value = eid_int(param_id)
    if value < 0:
        name = enum_name(DB.BuiltInParameter, value)
        return {"bip": name} if name else None
    element = doc.GetElement(param_id)
    if isinstance(element, DB.SharedParameterElement):
        return {"guid": str(element.GuidValue), "name": element.Name}
    if element is not None:
        return {"name": element.Name}
    return None


def element_ref(element_id):
    """///Summary: An element as class + name, so Import can look it up by name."""
    if element_id == DB.ElementId.InvalidElementId:
        return None
    element = doc.GetElement(element_id)
    if element is None:
        raise Unsupported("refers to an element id {} that has no name".format(eid_int(element_id)))
    return {"class": element.GetType().FullName, "name": element.Name}


class Exporter(object):
    """///Summary: Collects templates plus the filters and patterns they use."""

    def __init__(self):
        self.line_patterns = {}
        self.fill_patterns = {}
        self.filters = {}
        self.problems = []
        self.solid_line_id = eid_int(DB.LinePatternElement.GetSolidPatternId())

    # ---- graphic overrides -------------------------------------------------

    def overrides(self, ogs):
        data = {}
        for key, getter, _setter, kind in OVERRIDES:
            value = getattr(ogs, getter)
            if kind == "flag" and value:
                data[key] = True
            elif kind == "visible" and not value:
                data[key] = False
            elif kind == "count" and value > 0:
                data[key] = int(value)
            elif kind == "detail" and value != DB.ViewDetailLevel.Undefined:
                data[key] = str(value)
            elif kind == "color" and value.IsValid:
                data[key] = [int(value.Red), int(value.Green), int(value.Blue)]
            elif kind == "line" and value != DB.ElementId.InvalidElementId:
                data[key] = self.line_pattern(value)
            elif kind == "fill" and value != DB.ElementId.InvalidElementId:
                ref = self.fill_pattern(value)
                if ref:
                    data[key] = ref
        return data

    def line_pattern(self, pattern_id):
        if eid_int(pattern_id) == self.solid_line_id:
            return "<Solid>"
        element = doc.GetElement(pattern_id)
        pattern = element.GetLinePattern()
        if pattern.Name not in self.line_patterns:
            self.line_patterns[pattern.Name] = {
                "segments": [[str(s.Type), s.Length] for s in pattern.GetSegments()]}
        return pattern.Name

    def fill_pattern(self, pattern_id):
        element = doc.GetElement(pattern_id)
        if element is None:
            return None
        pattern = element.GetFillPattern()
        ref = {"name": pattern.Name, "target": str(pattern.Target)}
        key = "{}|{}".format(ref["target"], ref["name"])
        if key not in self.fill_patterns:
            self.fill_patterns[key] = {
                "name": pattern.Name,
                "target": str(pattern.Target),
                "orientation": str(pattern.HostOrientation),
                "solid": bool(pattern.IsSolidFill),
                "grids": [{
                    "angle": g.Angle,
                    "origin": [g.Origin.U, g.Origin.V],
                    "offset": g.Offset,
                    "shift": g.Shift,
                    "segments": [float(s) for s in g.GetSegments()],
                } for g in pattern.GetFillGrids()],
            }
        return ref

    # ---- filters -----------------------------------------------------------

    def rule(self, rule):
        """///Summary: One filter rule as a dict. Order matters: subclasses come first."""
        if isinstance(rule, DB.FilterInverseRule):
            return {"kind": "not", "rule": self.rule(rule.GetInnerRule())}
        if isinstance(rule, DB.SharedParameterApplicableRule):
            return {"kind": "shared_applicable", "name": rule.ParameterName}
        if isinstance(rule, DB.FilterGlobalParameterAssociationRule):
            raise Unsupported("uses a global parameter rule")

        param = param_key(rule.GetRuleParameter())
        if param is None:
            raise Unsupported("uses a parameter this tool can't identify")
        if isinstance(rule, DB.HasValueFilterRule):
            return {"kind": "has_value", "param": param}
        if isinstance(rule, DB.HasNoValueFilterRule):
            return {"kind": "has_no_value", "param": param}
        if isinstance(rule, DB.FilterStringRule):
            return {"kind": "string", "param": param, "value": rule.RuleString,
                    "evaluator": rule.GetEvaluator().GetType().Name}
        if isinstance(rule, (DB.FilterIntegerRule, DB.FilterDoubleRule, DB.FilterElementIdRule)):
            data = {"param": param, "evaluator": rule.GetEvaluator().GetType().Name}
            if isinstance(rule, DB.FilterIntegerRule):
                data.update(kind="int", value=int(rule.RuleValue))
            elif isinstance(rule, DB.FilterDoubleRule):
                data.update(kind="double", value=rule.RuleValue, epsilon=rule.Epsilon)
            else:
                data.update(kind="element", value=element_ref(rule.RuleValue))
            return data
        raise Unsupported("uses a {} rule".format(rule.GetType().Name))

    def element_filter(self, element_filter):
        if element_filter is None:
            return None
        if isinstance(element_filter, DB.ElementLogicalFilter):
            logic = "and" if isinstance(element_filter, DB.LogicalAndFilter) else "or"
            return {"logic": logic,
                    "filters": [self.element_filter(f) for f in element_filter.GetFilters()]}
        if isinstance(element_filter, DB.ElementParameterFilter):
            return {"rules": [self.rule(r) for r in element_filter.GetRules()],
                    "inverted": bool(element_filter.Inverted)}
        raise Unsupported("uses a {} condition".format(element_filter.GetType().Name))

    def filter_definition(self, filter_element):
        name = filter_element.Name
        if name in self.filters:
            return
        if isinstance(filter_element, DB.SelectionFilterElement):
            self.filters[name] = {"kind": "selection"}
            return
        categories = []
        for cat_id in filter_element.GetCategories():
            bic = enum_name(DB.BuiltInCategory, eid_int(cat_id))
            if bic:
                categories.append(bic)
        definition = {"kind": "parameter", "categories": categories}
        try:
            definition["rules"] = self.element_filter(filter_element.GetElementFilter())
        except Unsupported as ex:
            definition["unsupported"] = str(ex)
            self.problems.append(("Filter '{}'".format(name),
                                  "Rules not exported: {}. Import can only use a filter "
                                  "that already has this name.".format(ex)))
        self.filters[name] = definition

    # ---- templates ---------------------------------------------------------

    def template(self, view, categories):
        params = []
        uncontrolled = []
        by_id = {eid_int(p.Id): p for p in view.Parameters}
        not_controlled = {eid_int(i) for i in view.GetNonControlledTemplateParameterIds()}
        for pid in view.GetTemplateParameterIds():
            key = param_key(pid)
            if key is None:
                continue
            if eid_int(pid) in not_controlled:
                uncontrolled.append(key)
            param = by_id.get(eid_int(pid))
            if param is None or param.IsReadOnly:
                continue
            entry = self.param_value(param)
            if entry is not None:
                entry["param"] = key
                params.append(entry)

        hidden = []
        category_overrides = []
        if view.AreGraphicsOverridesAllowed():
            for key, cat in categories:
                try:
                    if view.GetCategoryHidden(cat.Id):
                        hidden.append(key)
                    data = self.overrides(view.GetCategoryOverrides(cat.Id))
                except Exception as ex:
                    logger.debug("category %s skipped on %s: %s", key, view.Name, ex)
                    continue
                if data:
                    category_overrides.append({"category": key, "overrides": data})

        filters = []
        for fid in view.GetOrderedFilters():
            filter_element = doc.GetElement(fid)
            self.filter_definition(filter_element)
            filters.append({
                "name": filter_element.Name,
                "visible": bool(view.GetFilterVisibility(fid)),
                "enabled": bool(view.GetIsFilterEnabled(fid)),
                "overrides": self.overrides(view.GetFilterOverrides(fid)),
            })

        return {
            "name": view.Name,
            "view_type": str(view.ViewType),
            "parameters": params,
            "uncontrolled": uncontrolled,
            "hidden_categories": hidden,
            "category_overrides": category_overrides,
            "filters": filters,
        }

    def param_value(self, param):
        storage = param.StorageType
        if storage == DB.StorageType.Integer:
            return {"int": param.AsInteger()}
        if storage == DB.StorageType.Double:
            return {"double": param.AsDouble()}
        if storage == DB.StorageType.String:
            return {"string": param.AsString() or ""}
        if storage == DB.StorageType.ElementId:
            try:
                return {"element": element_ref(param.AsElementId())}
            except Unsupported:
                return None
        return None


def choose_save_path():
    dialog = SaveFileDialog()
    dialog.Title = TOOL_TITLE
    dialog.Filter = "View template file (*.json)|*.json"
    dialog.FileName = "{} - view templates.json".format(doc.Title)
    if dialog.ShowDialog() != True:
        return None
    return dialog.FileName


def export_main():
    """///Summary: Pick templates in this project and write them to a .json file."""
    if doc.IsFamilyDocument:
        TaskDialog.Show(TOOL_TITLE, "Open a project, not a family, then run again.")
        return

    templates = sorted((v for v in DB.FilteredElementCollector(doc).OfClass(DB.View) if v.IsTemplate),
                       key=lambda v: v.Name)
    if not templates:
        TaskDialog.Show(TOOL_TITLE, "This project has no view templates to export.")
        return

    items = [PickItem("{}   ({})".format(v.Name, v.ViewType), v) for v in templates]
    chosen = pick_items(items, TOOL_TITLE, "Tick the view templates to export:", "Export")
    if not chosen:
        return
    path = choose_save_path()
    if not path:
        return

    exporter = Exporter()
    categories = all_categories()
    exported = []
    for view in chosen:
        try:
            exported.append(exporter.template(view, categories))
        except Exception as ex:
            logger.error("template %s failed: %s", view.Name, ex)
            exporter.problems.append(("Template '{}'".format(view.Name), "Not exported: {}".format(ex)))

    data = {
        "format": FILE_FORMAT,
        "version": FILE_VERSION,
        "revit_version": doc.Application.VersionNumber,
        "source_project": doc.Title,
        "exported": datetime.datetime.now().isoformat(timespec="seconds"),
        "categories": [key for key, _cat in categories],
        "line_patterns": exporter.line_patterns,
        "fill_patterns": exporter.fill_patterns,
        "filters": exporter.filters,
        "templates": exported,
    }
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1, ensure_ascii=False)
    except Exception as ex:
        TaskDialog.Show(TOOL_TITLE, "Could not write the file:\n{}\n\n{}\n\n"
                        "Pick a folder you can save to and try again.".format(path, ex))
        return

    output.print_md("## {}".format(TOOL_TITLE))
    output.print_md("Exported **{}** templates, **{}** filters, **{}** line patterns and **{}** fill "
                    "patterns from Revit {} to:\n\n`{}`".format(
                        len(exported), len(exporter.filters), len(exporter.line_patterns),
                        len(exporter.fill_patterns), data["revit_version"], path))
    output.print_md("Open the target project in the other Revit, click **{}** and choose "
                    "**Import**.".format(TOOL_TITLE))
    if exporter.problems:
        output.print_md("### Needs attention")
        output.print_md(joolslib.markdown_table(exporter.problems, ["Item", "Problem"]))


# ==== Import ==================================================================

class Missing(Exception):
    """///Summary: Something the file refers to has no equivalent in this project."""


def param_label(key):
    return key.get("name") or key.get("bip") or "?"


class Importer(object):
    """///Summary: Resolves names from the file to elements in this project, creating what it can.

    Every lookup is cached together with its problem, so a filter that fails
    once is reported against every template that uses it, not just the first.
    """

    def __init__(self, data):
        self.data = data
        self.issues = {}        # problem -> [template names]
        self.created = []
        self.current = None     # template being imported, for issue reports
        self._cache = {}
        self.categories = dict(all_categories())

    def note(self, problem):
        templates = self.issues.setdefault(problem, [])
        if self.current and self.current not in templates:
            templates.append(self.current)

    def cached(self, kind, name, build):
        """///Summary: build() once per (kind, name); a Missing becomes a reported None."""
        key = (kind, name)
        if key not in self._cache:
            try:
                self._cache[key] = (build(), None)
            except Missing as ex:
                self._cache[key] = (None, str(ex))
        value, problem = self._cache[key]
        if problem:
            self.note(problem)
        return value

    # ---- parameters and elements -------------------------------------------

    def param_id(self, key):
        return self.cached("param", json.dumps(key, sort_keys=True), lambda: self._param_id(key))

    def _param_id(self, key):
        if "bip" in key:
            bip = getattr(DB.BuiltInParameter, key["bip"], None)
            if bip is None:
                raise Missing("Parameter '{}' doesn't exist in Revit {}".format(
                    key["bip"], doc.Application.VersionNumber))
            return DB.ElementId(bip)
        if "guid" in key:
            element = DB.SharedParameterElement.Lookup(doc, System.Guid(key["guid"]))
            if element is not None:
                return element.Id
        for element in DB.FilteredElementCollector(doc).OfClass(DB.ParameterElement):
            if element.Name == key.get("name"):
                return element.Id
        raise Missing("Parameter '{}' isn't loaded in this project".format(param_label(key)))

    def element_id(self, ref):
        if ref is None:
            return DB.ElementId.InvalidElementId
        return self.cached("element", "{}|{}".format(ref["class"], ref["name"]),
                           lambda: self._element_id(ref))

    def _element_id(self, ref):
        problem = "{} '{}' isn't in this project".format(ref["class"].split(".")[-1], ref["name"])
        clr_type = clr.GetClrType(DB.Element).Assembly.GetType(ref["class"])
        if clr_type is None:
            raise Missing(problem)
        try:
            for element in DB.FilteredElementCollector(doc).OfClass(clr_type):
                if element.Name == ref["name"]:
                    return element.Id
        except Exception as ex:
            logger.debug("OfClass(%s) failed: %s", ref["class"], ex)
        raise Missing(problem)

    # ---- patterns ----------------------------------------------------------

    def line_pattern(self, name):
        if name == "<Solid>":
            return DB.LinePatternElement.GetSolidPatternId()
        return self.cached("line", name, lambda: self._line_pattern(name))

    def _line_pattern(self, name):
        element = DB.LinePatternElement.GetLinePatternElementByName(doc, name)
        if element is not None:
            return element.Id
        definition = self.data.get("line_patterns", {}).get(name)
        if not definition:
            raise Missing("Line pattern '{}' isn't in this project".format(name))
        segments = List[DB.LinePatternSegment]()
        for seg_type, length in definition["segments"]:
            segments.Add(DB.LinePatternSegment(getattr(DB.LinePatternSegmentType, seg_type), length))
        pattern = DB.LinePattern(name)
        pattern.SetSegments(segments)
        try:
            created = DB.LinePatternElement.Create(doc, pattern)
        except Exception as ex:
            raise Missing("Line pattern '{}' could not be created ({})".format(name, ex))
        self.created.append("Line pattern '{}'".format(name))
        return created.Id

    def fill_pattern(self, ref):
        return self.cached("fill", "{}|{}".format(ref["target"], ref["name"]),
                           lambda: self._fill_pattern(ref))

    def _fill_pattern(self, ref):
        target = getattr(DB.FillPatternTarget, ref["target"])
        element = DB.FillPatternElement.GetFillPatternElementByName(doc, target, ref["name"])
        if element is not None:
            return element.Id
        key = "{}|{}".format(ref["target"], ref["name"])
        definition = self.data.get("fill_patterns", {}).get(key)
        if not definition:
            raise Missing("Fill pattern '{}' isn't in this project".format(ref["name"]))
        if definition["solid"]:
            # Every project has a solid fill; its name is just localised differently.
            for candidate in DB.FilteredElementCollector(doc).OfClass(DB.FillPatternElement):
                if candidate.GetFillPattern().IsSolidFill:
                    return candidate.Id
        pattern = DB.FillPattern(ref["name"], target,
                                 getattr(DB.FillPatternHostOrientation, definition["orientation"]))
        grids = List[DB.FillGrid]()
        for g in definition["grids"]:
            grid = DB.FillGrid()
            grid.Angle = g["angle"]
            grid.Origin = DB.UV(g["origin"][0], g["origin"][1])
            grid.Offset = g["offset"]
            grid.Shift = g["shift"]
            segments = List[System.Double]()
            for s in g["segments"]:
                segments.Add(s)
            grid.SetSegments(segments)
            grids.Add(grid)
        try:
            pattern.SetFillGrids(grids)
            created = DB.FillPatternElement.Create(doc, pattern)
        except Exception as ex:
            raise Missing("Fill pattern '{}' could not be created ({})".format(ref["name"], ex))
        self.created.append("Fill pattern '{}'".format(ref["name"]))
        return created.Id

    def overrides(self, data):
        ogs = DB.OverrideGraphicSettings()
        for key, _getter, setter, kind in OVERRIDES:
            if key not in data:
                continue
            value = data[key]
            if kind == "color":
                value = DB.Color(value[0], value[1], value[2])
            elif kind == "detail":
                value = getattr(DB.ViewDetailLevel, value)
            elif kind == "line":
                value = self.line_pattern(value)
            elif kind == "fill":
                value = self.fill_pattern(value)
            if value is not None:
                getattr(ogs, setter)(value)
        return ogs

    # ---- filters -----------------------------------------------------------

    def filter_id(self, name):
        return self.cached("filter", name, lambda: self._filter_id(name))

    def _filter_id(self, name):
        for element in DB.FilteredElementCollector(doc).OfClass(DB.FilterElement):
            if element.Name == name:
                return element.Id
        definition = self.data.get("filters", {}).get(name, {})
        if definition.get("kind") == "selection":
            raise Missing("Selection filter '{}' not imported: it lists specific elements, "
                          "which don't exist in another project".format(name))
        if "unsupported" in definition:
            raise Missing("Filter '{}' not imported: {}. Create it by hand".format(
                name, definition["unsupported"]))

        categories = List[DB.ElementId]()
        for bic_name in definition.get("categories", []):
            bic = getattr(DB.BuiltInCategory, bic_name, None)
            if bic is None or bic_name not in self.categories:
                self.note("Filter '{}': category '{}' dropped, not in this Revit".format(name, bic_name))
            else:
                categories.Add(DB.ElementId(bic))
        if categories.Count == 0:
            raise Missing("Filter '{}' not imported: none of its categories exist here".format(name))

        try:
            element_filter = self.element_filter(definition.get("rules"))
        except Missing as ex:
            raise Missing("Filter '{}' not imported: {}".format(name, ex))
        try:
            if element_filter is None:
                created = DB.ParameterFilterElement.Create(doc, name, categories)
            else:
                created = DB.ParameterFilterElement.Create(doc, name, categories, element_filter)
        except Exception as ex:
            raise Missing("Filter '{}' not imported: Revit refused it ({})".format(name, ex))
        self.created.append("Filter '{}'".format(name))
        return created.Id

    def element_filter(self, data):
        if data is None:
            return None
        if "logic" in data:
            children = List[DB.ElementFilter]()
            for child in data["filters"]:
                children.Add(self.element_filter(child))
            if data["logic"] == "and":
                return DB.LogicalAndFilter(children)
            return DB.LogicalOrFilter(children)
        rules = List[DB.FilterRule]()
        for rule in data["rules"]:
            rules.Add(self.rule(rule))
        return DB.ElementParameterFilter(rules, bool(data.get("inverted")))

    def rule(self, data):
        kind = data["kind"]
        if kind == "not":
            return DB.FilterInverseRule(self.rule(data["rule"]))
        if kind == "shared_applicable":
            return DB.SharedParameterApplicableRule(data["name"])

        param_id = self.param_id(data["param"])
        if param_id is None:
            raise Missing("its rule uses parameter '{}', which isn't here".format(param_label(data["param"])))
        if kind == "has_value":
            return DB.HasValueFilterRule(param_id)
        if kind == "has_no_value":
            return DB.HasNoValueFilterRule(param_id)

        evaluator_type = getattr(DB, data["evaluator"], None)
        if evaluator_type is None:
            raise Missing("its '{}' rule type doesn't exist in Revit {}".format(
                data["evaluator"], doc.Application.VersionNumber))
        provider = DB.ParameterValueProvider(param_id)
        evaluator = evaluator_type()
        if kind == "string":
            return DB.FilterStringRule(provider, evaluator, data["value"])
        if kind == "int":
            return DB.FilterIntegerRule(provider, evaluator, int(data["value"]))
        if kind == "double":
            return DB.FilterDoubleRule(provider, evaluator, float(data["value"]), float(data["epsilon"]))
        if kind == "element":
            value = self.element_id(data["value"])
            if value is None:
                raise Missing("its rule compares against '{}', which isn't here".format(data["value"]["name"]))
            return DB.FilterElementIdRule(provider, evaluator, value)
        raise Missing("it has a '{}' rule this tool doesn't know".format(kind))

    # ---- templates ---------------------------------------------------------

    def make_template(self, name, view_type_name):
        """///Summary: A new, empty-named template of the right view type, or Missing."""
        view_type = getattr(DB.ViewType, view_type_name, None)
        if view_type is None:
            raise Missing("View type '{}' doesn't exist in this Revit".format(view_type_name))
        views = [v for v in DB.FilteredElementCollector(doc).OfClass(DB.View) if v.ViewType == view_type]
        for view in views:
            if not view.IsTemplate:
                continue
            try:
                if view.CanViewBeDuplicated(DB.ViewDuplicateOption.Duplicate):
                    new_view = doc.GetElement(view.Duplicate(DB.ViewDuplicateOption.Duplicate))
                    new_view.Name = name
                    return new_view
            except Exception as ex:
                logger.debug("duplicating template %s failed: %s", view.Name, ex)
        for view in views:
            if view.IsTemplate:
                continue
            try:
                new_view = view.CreateViewTemplate()
                new_view.Name = name
                return new_view
            except Exception as ex:
                logger.debug("CreateViewTemplate from %s failed: %s", view.Name, ex)
        raise Missing("No {} view or template in this project to start from. Create one "
                      "and import again".format(view_type_name))

    def apply(self, view, template):
        by_id = {eid_int(p.Id): p for p in view.Parameters}
        for entry in template["parameters"]:
            param_id = self.param_id(entry["param"])
            if param_id is None:
                continue
            param = by_id.get(eid_int(param_id))
            if param is None or param.IsReadOnly:
                continue
            try:
                if "int" in entry:
                    param.Set(int(entry["int"]))
                elif "double" in entry:
                    param.Set(float(entry["double"]))
                elif "string" in entry:
                    param.Set(entry["string"])
                elif "element" in entry:
                    value = self.element_id(entry["element"])
                    if value is not None:
                        param.Set(value)
            except Exception as ex:
                self.note("Could not set '{}' ({})".format(param.Definition.Name, ex))

        controllable = {eid_int(i) for i in view.GetTemplateParameterIds()}
        uncontrolled = List[DB.ElementId]()
        for key in template["uncontrolled"]:
            param_id = self.param_id(key)
            if param_id is not None and eid_int(param_id) in controllable:
                uncontrolled.Add(param_id)
        view.SetNonControlledTemplateParameterIds(uncontrolled)

        if view.AreGraphicsOverridesAllowed():
            self.apply_categories(view, template)
        self.apply_filters(view, template)

    def apply_categories(self, view, template):
        hidden = set(template["hidden_categories"])
        overrides = {e["category"]: e["overrides"] for e in template["category_overrides"]}
        for key in sorted(hidden | set(overrides)):
            if key not in self.categories:
                self.note("Category '{}' doesn't exist in this Revit/project".format(key))
        # Only categories the source project also had: anything else is left as it was.
        for key in self.data.get("categories", []):
            cat = self.categories.get(key)
            if cat is None:
                continue
            try:
                if view.CanCategoryBeHidden(cat.Id):
                    view.SetCategoryHidden(cat.Id, key in hidden)
                view.SetCategoryOverrides(cat.Id, self.overrides(overrides.get(key, {})))
            except Exception as ex:
                logger.debug("category %s on %s: %s", key, view.Name, ex)

    def apply_filters(self, view, template):
        for existing in list(view.GetFilters()):
            view.RemoveFilter(existing)
        for entry in template["filters"]:
            filter_id = self.filter_id(entry["name"])
            if filter_id is None:
                continue
            view.AddFilter(filter_id)
            view.SetFilterOverrides(filter_id, self.overrides(entry["overrides"]))
            view.SetFilterVisibility(filter_id, entry["visible"])
            view.SetIsFilterEnabled(filter_id, entry["enabled"])


def load_file():
    """///Summary: Ask for an export file and parse it, or return None after saying why."""
    dialog = OpenFileDialog()
    dialog.Title = TOOL_TITLE
    dialog.Filter = "View template file (*.json)|*.json"
    if dialog.ShowDialog() != True:
        return None
    try:
        with open(dialog.FileName, encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except Exception as ex:
        TaskDialog.Show(TOOL_TITLE, "Could not read that file:\n{}\n\n{}".format(dialog.FileName, ex))
        return None
    if not isinstance(data, dict) or data.get("format") != FILE_FORMAT:
        TaskDialog.Show(TOOL_TITLE, "That isn't a file from this tool's Export. Run {} > Export "
                        "in the project you want to copy from, then pick its .json here.".format(TOOL_TITLE))
        return None
    return data


def ask_existing_policy(count):
    """///Summary: "overwrite", "skip", or None to cancel."""
    dialog = TaskDialog(TOOL_TITLE)
    dialog.MainInstruction = "{} of the chosen templates already exist in this project.".format(count)
    dialog.AddCommandLink(TaskDialogCommandLinkId.CommandLink1, "Overwrite them",
                          "Their settings, overrides and filters are replaced with the file's.")
    dialog.AddCommandLink(TaskDialogCommandLinkId.CommandLink2, "Skip them",
                          "Only templates that don't exist yet are created.")
    result = dialog.Show()
    if result == TaskDialogResult.CommandLink1:
        return "overwrite"
    if result == TaskDialogResult.CommandLink2:
        return "skip"
    return None


def import_main():
    """///Summary: Pick templates from a .json file and rebuild them in this project."""
    if doc.IsFamilyDocument:
        TaskDialog.Show(TOOL_TITLE, "Open a project, not a family, then run again.")
        return
    data = load_file()
    if data is None:
        return
    if not data.get("templates"):
        TaskDialog.Show(TOOL_TITLE, "That file has no templates in it. Export again and tick at least one.")
        return

    existing = {v.Name: v for v in DB.FilteredElementCollector(doc).OfClass(DB.View) if v.IsTemplate}
    items = []
    for template in sorted(data["templates"], key=lambda t: t["name"]):
        label = "{}   ({})".format(template["name"], template["view_type"])
        if template["name"] in existing:
            label += "   - already here"
        items.append(PickItem(label, template))
    prompt = "Templates in the file (from Revit {}, {}). Tick the ones to import:".format(
        data.get("revit_version", "?"), data.get("source_project", "?"))
    chosen = pick_items(items, TOOL_TITLE, prompt, "Import")
    if not chosen:
        return

    clashes = [t for t in chosen if t["name"] in existing]
    policy = "overwrite"
    if clashes:
        policy = ask_existing_policy(len(clashes))
        if policy is None:
            return

    importer = Importer(data)
    done = []
    group = DB.TransactionGroup(doc, TOOL_TITLE)
    group.Start()
    try:
        with revit.Transaction(TOOL_TITLE):
            for template in chosen:
                name = template["name"]
                importer.current = name
                view = existing.get(name)
                if view is not None and policy == "skip":
                    continue
                try:
                    action = "Updated"
                    if view is None:
                        view = importer.make_template(name, template["view_type"])
                        action = "Created"
                    importer.apply(view, template)
                    done.append((name, action))
                except Missing as ex:
                    importer.note(str(ex))
                except Exception as ex:
                    logger.error("template %s failed: %s", name, ex)
                    importer.note("Template not imported: {}".format(ex))
        if DRY_RUN:
            group.RollBack()
        else:
            group.Assimilate()
    except Exception as ex:
        if group.GetStatus() == DB.TransactionStatus.Started:
            group.RollBack()
        TaskDialog.Show(TOOL_TITLE, "Nothing was changed.\n\n{}".format(ex))
        return

    output.print_md("## {}{}".format(TOOL_TITLE, " (DRY RUN - rolled back)" if DRY_RUN else ""))
    output.print_md("From Revit {} into Revit {}: **{}** templates imported.".format(
        data.get("revit_version", "?"), doc.Application.VersionNumber, len(done)))
    if done:
        output.print_md(joolslib.markdown_table(done, ["Template", "Result"]))
    if importer.created:
        output.print_md("### Also created\n" + "\n".join("- " + c for c in importer.created))
    if importer.issues:
        output.print_md("### Not carried over - fix these by hand")
        rows = [(problem, ", ".join(names) or "-") for problem, names in importer.issues.items()]
        output.print_md(joolslib.markdown_table(rows, ["Problem", "Templates"]))
    else:
        output.print_md("Everything in the file was carried over.")



# ==== Entry ===================================================================

def main():
    """///Summary: Ask which direction, then run it."""
    dialog = TaskDialog(TOOL_TITLE)
    dialog.MainInstruction = "Export or import view templates?"
    dialog.MainContent = ("Export in the project you're copying FROM, then Import in the "
                          "project you're copying TO. Works between any Revit versions.")
    dialog.AddCommandLink(TaskDialogCommandLinkId.CommandLink1, "Export",
                          "Save templates from this project to a .json file.")
    dialog.AddCommandLink(TaskDialogCommandLinkId.CommandLink2, "Import",
                          "Rebuild templates in this project from a .json file.")
    result = dialog.Show()
    if result == TaskDialogResult.CommandLink1:
        export_main()
    elif result == TaskDialogResult.CommandLink2:
        import_main()


if __name__ == "__main__":
    main()
