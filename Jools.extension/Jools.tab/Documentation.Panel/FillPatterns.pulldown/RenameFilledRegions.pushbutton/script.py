#! python3
__author__ = "Ryan Johnston"
__date__ = "2024-06-25"
__purpose__ = "To align fill patterns with filled regions to test printing and run quality checks."


import sys
import re
import clr
clr.AddReference('RevitAPI')
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import TaskDialog

import System.Windows.Forms as WinForms
from System.Drawing import Size, Point, Font
from System.Windows.Forms import Form, Label, TextBox, Button, CheckBox, DialogResult, MessageBox, MessageBoxButtons, MessageBoxIcon

# Get the current document
doc = __revit__.ActiveUIDocument.Document # type: ignore


def eid_int(element_id):
    """///Summary: An ElementId's integer value, valid in Revit 2022-2026.

    Autodesk added ElementId.Value in 2024 and removed ElementId.IntegerValue
    in 2026, so neither attribute alone covers every Revit this extension is
    attached to. Written for both CPython 3 and IronPython 2.7.
    """
    value = getattr(element_id, "Value", None)
    return int(value) if value is not None else element_id.IntegerValue



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

    for fpe in fill_pattern_elements:
        fill_pattern_id     = eid_int(fpe.Id)
        fill_pattern_name   = fpe.Name
        fill_pattern_target = fpe.GetFillPattern().Target
        fill_pattern_type   = fpe

        if fill_pattern_target == 0: 
            fill_pattern_target = "Drafting"
        elif fill_pattern_target == 1:
            fill_pattern_target = "Model"


        fill_pattern_dict[fill_pattern_id] = {
            "id"     : fill_pattern_id,
            "name"   : fill_pattern_name,
            "element": fill_pattern_type,
            "target" : fill_pattern_target
            }

    print(f"\tCOLLECTED {len(fill_pattern_elements)} FillPatternElement ELEMENTS")

    return fill_pattern_dict

#-----------------------------------------------------------------------------------------------------------------------------------

def get_filled_region_types(doc, fill_pattern_dict):
    # Collect all filled region types in the document.
    filled_region_types = FilteredElementCollector(doc).OfClass(FilledRegionType).ToElements()

    if not filled_region_types:
        raise ValueError("No FillRegionType elements found in the document.")

    filled_region_dict = {}

    for frt in filled_region_types:

        fg_fill_pattern_id     = eid_int(frt.ForegroundPatternId)
        fg_fill_pattern_name   = fill_pattern_dict.get(fg_fill_pattern_id, {}).get("name", None)
        fg_fill_pattern_target = fill_pattern_dict.get(fg_fill_pattern_id, {}).get("target", None)

        if fg_fill_pattern_name is None:
            print(f"ForegroundPatternID '{fg_fill_pattern_id}' - '{fg_fill_pattern_name}' not found in Fill Pattern Dictionary")
            continue

        filled_region_dict[frt.Id] = {
            "name"                        : frt.get_Name(),
            "id"                          : eid_int(frt.Id),
            "element"                     : frt,
            "foreground_fill_pattern_name": fg_fill_pattern_name,
            "foreground_fill_pattern_id"  : fg_fill_pattern_id,
            "color"                       : frt.ForegroundPatternColor,
            "target"                      : fg_fill_pattern_target,
            "line_weight"                 : frt.LineWeight,
            "masking"                     : "Masking" if frt.IsMasking else "NonMasking"
        }

    print(f"\tCOLLECTED {len(filled_region_types)} FilledRegionType ELEMENTS")

    return filled_region_types, filled_region_dict

#-----------------------------------------------------------------------------------------------------------------------------------

COLOR_NAMES = {
    (0, 0, 0)      : "Black",
    (255, 255, 255): "White",
    (255, 0, 0)    : "Red",
    (0, 255, 0)    : "Green",
    (0, 0, 255)    : "Blue",
    (255, 255, 0)  : "Yellow",
    (0, 255, 255)  : "Cyan",
    (255, 0, 255)  : "Magenta",
    (192, 192, 192): "Silver",
    (128, 128, 128): "Gray",
    (128, 0, 0)    : "Maroon",
    (128, 128, 0)  : "Olive",
    (0, 128, 0)    : "DarkGreen",
    (128, 0, 128)  : "Purple",
    (0, 128, 128)  : "Teal",
    (0, 0, 128)    : "Navy"
}


# =============================================================
# User Inputs
# =============================================================

    # need to get inputs for naming convention application

