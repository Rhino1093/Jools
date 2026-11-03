#! python3
# -*- coding: utf-8 -*-
"""Color an ACC Model Diff in both the up-to-date and the central model.

Run with both models open. The CSV's IDs are the up-to-date model's IDs.
Each model gets a new 3D view of just the changed elements:
Added = blue, Modified = yellow, Removed = red (central only).

Changed elements are also tagged with "Source Element ID" (their ID in the
up-to-date model). Tags set in the up-to-date model travel with the element
when it is copied into the central model, so nothing has to be typed by
hand. Nothing is pasted or deleted; that is left to the user.
"""
import csv
import datetime
import os
import re
import tempfile
import clr # type: ignore
import System # type: ignore
import joolslib          # Jools.extension/lib, see CLAUDE.md section 6a
joolslib.install_events_shim()   # must precede any pyrevit import

from pyrevit import revit, script

clr.AddReference("RevitAPI") # type: ignore
clr.AddReference("RevitAPIUI") # type: ignore
clr.AddReference("System.Windows.Forms") # type: ignore
clr.AddReference("System.Drawing") # type: ignore
from Autodesk.Revit import DB # type: ignore
from Autodesk.Revit.UI import TaskDialog, TaskDialogCommandLinkId, TaskDialogResult # type: ignore
from System.Collections.Generic import List # type: ignore
from Microsoft.Win32 import OpenFileDialog # type: ignore
from System.Windows.Forms import (Form, Label, ListBox, Button, DialogResult,  # type: ignore
                                  FormStartPosition, FormBorderStyle)
from System.Drawing import Size, Point # type: ignore

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
logger = script.get_logger()

TOOL_TITLE = "Model Diff"

# Must match SOURCE_ID_PARAM / SOURCE_ID_GUID in SetSourceId.pushbutton/script.py.
# The GUID makes it the same shared parameter in every model, which is what
# lets a tag travel with an element copied from one model to the other.
SOURCE_ID_PARAM = "Source Element ID"
SOURCE_ID_GUID = System.Guid("6f1c2a8e-3d4b-4c7a-9e51-2b8d0f6a4c13")

ADDED = "Added"
MODIFIED = "Modified"
REMOVED = "Removed"

COLORS = {
    ADDED: DB.Color(0, 0, 255),
    MODIFIED: DB.Color(255, 255, 0),
    REMOVED: DB.Color(255, 0, 0),
}

# First number inside the brackets. Stair rows look like
# "Assembled Stair [1839725/977405]"; the first number is the element.
ID_PATTERN = re.compile(r"\[(\d+)")


# ---------------------------------------------------------------- input

def pick_csv_file():
    dialog = OpenFileDialog()
    dialog.Title = "Select ACC Model Diff CSV"
    dialog.Filter = "CSV Files (*.csv)|*.csv"
    if dialog.ShowDialog() == True:
        return dialog.FileName
    return None


def pick_other_doc():
    """///Summary: The other open model to compare with, or None.

    With only one other project open it is used without asking.
    """
    candidates = [d for d in doc.Application.Documents
                  if not d.Equals(doc) and not d.IsFamilyDocument and not d.IsLinked]
    if not candidates:
        TaskDialog.Show(TOOL_TITLE,
                        "Only one model is open.\n\n"
                        "Open both the central model and the up-to-date model in this "
                        "Revit session (File > Open), then run again.")
        return None
    if len(candidates) == 1:
        return candidates[0]

    form = Form()
    form.Text = "{} - pick the other model".format(TOOL_TITLE)
    form.ClientSize = Size(500, 260)
    form.FormBorderStyle = FormBorderStyle.FixedDialog
    form.StartPosition = FormStartPosition.CenterScreen
    form.MinimizeBox = False
    form.MaximizeBox = False
    # Without this the dialog can open behind Revit and look like a hang.
    form.TopMost = True

    label = Label()
    label.Text = "Compare {} with which model?".format(doc.Title)
    label.Location = Point(12, 12)
    label.AutoSize = True
    form.Controls.Add(label)

    listbox = ListBox()
    listbox.Location = Point(12, 38)
    listbox.Size = Size(476, 170)
    for d in candidates:
        listbox.Items.Add(d.Title)
    listbox.SelectedIndex = 0
    form.Controls.Add(listbox)

    ok = Button()
    ok.Text = "OK"
    ok.DialogResult = DialogResult.OK
    ok.Location = Point(332, 222)
    form.Controls.Add(ok)

    cancel = Button()
    cancel.Text = "Cancel"
    cancel.DialogResult = DialogResult.Cancel
    cancel.Location = Point(413, 222)
    form.Controls.Add(cancel)

    form.AcceptButton = ok
    form.CancelButton = cancel

    if form.ShowDialog() != DialogResult.OK or listbox.SelectedIndex < 0:
        return None
    return candidates[listbox.SelectedIndex]


