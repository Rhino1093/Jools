#! python3
# -*- coding: utf-8 -*-

__author__ = "Ryan Johnston"
__date__ = "2025-12-15"
__purpose__ = "Adjusts the height of selected elements to snap to the nearest ceiling in linked models."
import clr # type: ignore
clr.AddReference("RevitAPI")  # type: ignore
clr.AddReference("RevitAPIUI")  # type: ignore

import sys

# Patch sys.stdout.flush if it's missing (CPython engine workaround)
if not hasattr(sys.stdout, "flush"):
    sys.stdout.flush = lambda: None

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    BuiltInCategory,
    Transaction,
    ReferenceIntersector,
    FindReferenceTarget,
    XYZ,
    ElementCategoryFilter,
    ViewType,
    StorageType
)
from Autodesk.Revit.UI import TaskDialog
from pyrevit import revit, script

# Helper for printing debug messages
output = script.get_output()
logger = script.get_logger()

def print_debug(msg):
    """Prints debug messages to the pyRevit console."""
    output.print_md(u"**DEBUG:** {}".format(msg))

def get_parameter_value(param):
    """Safe getter for parameter value based on storage type."""
    if not param:
        return 0.0
    if param.StorageType == StorageType.Double:
        return param.AsDouble()
    elif param.StorageType == StorageType.Integer:
        return param.AsInteger()
    elif param.StorageType == StorageType.String:
        return 0.0 # Cannot calculate with string
    return 0.0

def set_parameter_value(param, value):
    """Safe setter for parameter value."""
    if not param or param.IsReadOnly:
        return False
    if param.StorageType == StorageType.Double:
        return param.Set(value)
    elif param.StorageType == StorageType.Integer:
        return param.Set(int(value))
    return False

