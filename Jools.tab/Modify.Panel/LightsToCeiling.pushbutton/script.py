#! python3
# -*- coding: utf-8 -*-

__author__ = "Ryan Johnston"
__date__ = "2025-12-04"
__purpose__ = "Adjusts the height of selected elements to snap to the nearest ceiling in linked models."

import clr
import sys

# Standard Revit API
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
from Autodesk.Revit.DB import (
    FilteredElementCollector,
    BuiltInCategory,
    Transaction,
    ReferenceIntersector,
    FindReferenceTarget,
    XYZ,
    ElementCategoryFilter,
    ViewType,
    StorageType,
    ElementId
)
from Autodesk.Revit.UI import TaskDialog, Selection
from System.Collections.Generic import List

# Windows Forms (for UI in CPython)
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")
from System.Windows.Forms import (
    Application, Form, Label, TextBox, Button, 
    CheckBox, CheckedListBox, DialogResult, 
    MessageBox, MessageBoxButtons, MessageBoxIcon,
    FormBorderStyle, FormStartPosition, AnchorStyles,
    Padding, GroupBox, DockStyle, ProgressBar
)
from System.Drawing import Point, Size, Font, FontStyle

# pyRevit
from pyrevit import revit, script

# --- PATCHES ---
if not hasattr(sys.stdout, "flush"):
    sys.stdout.flush = lambda: None

# --- GLOBALS ---
output = script.get_output()
doc = revit.doc
uidoc = revit.uidoc

# --- HELPER CLASSES ---

class ConfigForm(Form):
    """A simple Windows Forms dialog for configuring the script."""
    def __init__(self, selected_count=0):
        super(ConfigForm, self).__init__()
        self.Text = "Snap to Ceiling Settings"
        self.Size = Size(400, 450)
        self.MinimumSize = Size(400, 450)
        self.StartPosition = FormStartPosition.CenterScreen
        self.FormBorderStyle = FormBorderStyle.FixedDialog
        self.MaximizeBox = False
        self.MinimizeBox = False
        
        self.selected_categories = []
        self.target_parameter = "Offset from Host"
        self.should_reselect = False

        # 1. Selection Status
        self.lbl_status = Label()
        self.lbl_status.Text = "Selected Elements: {}".format(selected_count)
        self.lbl_status.Location = Point(20, 20)
        self.lbl_status.AutoSize = True
        try:
            self.lbl_status.Font = Font("Microsoft Sans Serif", 8.25, FontStyle.Bold)
        except:
            pass # Fallback to default
        self.Controls.Add(self.lbl_status)

        self.btn_reselect = Button()
        self.btn_reselect.Text = "Select Objects"
        self.btn_reselect.Location = Point(250, 15)
        self.btn_reselect.Size = Size(110, 25)
        self.btn_reselect.Click += self.on_reselect_click
        self.Controls.Add(self.btn_reselect)

        # 2. Parameter Settings
        self.grp_param = GroupBox()
        self.grp_param.Text = "Target Parameter"
        self.grp_param.Location = Point(20, 60)
        self.grp_param.Size = Size(340, 60)
        self.Controls.Add(self.grp_param)

        self.txt_param = TextBox()
        self.txt_param.Text = self.target_parameter
        self.txt_param.Location = Point(15, 25)
        self.txt_param.Size = Size(310, 25)
        self.grp_param.Controls.Add(self.txt_param)

        # 3. Category Filter
        self.grp_cats = GroupBox()
        self.grp_cats.Text = "Filter by Category"
        self.grp_cats.Location = Point(20, 140)
        self.grp_cats.Size = Size(340, 180)
        self.Controls.Add(self.grp_cats)

        self.chk_list = CheckedListBox()
        self.chk_list.Location = Point(15, 25)
        self.chk_list.Size = Size(310, 140)
        self.chk_list.CheckOnClick = True
        # Default Categories
        defaults = [
            "Lighting Fixtures", 
            "Electrical Fixtures", 
            "Fire Alarm Devices", 
            "Generic Models",
            "Data Devices",
            "Communication Devices",
            "Security Devices"
        ]
        for cat in defaults:
            self.chk_list.Items.Add(cat, True)
        self.grp_cats.Controls.Add(self.chk_list)

        # 4. Action Buttons
        self.btn_run = Button()
        self.btn_run.Text = "Align to Ceiling"
        self.btn_run.Location = Point(20, 340)
        self.btn_run.Size = Size(340, 40)
        self.btn_run.Font = Font(self.Font, FontStyle.Bold)
        self.btn_run.Click += self.on_run_click
        self.btn_run.Enabled = selected_count > 0
        self.Controls.Add(self.btn_run)

    def on_reselect_click(self, sender, args):
        self.should_reselect = True
        self.Close()

    def on_run_click(self, sender, args):
        self.target_parameter = self.txt_param.Text
        # Get checked categories
        self.selected_categories = [
            self.chk_list.Items[i] 
            for i in range(self.chk_list.Items.Count) 
            if self.chk_list.GetItemChecked(i)
        ]
        self.DialogResult = DialogResult.OK
        self.Close()

