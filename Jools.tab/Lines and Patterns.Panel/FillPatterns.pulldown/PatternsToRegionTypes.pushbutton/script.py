# !python3
__author__ = "Ryan Johnston"
__date__ = "2024-06-25"
__purpose__ = "To create filled region types from fill patterns in the model"


import clr
import re
import os, sys, math, datetime, time, logging
clr.AddReference('ProtoGeometry')
clr.AddReference('RevitAPI')
clr.AddReference('RevitServices')
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
from Autodesk.DesignScript.Geometry import * # type: ignore
from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import TaskDialog
from RevitServices.Persistence import DocumentManager # type: ignore
from RevitServices.Transactions import TransactionManager # type: ignore
import System # type: ignore
from System.Collections.Generic import List # type: ignore
from System.Drawing import Size, Point, Font # type: ignore
from System.Windows.Forms import Form, Label, MessageBox, MessageBoxButtons, MessageBoxIcon, TextBox, Button, DialogResult, ComboBox, CheckBox, FormStartPosition, ListBox, CheckedListBox, Panel, ScrollBars, SelectionMode, CheckState # type: ignore

# Get the current document
doc = __revit__.ActiveUIDocument.Document # type: ignore

if doc is None:
    TaskDialog.Show("Error", "No active document found. Open a Revit file and try again")
    exit()

# =============================================================
# Getting Revit Elements and Building Dictionaries for Reference
# =============================================================

#-----------------------------------------------------------------------------------------------------------------------------------

def get_fill_pattern_elements(doc):
    # Collect all fill pattern elements in the document
    fill_pattern_elements = FilteredElementCollector(doc).OfClass(FillPatternElement).ToElements()

    if not fill_pattern_elements:
        raise ValueError("No FillPatternElement elements found in the document.")

    fill_pattern_dict = {}
    fill_pattern_name_list = []

    for fpe in fill_pattern_elements:
        fill_pattern_id = fpe.Id.IntegerValue
        fill_pattern_name = fpe.Name
        fill_pattern_type = fpe

        fill_pattern_dict[fill_pattern_id] = {
            "id" : fill_pattern_id,
            "name" : fill_pattern_name,
            "element" : fill_pattern_type
            }
        fill_pattern_name_list.append(fill_pattern_name)

    return fill_pattern_elements, fill_pattern_dict, fill_pattern_name_list

#-----------------------------------------------------------------------------------------------------------------------------------

def get_filled_region_types(doc, fill_pattern_dict):
    # Collect all filled region types in the document.
    filled_region_types = FilteredElementCollector(doc).OfClass(FilledRegionType).ToElements()

    if not filled_region_types:
        raise ValueError("No FillRegionType elements found in the document.")

    filled_region_dict = {}

    for frt in filled_region_types:

        fg_fill_pattern_id   = frt.ForegroundPatternId.IntegerValue
        fg_fill_pattern_name = fill_pattern_dict.get(fg_fill_pattern_id, {}).get("name", None)

        if fg_fill_pattern_name is None:
            print(f"ForegroundPatternID '{fg_fill_pattern_id}' - '{fg_fill_pattern_name}' not found in Fill Pattern Dictionary")
            continue

        filled_region_dict[frt.Id] = {
            "name"                        : frt.get_Name(),
            "id"                          : frt.Id.IntegerValue,
            "element"                     : frt,
            "foreground_fill_pattern_name": fg_fill_pattern_name,
            "foreground_fill_pattern_id"  : fg_fill_pattern_id,
            "color"                       : frt.ForegroundPatternColor,
            "line_weight"                 : frt.LineWeight,
            "masking"                     : "Masking" if frt.IsMasking else "NonMasking"
        }
    return filled_region_dict


# =============================================================
# User Inputs
# =============================================================

# design user interface that allows users to select which fill patterns they want to turn into filled regions
# have the option to select all, select all model patterns, select all drafting patterns
# have the option to rename the filled regions as you want to


class UserInputForm(Form):
    def __init__(self, fill_pattern_name_list):
        super().__init__()
        self.fill_pattern_name_list = sorted(fill_pattern_name_list)
        self.initialize_components()

    def initialize_components(self):
        # Form properties
        self.Text = "Select which Fill Patterns to make into Filled Region Types"
        self.Width = 500
        self.Height = 640
        self.StartPosition = FormStartPosition.CenterScreen

        # Panel to hold list and ensure it's scrollable
        self.panel = Panel()  #MODIFIED
        self.panel.Size = Size(460, 500)  #MODIFIED
        self.panel.Location = Point(10, 50)  #MODIFIED
        self.panel.AutoScroll = True  #MODIFIED

        # List box for fill patterns
        self.list_fill_patterns = CheckedListBox()
        self.list_fill_patterns.Dock = System.Windows.Forms.DockStyle.Fill
        self.list_fill_patterns.CheckOnClick = True
        self.list_fill_patterns.SelectionMode = SelectionMode.One
        self.list_fill_patterns.ScrollAlwaysVisible = True

        # Load fill patterns into the list
        for pattern_name in self.fill_pattern_name_list:
            self.list_fill_patterns.Items.Add(pattern_name)
        
        self.panel.Controls.Add(self.list_fill_patterns)
        self.Controls.Add(self.panel)

        # Checkbox for Select All
        self.chk_select_all = CheckBox()
        self.chk_select_all.Text = "Select All Fill Patterns"
        self.chk_select_all.Location = Point(10, 20)  #MODIFIED
        self.chk_select_all.AutoSize = True
        self.chk_select_all.CheckedChanged += self.select_all_fill_patterns  #MODIFIED
        
        self.Controls.Add(self.chk_select_all)

        # Submit Button
        self.btn_submit = Button()
        self.btn_submit.Text = "Generate Fill Region Types"
        self.btn_submit.Location = Point(10, 560)
        self.btn_submit.AutoSize = True
        self.btn_submit.Click += self.button_click
        self.Controls.Add(self.btn_submit)

    def select_all_fill_patterns(self, sender, args):
        # Toggle all items based on the state of the 'Select All' checkbox
        check_state = self.chk_select_all.Checked
        for i in range(self.list_fill_patterns.Items.Count):
            self.list_fill_patterns.SetItemChecked(i, check_state)

    def button_click(self, sender, event):
        selected_patterns = [self.list_fill_patterns.Items[i] for i in range(self.list_fill_patterns.Items.Count) if self.list_fill_patterns.GetItemCheckState(i) == CheckState.Checked]
        MessageBox.Show("Selected Patterns: " + ", ".join(selected_patterns))
        self.Close()


