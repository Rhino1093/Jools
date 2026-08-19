#! python3
# -*- coding: utf-8 -*-
import csv
import re
import clr # type: ignore
import System # type: ignore
import joolslib          # Jools.extension/lib, see CLAUDE.md section 6a
joolslib.install_events_shim()   # must precede any pyrevit import

from pyrevit import revit, script

clr.AddReference("RevitAPI") # type: ignore
clr.AddReference("PresentationFramework") # type: ignore
from Autodesk.Revit import DB # type: ignore
from Autodesk.Revit.UI import TaskDialog # type: ignore
from System.Collections.Generic import List # type: ignore
from Microsoft.Win32 import OpenFileDialog # type: ignore

doc = revit.doc
uidoc = revit.uidoc
logger = script.get_logger()

TOOL_TITLE = "Model Diff"

def pick_csv_file():
    dialog = OpenFileDialog()
    dialog.Title = "Select ACC Model Diff CSV"
    dialog.Filter = "CSV Files (*.csv)|*.csv"
    if dialog.ShowDialog() == True:
        return dialog.FileName
    return None

def get_solid_fill_pattern_id(doc):
    fill_patterns = DB.FilteredElementCollector(doc).OfClass(DB.FillPatternElement)
    for fp in fill_patterns:
        try:
            if fp.GetFillPattern().IsSolid:
                return fp.Id
        except AttributeError:
            if "solid fill" in fp.Name.lower() or "<solid fill>" in fp.Name.lower() or "solid" == fp.Name.lower():
                return fp.Id
    return DB.ElementId.InvalidElementId

def main():
    csv_path = pick_csv_file()
    if not csv_path:
        return

    modified_ids = []
    removed_ids = []

    try:
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) < 2:
                    continue
                name_col = row[0]
                change_col = row[1].strip().lower()

                match = re.search(r"\[(\d+)", name_col)
                if match:
                    id_int = int(match.group(1))
                    if change_col == 'modified':
                        modified_ids.append(DB.ElementId(id_int))
                    elif change_col == 'removed':
                        removed_ids.append(DB.ElementId(id_int))
    except Exception as e:
        TaskDialog.Show(TOOL_TITLE, "Error reading CSV file: {}".format(e))
        return

    if not modified_ids and not removed_ids:
        TaskDialog.Show(TOOL_TITLE, "No modified or removed elements found in the CSV.")
        return

    with revit.Transaction("Create Model Diff View"):
        valid_modified = [eid for eid in modified_ids if doc.GetElement(eid) is not None]
        valid_removed = [eid for eid in removed_ids if doc.GetElement(eid) is not None]

        all_valid_ids = valid_modified + valid_removed

        if not all_valid_ids:
            TaskDialog.Show(TOOL_TITLE, "None of the elements listed in the CSV could be found in the current model.")
            return

        view_family_types = DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType).WhereElementIsElementType().ToElements()
        vft_3d = None
        for v in view_family_types:
            if v.ViewFamily == DB.ViewFamily.ThreeDimensional:
                vft_3d = v
                break
                
        if not vft_3d:
            TaskDialog.Show(TOOL_TITLE, "Could not find a 3D View Family Type.")
            return

        view3d = DB.View3D.CreateIsometric(doc, vft_3d.Id)
        view3d.Name = "Model Diff - " + System.Guid.NewGuid().ToString()[:8]
        
        # Needs regeneration before isolation
        doc.Regenerate()

        list_ids = List[DB.ElementId]()
        for eid in all_valid_ids:
            list_ids.Add(eid)
            
        try:
            view3d.IsolateElementsTemporary(list_ids)
            view3d.ConvertTemporaryHideIsolateToPermanent()
        except Exception as e:
            logger.warning("Could not permanently isolate elements: {}".format(e))

        solid_fill_id = get_solid_fill_pattern_id(doc)
        
        # Color Yellow for modified
        if valid_modified:
            ogs_yellow = DB.OverrideGraphicSettings()
            ogs_yellow.SetSurfaceForegroundPatternColor(DB.Color(255, 255, 0))
            ogs_yellow.SetCutForegroundPatternColor(DB.Color(255, 255, 0))
            if solid_fill_id != DB.ElementId.InvalidElementId:
                ogs_yellow.SetSurfaceForegroundPatternId(solid_fill_id)
                ogs_yellow.SetCutForegroundPatternId(solid_fill_id)
            
            for eid in valid_modified:
                try:
                    view3d.SetElementOverrides(eid, ogs_yellow)
                except Exception:
                    pass

        # Color Red for removed
        if valid_removed:
            ogs_red = DB.OverrideGraphicSettings()
            ogs_red.SetSurfaceForegroundPatternColor(DB.Color(255, 0, 0))
            ogs_red.SetCutForegroundPatternColor(DB.Color(255, 0, 0))
            if solid_fill_id != DB.ElementId.InvalidElementId:
                ogs_red.SetSurfaceForegroundPatternId(solid_fill_id)
                ogs_red.SetCutForegroundPatternId(solid_fill_id)
                
            for eid in valid_removed:
                try:
                    view3d.SetElementOverrides(eid, ogs_red)
                except Exception:
                    pass

    uidoc.ActiveView = view3d

if __name__ == "__main__":
    main()