class ProgressBarForm(Form):
    """A simple custom progress bar form."""
    def __init__(self, max_value, title="Processing..."):
        super(ProgressBarForm, self).__init__()
        self.Text = title
        self.Width = 400
        self.Height = 120
        self.FormBorderStyle = FormBorderStyle.FixedToolWindow
        self.StartPosition = FormStartPosition.CenterScreen
        self.ControlBox = False # Hide close button
        
        self.lbl = Label()
        self.lbl.Location = Point(10, 15)
        self.lbl.AutoSize = True
        self.Controls.Add(self.lbl)
        
        self.pb = ProgressBar()
        self.pb.Location = Point(10, 40)
        self.pb.Width = 360
        self.pb.Height = 25
        self.pb.Maximum = max_value
        self.Controls.Add(self.pb)
        
        self.Show()
        self.Update()

    def update_progress(self, value, msg):
        if value <= self.pb.Maximum:
            self.pb.Value = value
        self.lbl.Text = msg
        self.lbl.Update()
        Application.DoEvents()

# --- HELPER FUNCTIONS ---

def get_parameter_value(param):
    if not param: return 0.0
    if param.StorageType == StorageType.Double: return param.AsDouble()
    elif param.StorageType == StorageType.Integer: return param.AsInteger()
    return 0.0

def set_parameter_value(param, value):
    if not param or param.IsReadOnly: return False
    if param.StorageType == StorageType.Double: return param.Set(value)
    elif param.StorageType == StorageType.Integer: return param.Set(int(value))
    return False

def get_category_name(element):
    if hasattr(element, "Category") and element.Category:
        return element.Category.Name
    return "Unknown"

def get_ceiling_element_from_ref(doc, ref):
    if ref.LinkedElementId != ElementId.InvalidElementId:
        link_instance = doc.GetElement(ref.ElementId)
        if link_instance:
            link_doc = link_instance.GetLinkDocument()
            if link_doc:
                return link_doc.GetElement(ref.LinkedElementId)
    else:
        return doc.GetElement(ref.ElementId)
    return None

def get_element_thickness(element):
    if not element: return 0.0
    try:
        # Use the element's document (could be a linked doc)
        doc = element.Document 
        el_type = doc.GetElement(element.GetTypeId())
        if hasattr(el_type, "GetCompoundStructure"):
            cs = el_type.GetCompoundStructure()
            if cs: return cs.GetWidth()
    except:
        pass
    return 0.0

# --- MAIN LOGIC ---