# =============================================================
# Functions
# =============================================================

# Duplicate and assign new fill region types for fill patterns without corresponding filled region types.
def create_filled_region_types_for_fill_patterns(doc, fill_pattern_elements, filled_region_dict, selected_patterns=None):
    if selected_patterns is not None:
        fill_pattern_elements = [fpe for fpe in fill_pattern_elements if fpe.Name in selected_patterns]
    
    errors = []
    new_filled_region_count = 0
    new_filled_region_list = []

    t = Transaction(doc, "Duplicate and Assign Fill Region Types")
    t.Start()

    try:
        existing_fg_pattern_ids = {fr_dict['foreground_fill_pattern_id'] for fr_dict in filled_region_dict.values()} # get all of the foreground pattern id's in the project's current filled regions

        for fill_pattern in fill_pattern_elements:
            if fill_pattern.Id not in existing_fg_pattern_ids: #if a fill pattern is not in a filled region, we need to make one.

                first_filled_region = FilteredElementCollector(doc).OfClass(FilledRegionType).FirstElement() # get any filled region type to prep for duplication
                
                fp_name_clean = str(re.sub(r'[\|{}<>?;:^%$@!&*=+[\]~]', "", fill_pattern.Name.title()))
                
                # Insert Naming convention option into the new_region function. 
                new_region = first_filled_region.Duplicate(fp_name_clean) # have to duplicate the filled region. Setting filled region name to fill pattern name.
                
                # Get user input for new region options and use them to replace new_region properties below

                # Set properties for the new filled region
                new_region.BackgroundPatternId    = ElementId(-1) # ??
                new_region.ForegroundPatternId    = fill_pattern.Id
                new_region.BackgroundPatternColor = Color(0,0,0)
                new_region.ForegroundPatternColor = Color(0,0,0)
                new_region.IsMasking              = False
                new_region.LineWeight             = 1
                
                # Update the dictionary with the new values
                filled_region_dict[new_region.Id] = {
                    "name"                        : new_region.get_Name(),
                    "id"                          : new_region.Id,
                    "foreground_fill_pattern_name": fill_pattern.Name,
                    "foreground_fill_pattern_id"  : fill_pattern.Id,
                    "color"                       : new_region.ForegroundPatternColor,
                    "masking"                     : "Masking" if new_region.IsMasking else "NonMasking",
                    "line_weight"                 : new_region.LineWeight
                }
                
                new_filled_region_count += 1
                new_filled_region_list.append(fp_name_clean)
        t.Commit()

    except Exception as e:  
        t.RollBack()
        errors.append(f"\t\tError during duplication: {e}")

    # Change error handling to continue creating filled regions for valid entries 
    # and pass the creations that have errors to a report that displays at the end 

    if errors:
        for error in errors:
            MessageBox.Show(f"Errors:\n{error}",MessageBoxButtons.OK, MessageBoxIcon.Information)
            sys.exit()
    else:
        MessageBox.Show(f"CREATED {new_filled_region_count}\nNEW FILLED REGION TYPES: \n{new_filled_region_list}", "Info", MessageBoxButtons.OK, MessageBoxIcon.Information)


# =============================================================
# Script Execution
# =============================================================

# Main function to execute the workflow
def main(doc):
    fill_pattern_elements, fill_pattern_dict, fill_pattern_name_list = get_fill_pattern_elements(doc)

    filled_region_dict = get_filled_region_types(doc, fill_pattern_dict)

    form = UserInputForm(fill_pattern_name_list)

    if form.ShowDialog() == DialogResult.OK:
        user_input = form.result
        try:
            selected_patterns = user_input["selected_fill_patterns"]
            create_filled_region_types_for_fill_patterns(doc, fill_pattern_elements, filled_region_dict, selected_patterns)

        except Exception as e:
            MessageBox.Show(f"Script Failed. An error occurred:\n{e}") 

    else:
        MessageBox.Show("Operation cancelled by user.", "Cancelled", MessageBoxButtons.OK, MessageBoxIcon.Information)
        sys.exit()

main(doc)