def pick_up_to_date(doc_a, doc_b):
    """///Summary: (up-to-date doc, central doc), or None if cancelled."""
    dialog = TaskDialog(TOOL_TITLE)
    dialog.MainInstruction = "Which one is the up-to-date model?"
    dialog.MainContent = "The CSV's element IDs come from the up-to-date model."
    dialog.AddCommandLink(TaskDialogCommandLinkId.CommandLink1, doc_a.Title)
    dialog.AddCommandLink(TaskDialogCommandLinkId.CommandLink2, doc_b.Title)
    result = dialog.Show()
    if result == TaskDialogResult.CommandLink1:
        return doc_a, doc_b
    if result == TaskDialogResult.CommandLink2:
        return doc_b, doc_a
    return None


def make_element_id(id_int):
    """///Summary: An ElementId from an integer, valid in Revit 2022-2026.

    Revit 2022/2023 only have ElementId(Int32) and 2026 only has
    ElementId(Int64), so try the 64-bit form and fall back.
    """
    try:
        return DB.ElementId(System.Int64(id_int))
    except Exception:
        return DB.ElementId(System.Int32(id_int))


def one_line(text):
    """Collapse a CSV cell onto one line so it cannot break a markdown table."""
    return " ".join((text or "").split())


def read_diff_csv(path):
    """///Summary: Parse the ACC CSV into {element id: entry}.

    Each entry holds the row name, the set of changes seen for that ID, and
    the Modification Type / Properties text. One ID can appear on several
    rows (stairs are listed once as removed and once as added).
    """
    entries = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = [h.strip().lower() for h in next(reader, [])]

        def column(name, fallback):
            return header.index(name) if name in header else fallback

        name_col = column("name", 0)
        change_col = column("change", 1)
        type_col = column("modification type", 2)
        props_col = column("properties", 3)

        for row in reader:
            if len(row) <= max(name_col, change_col):
                continue
            match = ID_PATTERN.search(row[name_col])
            if not match:
                continue
            change = row[change_col].strip().lower()
            if change not in ("added", "modified", "removed"):
                continue

            id_int = int(match.group(1))
            entry = entries.setdefault(id_int, {
                "name": row[name_col].strip(),
                "changes": set(),
                "details": [],
            })
            entry["changes"].add(change)
            for col in (type_col, props_col):
                if col < len(row) and row[col].strip():
                    entry["details"].append(one_line(row[col]))
    return entries


def classify(changes):
    """///Summary: The single status an element is colored by.

    An element listed as both removed and added still exists in both
    versions, so it was changed rather than replaced: treat it as modified.
    """
    if "modified" in changes or ("added" in changes and "removed" in changes):
        return MODIFIED
    if "added" in changes:
        return ADDED
    return REMOVED


# ---------------------------------------------------- finding elements

def source_id_of(element):
    """The element's Source Element ID text, or "" if it has none."""
    param = element.LookupParameter(SOURCE_ID_PARAM)
    if param is None or not param.HasValue:
        return ""
    return (param.AsString() or "").strip()