def main():
    doc = revit.doc
    uidoc = revit.uidoc
    active_view = doc.ActiveView

    # -------------------------------------------------------------------------
    # 1. Validate Context (3D View)
    # -------------------------------------------------------------------------
    if active_view.ViewType != ViewType.ThreeD:
        TaskDialog.Show(
            "View Context Error",
            "This script requires an active 3D View to calculate intersections.\n"
            "Please switch to a 3D View and try again."
        )
        return

    selection = revit.get_selection()
    if not selection:
        TaskDialog.Show("Selection Error", "Please select elements to adjust.")
        return

    # -------------------------------------------------------------------------
    # 2. Setup Reference Intersector
    # -------------------------------------------------------------------------
    print_debug("Setting up ReferenceIntersector...")
    
    # Target Ceilings
    target_category_filter = ElementCategoryFilter(BuiltInCategory.OST_Ceilings)
    
    # Initialize Intersector
    # FindReferenceTarget.All allows finding faces, edges, etc. usually Mesh or Element is enough but All is safest.
    intersector = ReferenceIntersector(target_category_filter, FindReferenceTarget.All, active_view)
    intersector.FindReferencesInRevitLinks = True # CRITICAL for linked models
    
    # -------------------------------------------------------------------------
    # 3. Process Selected Elements
    # -------------------------------------------------------------------------
    print_debug("Processing {} selected elements...".format(len(selection)))
    
    moved_elements_report = []
    
    t = Transaction(doc, "Snap Elements to Ceiling")
    t.Start()
    
    modified_count = 0
    
    for el in selection:
        try:
            # Get Location Point
            if not hasattr(el.Location, "Point"):
                print_debug("Element ID {} has no point location. Skipping.".format(el.Id))
                continue
            
            location_pt = el.Location.Point
            
            # -----------------------------------------------------------------
            # 4. Cast Rays (Up and Down)
            # -----------------------------------------------------------------
            # We use the location point as origin. 
            # We might need to lift/lower the origin slightly to avoid self-intersection if the element IS the ceiling (unlikely here)
            # But since we are looking for linked ceilings, self-intersection is less of a risk unless they overlap.
            
            ref_up = intersector.FindNearest(location_pt, XYZ.BasisZ)
            ref_down = intersector.FindNearest(location_pt, XYZ.BasisZ.Negate())
            
            target_pt = None
            dist_up = float('inf')
            dist_down = float('inf')

            if ref_up:
                hit_pt_up = ref_up.GetReference().GlobalPoint
                dist_up = hit_pt_up.DistanceTo(location_pt)
                print_debug("ID {}: Found ceiling ABOVE at dist {}".format(el.Id, dist_up))
            
            if ref_down:
                hit_pt_down = ref_down.GetReference().GlobalPoint
                dist_down = hit_pt_down.DistanceTo(location_pt)
                print_debug("ID {}: Found ceiling BELOW at dist {}".format(el.Id, dist_down))

            # Determine closest ceiling
            closest_dist = float('inf')
            
            if dist_up < dist_down:
                target_pt = ref_up.GetReference().GlobalPoint
                closest_dist = dist_up
            elif dist_down < dist_up:
                target_pt = ref_down.GetReference().GlobalPoint
                closest_dist = dist_down
            
            if not target_pt:
                print_debug("ID {}: No ceiling found vertically.".format(el.Id))
                continue

            # -----------------------------------------------------------------
            # 5. Adjust Parameter
            # -----------------------------------------------------------------
            # TODO: Expand logic to allow user to select which parameter to adjust (e.g. via UI)
            
            # Strategy: Find the vertical delta required
            # Current Z = location_pt.Z
            # Target Z = target_pt.Z
            # Delta = Target Z - Current Z
            
            z_delta = target_pt.Z - location_pt.Z
            
            # Try to find a valid parameter
            # Priority: "Offset from Host" -> "Elevation from Level" -> "Offset"
            param_names = ["Offset from Host", "Elevation from Level", "Offset"]
            target_param = None
            
            for p_name in param_names:
                p = el.LookupParameter(p_name)
                if p and not p.IsReadOnly:
                    target_param = p
                    print_debug("ID {}: Using parameter '{}'".format(el.Id, p_name))
                    break
            
            if target_param:
                # Special logic: Reset 'Mounting Height' if using 'Offset from Host'
                if target_param.Definition.Name == "Offset from Host":
                    mounting_height_param = el.LookupParameter("Mounting Height")
                    if mounting_height_param and not mounting_height_param.IsReadOnly:
                        set_parameter_value(mounting_height_param, 0)
                        print_debug("ID {}: Reset 'Mounting Height' to 0".format(el.Id))

                current_val = get_parameter_value(target_param)
                new_val = current_val + z_delta
                
                success = set_parameter_value(target_param, new_val)
                if success:
                    modified_count += 1
                    
                    # Gather data for report
                    try:
                        el_type = doc.GetElement(el.GetTypeId())
                        fam_name = el_type.FamilyName if el_type else "Unknown"
                        type_name = el_type.Name if el_type else "Unknown"
                    except:
                        fam_name = "Unknown"
                        type_name = "Unknown"

                    fmt_delta = "{:.2f}".format(z_delta)
                    
                    moved_elements_report.append({
                        "Element ID": output.linkify(el.Id),
                        "Family": fam_name,
                        "Type": type_name,
                        "Adjustment": fmt_delta
                    })
                else:
                    print_debug("ID {}: Failed to set parameter.".format(el.Id))
            else:
                print_debug("ID {}: No suitable writable parameter found.".format(el.Id))

        except Exception as e:
            print_debug("Error processing element {}: {}".format(el.Id, str(e)))
            
    t.Commit()
    
    if moved_elements_report:
        output.print_table(
            table_data=moved_elements_report,
            title="Summary of Adjusted Elements",
            columns=["Element ID", "Family", "Type", "Adjustment"],
            formats=["", "", "", ""]
        )
    
    print_debug("Finished. Modified {} elements.".format(modified_count))

if __name__ == "__main__":
    main()