class UserInputForm(Form):
    def __init__(self):
        self.Text = "Fill Pattern and Filled Region Productivity Suite"
        self.ClientSize = Size(600, 300)
        self.FormBorderStyle = WinForms.FormBorderStyle.FixedDialog
        self.MaximizeBox = False
        self.StartPosition = WinForms.FormStartPosition.CenterScreen

        # Checkbox for Rename Filled Regions
        self.chk_rename = CheckBox()
        self.chk_rename.Text = "Rename Filled Regions"
        self.chk_rename.Location = Point(20, 20)
        self.chk_rename.AutoSize = True
        self.chk_rename.Checked = True
        self.Controls.Add(self.chk_rename)

        # Naming Convention Input
        self.lbl_naming_convention = Label()
        self.lbl_naming_convention.Text = "Filled Region Naming Convention: (example below)"
        self.lbl_naming_convention.Location = Point(20, 60)
        self.lbl_naming_convention.Size = Size(560, 20)
        self.Controls.Add(self.lbl_naming_convention)

        self.txt_naming_convention = TextBox()
        self.txt_naming_convention.Text = "{fill_pattern_name}__{line_weight}_{color_name}_{masking}"
        self.txt_naming_convention.Location = Point(20, 90)
        self.txt_naming_convention.Size = Size(560, 30)
        self.Controls.Add(self.txt_naming_convention)

        # Variables Information Label
        self.lbl_variables_info = Label()
        self.lbl_variables_info.Text = (
            "Available Variables:\n"
            "*{fill_pattern_name}*\n"
            "*{color}*\n"
            "*{color_name}*\n"
            "*{line_weight}*\n"
            "*{masking}*\n"
            "*{target} (drafting or model pattern)*"
        )
        self.lbl_variables_info.Location = Point(20, 130)
        self.lbl_variables_info.Size = Size(560, 100)
        self.lbl_variables_info.Font = Font("Arial", 10.0, 2)
        self.Controls.Add(self.lbl_variables_info)

        # Submit Button
        self.btn_submit = Button()
        self.btn_submit.Text = "Submit"
        self.btn_submit.Location = Point(240, 240)
        self.btn_submit.Size = Size(120, 30)
        self.btn_submit.Click += self.submit
        self.Controls.Add(self.btn_submit)
        # Results
        self.result = None

    def submit(self, sender, args):
        self.result = {
            "rename_filled_region_types"                  : self.chk_rename.Checked,
            "naming_convention"                           : self.txt_naming_convention.Text
        }
        self.DialogResult = DialogResult.OK
        self.Close()


# =============================================================
# Translations
# =============================================================

# Function to turn the Fill Pattern's color's RGB Value into words by applying the RGB Value to the Color Names above
def rgb_to_name(color):
    # Get the RGB values as a tuple
    rgb_tuple = (color.Red, color.Green, color.Blue)
    # Return the color name if found, otherwise return the hex color code
    return COLOR_NAMES.get(rgb_tuple, f"{color.Red:02X}{color.Green:02X}{color.Blue:02X}")

#-----------------------------------------------------------------------------------------------------------------------------------

# Remove special characters from the fill patterns so we can use them in naming our filled regions
def sanitize_name(name):
    # Remove special characters from the name
    return re.sub(r'[\|{}<>?;:^%$@!&*=+[\]~]', '', name.title())

#-----------------------------------------------------------------------------------------------------------------------------------

# Build the naming convention for all Filled Regions
def format_name(frt_info, naming_convention):
    fill_pattern_name = sanitize_name(frt_info["foreground_fill_pattern_name"])
    color             = frt_info["color"]
    color_name        = rgb_to_name(color)
    line_weight       = str(frt_info["line_weight"])
    masking           = frt_info["masking"]
    target            = frt_info["target"]

    return naming_convention.format(
        fill_pattern_name = fill_pattern_name,
        color_name        = color_name,
        line_weight       = line_weight,
        masking           = masking,
        target            = target
    )


# Rename filled region types based the def(format_name) function
def rename_filled_region_types(doc, filled_region_dict, filled_region_types, naming_convention):
    errors = []
    names_changed = 0
    names_unchanged = 0
    
    t = Transaction(doc, "Rename Filled Regions")
    t.Start()
    
    try:
        # Iterate over each filled region type in the dictionary
        for frt_id, frt_info in filled_region_dict.items(): # for all filled region id's, get the related information that's in the frt dictionary
            properly_formatted_name = format_name(frt_info, naming_convention) # set up what a properly_formatted_name looks like

            existing_frt_with_new_name = next((frt for frt in filled_region_types if frt.get_Name() == properly_formatted_name), None)
            if existing_frt_with_new_name:
                names_unchanged += 1
                continue

            old_name = frt_info['name']          
            
            if old_name != properly_formatted_name: # Check if the current name matches the formatted name
                fr_element = doc.GetElement(frt_id)
                fr_element.Name = properly_formatted_name # Rename the filled region type
                
                #Update the filled region dictionary with the properly_formatted_name
                filled_region_dict[frt_id] = {"name": properly_formatted_name,}
                names_changed += 1
                print(f"\t\tRenamed: {old_name} --to-- {properly_formatted_name}")

        t.Commit()
        print(f"\tRENAMING SUCCESS: {names_changed} filled region types we renamed successfully.")
        print(f"\tRENAMING SKIPPED: {names_unchanged} filled regions were skipped because the formatted name already exists.")

    except Exception as e:
        t.RollBack()
        errors.append(f"\t\tFailed: {str(e)}")

    if errors:
        print("\tErrors:")
        for error in errors:
            print(error)
#-----------------------------------------------------------------------------------------------------------------------------------


# =============================================================
# Script Execution
# =============================================================

# Main function to execute the workflow
def main(doc):
    form = UserInputForm()

    if form.ShowDialog() == DialogResult.OK:
        user_input = form.result
        print("===================\nGetting Revit Elements & Building Dictionaries...\n===================\n\n")

        print("\n\nCOLLECTING FILL PATTERN ELEMENTS...")
        fill_pattern_dict = get_fill_pattern_elements(doc)
    
        print("\n\nCOLLECTING FILLED REGION TYPES...")
        filled_region_types, filled_region_dict = get_filled_region_types(doc, fill_pattern_dict)

        # Retrieve the ID using the selected name
        naming_convention = str(user_input["naming_convention"])
        try:
            if user_input["rename_filled_region_types"]: 
                print("\nRenaming Filled Regions...")
                rename_filled_region_types(doc, filled_region_dict, filled_region_types, naming_convention)
                
            MessageBox.Show("Success")

        except Exception as e:
            MessageBox.Show(f"Script Failed. An error occurred:\n{e}") 

    else:
        MessageBox.Show("Operation cancelled by user.", "Info", MessageBoxButtons.OK, MessageBoxIcon.Information)
        sys.exit()

main(doc)