def build_source_index(d):
    """///Summary: Map each Source Element ID value to the elements carrying it.

    Returns an empty dict without scanning the model when the parameter
    has never been added to it.
    """
    definition, _ = find_binding(d)
    index = {}
    if definition is None:
        return index
    for el in DB.FilteredElementCollector(d).WhereElementIsNotElementType():
        value = source_id_of(el)
        if value:
            index.setdefault(value, []).append(el)
    return index


def find_in_up_to_date(d, id_int):
    """The up-to-date model's element with this ID, as a list (empty if none)."""
    el = d.GetElement(make_element_id(id_int))
    if el is None or isinstance(el, DB.ElementType):
        return []
    return [el]


def find_in_central(d, id_int, status, source_index):
    """///Summary: The central model's elements for one CSV ID.

    The Source Element ID wins over Revit's own ID. Added elements are only
    found by their tag: they are new, so a matching native ID in the
    central model is a coincidence. For the others, a native ID match is
    skipped when that element is tagged as a copy of something else.
    """
    tagged = source_index.get(str(id_int))
    if tagged:
        return tagged
    if status == ADDED:
        return []

    el = d.GetElement(make_element_id(id_int))
    if el is None or isinstance(el, DB.ElementType):
        return []
    own_tag = source_id_of(el)
    if own_tag and own_tag != str(id_int):
        return []
    return [el]


# ------------------------------------------- Source Element ID parameter
# Same approach as SetSourceId.pushbutton/script.py, for any open model.

def find_binding(d):
    """(definition, binding) of the Source Element ID parameter, or (None, None)."""
    iterator = d.ParameterBindings.ForwardIterator()
    while iterator.MoveNext():
        if iterator.Key.Name == SOURCE_ID_PARAM:
            return iterator.Key, iterator.Current
    return None, None


def create_definition(d):
    """///Summary: The Source Element ID shared parameter definition.

    Revit only creates shared parameters from a shared parameter file, so
    this writes a throwaway one to the temp folder and points Revit at it
    just long enough to create the definition, then restores the user's
    own file.
    """
    app = d.Application
    original_file = app.SharedParametersFilename
    temp_file = os.path.join(tempfile.gettempdir(), "Jools_SourceElementId.txt")
    open(temp_file, "w").close()
    try:
        app.SharedParametersFilename = temp_file
        group = app.OpenSharedParameterFile().Groups.Create("Jools")
        options = DB.ExternalDefinitionCreationOptions(SOURCE_ID_PARAM, DB.SpecTypeId.String.Text)
        options.GUID = SOURCE_ID_GUID
        options.Description = ("ID of this element in the up-to-date model it was copied "
                               "from. Used by Jools Model Diff.")
        return group.Definitions.Create(options)
    finally:
        try:
            app.SharedParametersFilename = original_file or ""
        except Exception as e:
            logger.warning("Could not restore shared parameter file '%s': %s", original_file, e)
        try:
            os.remove(temp_file)
        except OSError:
            pass


def ensure_source_param(d, categories):
    """///Summary: Make sure elements of these categories have Source Element ID.

    Adds the parameter to the model on first use, and adds any category
    it is missing. Must run inside a transaction.
    """
    app = d.Application
    definition, binding = find_binding(d)
    if definition is None:
        definition = create_definition(d)
        category_set = app.Create.NewCategorySet()
        for category in categories:
            category_set.Insert(category)
        d.ParameterBindings.Insert(definition, app.Create.NewInstanceBinding(category_set),
                                   DB.GroupTypeId.IdentityData)
        return

    category_set = binding.Categories
    missing = [c for c in categories if not category_set.Contains(c)]
    if not missing:
        return
    for category in missing:
        category_set.Insert(category)
    d.ParameterBindings.ReInsert(definition, app.Create.NewInstanceBinding(category_set),
                                 DB.GroupTypeId.IdentityData)


