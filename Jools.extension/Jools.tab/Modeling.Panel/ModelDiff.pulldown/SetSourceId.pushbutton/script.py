#! python3
# -*- coding: utf-8 -*-
"""Set or clear one element's Source Element ID.

Copy/paste between models gives every copy a new element ID, so Model Diff
stops finding it. Typing the original's ID into "Source Element ID" fixes
that: Model Diff looks the parameter up before the element's own ID.
"""
import os
import tempfile
import clr # type: ignore
import System # type: ignore
import joolslib          # Jools.extension/lib, see CLAUDE.md section 6a
joolslib.install_events_shim()   # must precede any pyrevit import

from pyrevit import revit, script

clr.AddReference("RevitAPI") # type: ignore
clr.AddReference("RevitAPIUI") # type: ignore
from Autodesk.Revit import DB # type: ignore
from Autodesk.Revit.UI import TaskDialog # type: ignore
from Autodesk.Revit.UI.Selection import ObjectType # type: ignore
from Autodesk.Revit.Exceptions import OperationCanceledException # type: ignore

doc = revit.doc
uidoc = revit.uidoc
logger = script.get_logger()

TOOL_TITLE = "Set Source ID"

# Must match SOURCE_ID_PARAM in ModelDiff.pushbutton/script.py. The GUID
# makes it the same shared parameter in every model, so a value survives
# being copied to another model.
SOURCE_ID_PARAM = "Source Element ID"
SOURCE_ID_GUID = System.Guid("6f1c2a8e-3d4b-4c7a-9e51-2b8d0f6a4c13")


def get_target():
    """Return the one element to tag, or None after telling the user why not."""
    ids = list(uidoc.Selection.GetElementIds())
    if len(ids) > 1:
        TaskDialog.Show(TOOL_TITLE,
                        "{} elements are selected. Select just one, then run again.".format(len(ids)))
        return None
    if ids:
        element = doc.GetElement(ids[0])
    else:
        try:
            reference = uidoc.Selection.PickObject(
                ObjectType.Element, "Click the element to set its Source Element ID")
        except OperationCanceledException:
            return None
        element = doc.GetElement(reference)

    if (isinstance(element, DB.ElementType) or element.Category is None
            or not element.Category.AllowsBoundParameters):
        TaskDialog.Show(TOOL_TITLE,
                        "This element can't hold a Source Element ID.\n\n"
                        "Select a model element (wall, door, floor, ...), then run again.")
        return None
    return element


def find_binding():
    """(definition, binding) of the Source Element ID parameter, or (None, None)."""
    iterator = doc.ParameterBindings.ForwardIterator()
    while iterator.MoveNext():
        if iterator.Key.Name == SOURCE_ID_PARAM:
            return iterator.Key, iterator.Current
    return None, None


def create_definition():
    """///Summary: The Source Element ID shared parameter definition.

    Revit only creates shared parameters from a shared parameter file, so
    this writes a throwaway one to the temp folder and points Revit at it
    just long enough to create the definition, then restores the user's
    own file.
    """
    app = doc.Application
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


def ensure_source_param(category):
    """///Summary: Make sure elements of this category have Source Element ID.

    Adds the parameter to the model on first use, and adds each new
    category to it the first time an element of that category is tagged.
    Must run inside a transaction.
    """
    app = doc.Application
    definition, binding = find_binding()
    if definition is None:
        definition = create_definition()
        categories = app.Create.NewCategorySet()
        categories.Insert(category)
        doc.ParameterBindings.Insert(definition, app.Create.NewInstanceBinding(categories),
                                     DB.GroupTypeId.IdentityData)
        return

    categories = binding.Categories
    categories.Insert(category)
    doc.ParameterBindings.ReInsert(definition, app.Create.NewInstanceBinding(categories),
                                   DB.GroupTypeId.IdentityData)


def element_label(element):
    el_type = doc.GetElement(element.GetTypeId())
    parts = [element.Category.Name]
    if el_type is not None:
        parts += [getattr(el_type, "FamilyName", "") or "", el_type.Name]
    return " : ".join(p for p in parts if p)


def main():
    element = get_target()
    if element is None:
        return

    param = element.LookupParameter(SOURCE_ID_PARAM)
    current = (param.AsString() or "") if param is not None and param.HasValue else ""

    value = joolslib.ask_for_string(
        "ID of the original in the up-to-date model (leave empty to clear):",
        current,
        "{} - {} [{}]".format(TOOL_TITLE, element_label(element), joolslib.eid_int(element.Id)))
    if value is None:
        return
    value = value.strip()
    if value and not value.isdigit():
        TaskDialog.Show(TOOL_TITLE,
                        "'{}' is not an element ID. An ID is a whole number, like 3501621.\n\n"
                        "Nothing was changed.".format(value))
        return
    if value == current:
        return

    problem = None
    with revit.Transaction("Set Source Element ID"):
        if param is None:
            ensure_source_param(element.Category)
            param = element.LookupParameter(SOURCE_ID_PARAM)
        if param is None or param.IsReadOnly:
            problem = "the parameter is missing or read-only on this element"
        else:
            try:
                param.Set(value)
            except Exception as e:
                # Most often another user owns the element in a workshared model.
                problem = str(e)

    if problem:
        logger.debug("set failed: id=%s, value=%s, problem=%s",
                     joolslib.eid_int(element.Id), value, problem)
        TaskDialog.Show(TOOL_TITLE,
                        "The Source Element ID could not be set: {}.\n\n"
                        "In a workshared model, check that nobody else is editing this "
                        "element, then try again.".format(problem))


if __name__ == "__main__":
    main()