def main():
    active_view = doc.ActiveView
    
    if active_view.ViewType != ViewType.ThreeD:
        TaskDialog.Show("Error", "Please switch to a 3D View.")
        return

    # 1. UI Loop
    try:
        selection = uidoc.Selection.GetElementIds()
        if selection:
            current_ids = [id for id in selection]
        else:
            current_ids = []
    except Exception as e:
        output.print_md(u"**ERROR:** Error getting element IDs: {}".format(e))
        current_ids = []

    target_param_name = "Offset from Host"
    target_categories = []
    
    while True:
        # Show Form
        try:
            form = ConfigForm(len(current_ids))
            # Restore previous text if loop
            form.txt_param.Text = target_param_name
            
            result = form.ShowDialog()
        except Exception as e:
            output.print_md(u"**ERROR:** Error showing form: {}".format(e))
            return
        
        if form.should_reselect:
            try:
                # Prompt user to pick objects
                picked_refs = uidoc.Selection.PickObjects(Selection.ObjectType.Element, "Select elements to align")
                current_ids = [r.ElementId for r in picked_refs]
                
                # Convert to .NET List for SetElementIds
                element_id_list = List[ElementId](current_ids)
                uidoc.Selection.SetElementIds(element_id_list) # Sync with Revit UI
            except Exception as e:
                output.print_md(u"**ERROR:** Error during reselection: {}".format(e))
                # User cancelled pick
                pass
            continue # Loop back to show form with new count
            
        elif result == DialogResult.OK:
            target_param_name = form.target_parameter
            target_categories = form.selected_categories
            break # Proceed to script
        else:
            return # Cancel script

    # 2. Filter Selection
    elements_to_process = []
    for eid in current_ids:
        el = doc.GetElement(eid)
        if get_category_name(el) in target_categories:
            elements_to_process.append(el)
            
    if not elements_to_process:
        TaskDialog.Show("Info", "No elements matched the selected categories.")
        return

    # 3. Setup Intersector
    intersector = ReferenceIntersector(
        ElementCategoryFilter(BuiltInCategory.OST_Ceilings), 
        FindReferenceTarget.All, 
        active_view
    )
    intersector.FindReferencesInRevitLinks = True

    # 4. Processing
    t = Transaction(doc, "Snap Elements to Ceiling")
    t.Start()
    
    modified_count = 0
    moved_elements_report = []
    total = len(elements_to_process)
    
    # Init Custom Progress Bar
    pb_form = None
    try:
        pb_form = ProgressBarForm(total, "Snapping to Ceiling...")
    except Exception as e:
        output.print_md(u"**WARNING:** Could not create progress bar: {}".format(e))

    for i, el in enumerate(elements_to_process):
        if pb_form:
            pb_form.update_progress(i, "Checking ID {}".format(el.Id))
        
        try:
            if not hasattr(el.Location, "Point"): continue
            
            location_pt = el.Location.Point
            
            # Cast Rays
            ref_up = intersector.FindNearest(location_pt, XYZ.BasisZ)
            ref_down = intersector.FindNearest(location_pt, XYZ.BasisZ.Negate())
            
            target_pt = None
            dist_up = float('inf')
            dist_down = float('inf')

            if ref_up:
                dist_up = ref_up.GetReference().GlobalPoint.DistanceTo(location_pt)
            if ref_down:
                dist_down = ref_down.GetReference().GlobalPoint.DistanceTo(location_pt)

            if dist_up < dist_down:
                target_pt = ref_up.GetReference().GlobalPoint
            elif dist_down < dist_up:
                target_pt = ref_down.GetReference().GlobalPoint
                
                # Adjust for ceiling thickness if hitting the top
                try:
                    hit_ref = ref_down.GetReference()
                    hit_el = get_ceiling_element_from_ref(doc, hit_ref)
                    thickness = get_element_thickness(hit_el)
                    target_pt = XYZ(target_pt.X, target_pt.Y, target_pt.Z - thickness)
                except Exception:
                    # Fallback to top face if error
                    pass
            
            if not target_pt: continue

            # Calc Delta
            z_delta = target_pt.Z - location_pt.Z
            
            # Param Logic
            p = el.LookupParameter(target_param_name)
            if p and not p.IsReadOnly:
                # Reset Mounting Height if applicable
                if target_param_name == "Offset from Host":
                    mh = el.LookupParameter("Mounting Height")
                    if mh and not mh.IsReadOnly: set_parameter_value(mh, 0)
                
                current_val = get_parameter_value(p)
                new_val = current_val + z_delta
                
                if set_parameter_value(p, new_val):
                    modified_count += 1
                    
                    # Report Data
                    try:
                        el_type = doc.GetElement(el.GetTypeId())
                        fam = el_type.FamilyName if el_type else "?"
                        typ = el_type.Name if el_type else "?"
                    except:
                        fam, typ = "?", "?"
                    
                    # Store as list for robust table printing
                    moved_elements_report.append([
                        str(el.Id.IntegerValue),
                        get_category_name(el),
                        fam,
                        typ,
                        "{:.2f}".format(z_delta)
                    ])

        except Exception as e:
            output.print_md(u"**ERROR:** Error processing element {}: {}".format(el.Id, e))

    t.Commit()
    
    if pb_form:
        pb_form.Close()
    
    # 5. Final Report
    if moved_elements_report:
        output.print_table(
            table_data=moved_elements_report,
            title="Summary of Adjusted Elements",
            columns=["Element ID", "Category", "Family", "Type", "Adjustment"]
        )
    else:
        output.print_md("No elements were moved.")

    output.print_md("Finished. Modified {} elements.".format(modified_count))

if __name__ == "__main__":
    main()