def can_hold_tag(element):
    return element.Category is not None and element.Category.AllowsBoundParameters


def editable_targets(d, targets):
    """///Summary: Split tag targets into ones this user may edit and ones not.

    targets is [(element, value)]. In a workshared model each element must
    be borrowed first and be current with central, or the whole
    transaction fails on commit. Returns (editable, [(element, reason)]).
    Must run outside a transaction, since borrowing talks to central.
    """
    # A detached copy has no central to borrow from; everything is editable.
    if not targets or not d.IsWorkshared or d.IsDetached:
        return targets, []

    ids = List[DB.ElementId]()
    for el, _ in targets:
        ids.Add(el.Id)
    try:
        owned = {joolslib.eid_int(i) for i in DB.WorksharingUtils.CheckoutElements(d, ids)}
    except Exception as e:
        reason = "couldn't reach the central model to borrow it: {}".format(one_line(str(e)))
        return [], [(el, reason) for el, _ in targets]

    out_of_date = (DB.ModelUpdatesStatus.UpdatedInCentral, DB.ModelUpdatesStatus.DeletedInCentral)
    editable, problems = [], []
    for el, value in targets:
        if joolslib.eid_int(el.Id) not in owned:
            problems.append((el, "someone else is editing it"))
        elif DB.WorksharingUtils.GetModelUpdatesStatus(d, el.Id) in out_of_date:
            problems.append((el, "changed in central since you opened the model; "
                                 "Reload Latest, then run again"))
        else:
            editable.append((el, value))
    return editable, problems


def tag_elements(d, targets):
    """///Summary: Set Source Element ID on each (element, value). Must run in a transaction."""
    categories = {}
    for el, _ in targets:
        categories[joolslib.eid_int(el.Category.Id)] = el.Category
    if categories:
        ensure_source_param(d, list(categories.values()))

    tagged, problems = 0, []
    for el, value in targets:
        param = el.LookupParameter(SOURCE_ID_PARAM)
        if param is None or param.IsReadOnly:
            problems.append((el, "the parameter is missing or read-only on it"))
            continue
        param.Set(value)
        tagged += 1
    return tagged, problems


# ----------------------------------------------------------------- view

def get_solid_fill_pattern_id(d):
    for fp in DB.FilteredElementCollector(d).OfClass(DB.FillPatternElement):
        pattern = fp.GetFillPattern()
        if pattern.IsSolidFill and pattern.Target == DB.FillPatternTarget.Drafting:
            return fp.Id
    return DB.ElementId.InvalidElementId


def make_overrides(color, solid_fill_id):
    ogs = DB.OverrideGraphicSettings()
    ogs.SetSurfaceForegroundPatternColor(color)
    ogs.SetCutForegroundPatternColor(color)
    if solid_fill_id != DB.ElementId.InvalidElementId:
        ogs.SetSurfaceForegroundPatternId(solid_fill_id)
        ogs.SetCutForegroundPatternId(solid_fill_id)
    return ogs


def get_3d_view_type(d):
    for vft in DB.FilteredElementCollector(d).OfClass(DB.ViewFamilyType):
        if vft.ViewFamily == DB.ViewFamily.ThreeDimensional:
            return vft
    return None


def create_diff_view(d, colored, view_name):
    """///Summary: New 3D view with only the given elements, colored by status.

    colored is [(element, status)]. View-specific elements (tags,
    dimensions) can't appear in a 3D view and are left out.
    """
    vft_3d = get_3d_view_type(d)
    view3d = DB.View3D.CreateIsometric(d, vft_3d.Id)
    view3d.Name = view_name

    # Isolation needs the new view regenerated first.
    d.Regenerate()

    colored = [(el, status) for el, status in colored if not el.ViewSpecific]
    all_ids = List[DB.ElementId]()
    for el, _ in colored:
        all_ids.Add(el.Id)
    if all_ids.Count:
        try:
            view3d.IsolateElementsTemporary(all_ids)
            view3d.ConvertTemporaryHideIsolateToPermanent()
        except Exception as e:
            logger.warning("Could not permanently isolate elements: {}".format(e))

    solid_fill_id = get_solid_fill_pattern_id(d)
    overrides = {status: make_overrides(color, solid_fill_id)
                 for status, color in COLORS.items()}
    for el, status in colored:
        try:
            view3d.SetElementOverrides(el.Id, overrides[status])
        except Exception as e:
            logger.debug("No override for %s: %s", joolslib.eid_int(el.Id), e)
    return view3d


# ------------------------------------------------------------ per model

def update_model(d, colored, targets, view_name):
    """///Summary: Tag the targets and create the colored view in one model.

    Returns a result dict for the report: the view (or None), how many
    elements were tagged, and [(element, reason)] for those that weren't.
    A failure here is reported rather than raised, so the other model
    still gets its view.
    """
    result = {"doc": d, "view": None, "tagged": 0, "problems": [], "error": None}
    if not colored:
        result["error"] = "none of the CSV's elements are in this model, so no view was made"
        return result
    if d.IsReadOnly:
        result["error"] = "the model is read-only, so no view or tags could be added"
        return result
    if get_3d_view_type(d) is None:
        result["error"] = ("the model has no 3D view type; create any 3D view once "
                           "(View > 3D View), then run again")
        return result

    targets, result["problems"] = editable_targets(d, targets)

    # DB.Transaction rather than revit.Transaction: one of the two models
    # is not revit.doc.
    transaction = DB.Transaction(d, "Model Diff")
    transaction.Start()
    try:
        tagged, problems = tag_elements(d, targets)
        result["problems"] += problems
        view = create_diff_view(d, colored, view_name)
        if transaction.Commit() == DB.TransactionStatus.Committed:
            result["view"], result["tagged"] = view, tagged
        else:
            result["error"] = "Revit refused the changes; nothing was added to this model"
    except Exception as e:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        logger.debug("update_model failed in %s: %s", d.Title, e)
        result["error"] = "nothing was added to this model: {}".format(one_line(str(e)))
    return result


# --------------------------------------------------------------- report

def links(d, elements):
    """Clickable links for the active model's elements, plain IDs for the other."""
    if not elements:
        return "-"
    if d.Equals(doc):
        return ", ".join(output.linkify(el.Id) for el in elements)
    return ", ".join(str(joolslib.eid_int(el.Id)) for el in elements)


def report_model(label, result, rows, key):
    d = result["doc"]
    output.print_md("## {}: {}".format(label, d.Title))
    if result["error"]:
        output.print_md("**Problem:** {}.".format(result["error"]))
    else:
        where = ("opened now" if d.Equals(doc) else
                 "switch to this model, then open it from Project Browser > 3D Views")
        output.print_md("View **{}** ({}). Tagged {} elements with {}.".format(
            result["view"].Name, where, result["tagged"], SOURCE_ID_PARAM))

    shown = [r for r in rows if r[key]]
    if shown:
        table = [[r["status"], r["id"], links(d, r[key]), r["name"], "; ".join(r["details"])]
                 for r in sorted(shown, key=lambda r: (r["status"], r["name"]))]
        output.print_md(joolslib.markdown_table(
            table, ["Status", "CSV ID", "Element", "CSV Name", "What changed"]))

    if result["problems"]:
        output.print_md("### Not tagged in {}".format(d.Title))
        output.print_md("Fix these by hand with Set Source ID.")
        table = [[links(d, [el]), source_id_of(el) or "-", reason]
                 for el, reason in result["problems"]]
        output.print_md(joolslib.markdown_table(table, ["Element", "Current tag", "Why"]))


def report(csv_path, up_result, central_result, rows):
    output.print_md("# {}: {}".format(TOOL_TITLE, os.path.basename(csv_path)))
    counts = []
    for status in (ADDED, MODIFIED, REMOVED):
        of_status = [r for r in rows if r["status"] == status]
        counts.append([status, len(of_status),
                       sum(1 for r in of_status if r["up"]),
                       sum(1 for r in of_status if r["central"])])
    output.print_md(joolslib.markdown_table(
        counts, ["Status", "In CSV", "Found in up-to-date", "Found in central"]))
    output.print_md("Blue = added, yellow = modified, red = removed. Added elements "
                    "show up in the central model once you've copied them in.")

    report_model("Up-to-date model", up_result, rows, "up")
    report_model("Central model", central_result, rows, "central")


# ----------------------------------------------------------------- main

def main():
    if doc.IsFamilyDocument:
        TaskDialog.Show(TOOL_TITLE, "Open a project model, not a family, then run again.")
        return

    csv_path = pick_csv_file()
    if not csv_path:
        return

    try:
        entries = read_diff_csv(csv_path)
    except Exception as e:
        TaskDialog.Show(TOOL_TITLE,
                        "Could not read the CSV file:\n{}\n\n{}\n\n"
                        "Check that it is the CSV exported from ACC Model Diff "
                        "and that it is not open in Excel.".format(csv_path, e))
        return

    if not entries:
        TaskDialog.Show(TOOL_TITLE,
                        "No added, modified or removed elements were found in the CSV.\n\n"
                        "Check that it is the CSV exported from ACC Model Diff.")
        return

    other = pick_other_doc()
    if other is None:
        return
    picked = pick_up_to_date(doc, other)
    if picked is None:
        return
    up_doc, central_doc = picked

    source_index = build_source_index(central_doc)
    rows = []
    for id_int, entry in entries.items():
        status = classify(entry["changes"])
        rows.append({
            "id": id_int,
            "name": entry["name"],
            "status": status,
            "details": entry["details"],
            # Removed elements are gone from the up-to-date model by definition.
            "up": [] if status == REMOVED else find_in_up_to_date(up_doc, id_int),
            "central": find_in_central(central_doc, id_int, status, source_index),
        })
    logger.debug("csv_ids=%s, tagged_in_central=%s, up=%s, central=%s",
                 len(entries), len(source_index), up_doc.Title, central_doc.Title)

    if not any(r["up"] or r["central"] for r in rows):
        TaskDialog.Show(TOOL_TITLE,
                        "None of the {} elements in the CSV were found in either model.\n\n"
                        "Check that the CSV was made from {}.".format(len(entries), up_doc.Title))
        return

    # In the up-to-date model an element's own ID is the right tag, so an
    # existing different value is corrected. In the central model existing
    # tags are kept: they may have been set by hand with Set Source ID.
    up_targets, central_targets = [], []
    up_colored, central_colored = [], []
    for r in rows:
        value = str(r["id"])
        for el in r["up"]:
            up_colored.append((el, r["status"]))
            if can_hold_tag(el) and source_id_of(el) != value:
                up_targets.append((el, value))
        for el in r["central"]:
            central_colored.append((el, r["status"]))
            if can_hold_tag(el) and not source_id_of(el):
                central_targets.append((el, value))

    # Same name in both models so the matching pair is easy to find.
    view_name = "Model Diff - {} - {}".format(
        datetime.date.today().isoformat(), System.Guid.NewGuid().ToString()[:8])
    # Printing first opens the output window, so there is visible activity.
    output.print_md("Tagging and creating views in both models...")
    up_result = update_model(up_doc, up_colored, up_targets, view_name)
    central_result = update_model(central_doc, central_colored, central_targets, view_name)

    for result in (up_result, central_result):
        if result["view"] is not None and result["doc"].Equals(doc):
            uidoc.ActiveView = result["view"]
    report(csv_path, up_result, central_result, rows)


if __name__ == "__main__":
    main()
