#! python3
__author__ = "Ryan Johnston"
__date__ = "2024-06-25"
__purpose__ = "To align fill patterns with filled regions to test printing and run quality checks."


import clr
import re
import sys
clr.AddReference('RevitAPI')
clr.AddReference('System.Windows.Forms')
from Autodesk.DesignScript.Geometry import * # type: ignore
from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import TaskDialog
from System.Collections.Generic import List
from System.Windows.Forms import Form, Label, MessageBox, MessageBoxButtons, MessageBoxIcon, TextBox, Button, DialogResult, ComboBox, CheckBox

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


def get_text_note_types(doc):
    # Using OfClass to collect only TextNoteTypes
    text_note_types = FilteredElementCollector(doc).OfClass(TextNoteType).ToElements()

    if not text_note_types:
        raise ValueError("No TextNoteType elements found in the document.")

    text_note_dict = {}

    for tnt in text_note_types:
        text_name = tnt.get_Name()
        text_id = tnt.Id
        text_type = tnt

        text_note_dict[text_id] = {
            "name" : text_name,
            "element" : text_type
        }

    print(f"\tCOLLECTED {len(text_note_types)} TextNoteType ELEMENTS")

    return text_note_types, text_note_dict

#-----------------------------------------------------------------------------------------------------------------------------------

def get_fill_pattern_elements(doc):
    # Collect all fill pattern elements in the document
    fill_pattern_elements = FilteredElementCollector(doc).OfClass(FillPatternElement).ToElements()

    if not fill_pattern_elements:
        raise ValueError("No FillPatternElement elements found in the document.")

    fill_pattern_dict = {}

    for fpe in fill_pattern_elements:
        fill_pattern_id = eid_int(fpe.Id)
        fill_pattern_name = fpe.Name
        fill_pattern_type = fpe

        fill_pattern_dict[fill_pattern_id] = {
            "id" : fill_pattern_id,
            "name" : fill_pattern_name,
            "element" : fill_pattern_type
            }

    print(f"\tCOLLECTED {len(fill_pattern_elements)} FillPatternElement ELEMENTS")

    return fill_pattern_elements, fill_pattern_dict

#-----------------------------------------------------------------------------------------------------------------------------------

def get_filled_region_types(doc, fill_pattern_dict):
    # Collect all filled region types in the document.
    filled_region_types = FilteredElementCollector(doc).OfClass(FilledRegionType).ToElements()

    if not filled_region_types:
        raise ValueError("No FillRegionType elements found in the document.")

    filled_region_dict = {}

    for frt in filled_region_types:

        fg_fill_pattern_id   = eid_int(frt.ForegroundPatternId)
        fg_fill_pattern_name = fill_pattern_dict.get(fg_fill_pattern_id, {}).get("name", None)

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
            "line_weight"                 : frt.LineWeight,
            "masking"                     : "Masking" if frt.IsMasking else "NonMasking"
        }

    print(f"\tCOLLECTED {len(filled_region_types)} FilledRegionType ELEMENTS")

    return filled_region_dict

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
    def __init__(self, text_type_names):
        self.Text   = "Fill Pattern and Filled Region Productivity Suite"
        self.Width  = 260
        self.Height = 480

                # Checkbox for Duplicate and Assign Filled Region Types
        self.chk_duplicate          = CheckBox()
        self.chk_duplicate.Text     = "Duplicate and Assign Filled Region Types"
        self.chk_duplicate.Top      = 20
        self.chk_duplicate.Left     = 10
        self.chk_duplicate.AutoSize = True
        self.chk_duplicate.Checked  = True
        self.Controls.Add(self.chk_duplicate)

        # Checkbox for Rename Filled Regions
        self.chk_rename          = CheckBox()
        self.chk_rename.Text     = "Rename Filled Regions"
        self.chk_rename.Top      = 40
        self.chk_rename.Left     = 10
        self.chk_rename.AutoSize = True
        self.chk_rename.Checked  = True
        self.Controls.Add(self.chk_rename)

        # Checkbox for Create Filled Regions with Annotations
        self.chk_create_filled          = CheckBox()
        self.chk_create_filled.Text     = "Create Filled Regions with Annotations"
        self.chk_create_filled.Top      = 60
        self.chk_create_filled.Left     = 10
        self.chk_create_filled.AutoSize = True
        self.chk_create_filled.Checked  = True
        self.Controls.Add(self.chk_create_filled)


        # Text Type Dropdown
        self.lbl_text_type       = Label()
        self.lbl_text_type.Text  = "Select Text Type for Annotations:"
        self.lbl_text_type.Top   = 100
        self.lbl_text_type.Left  = 20
        self.lbl_text_type.Width = 230
        self.Controls.Add(self.lbl_text_type)

        self.cmb_text_type       = ComboBox()
        self.cmb_text_type.Top   = 125
        self.cmb_text_type.Left  = 20
        self.cmb_text_type.Width = 210
        sorted_text_type_names   = sorted(text_type_names)
        for name in sorted_text_type_names: 
            self.cmb_text_type.Items.Add(name)
        self.cmb_text_type.SelectedIndex = 0
        self.Controls.Add(self.cmb_text_type)

        # Filled Region Size Input
        self.lbl_fr_size       = Label()
        self.lbl_fr_size.Text  = "Filled Region Size (W X H):"
        self.lbl_fr_size.Top   = 160
        self.lbl_fr_size.Left  = 20
        self.lbl_fr_size.Width = 145
        self.Controls.Add(self.lbl_fr_size)

        self.user_input_txt_fr_W       = TextBox()
        self.user_input_txt_fr_W.Text  = "10"
        self.user_input_txt_fr_W.Top   = 157
        self.user_input_txt_fr_W.Left  = 170
        self.user_input_txt_fr_W.Width = 20
        self.Controls.Add(self.user_input_txt_fr_W)
    
        self.lbl_fr_X       = Label()
        self.lbl_fr_X.Text  = "X"
        self.lbl_fr_X.Top   = 160
        self.lbl_fr_X.Left  = 195
        self.lbl_fr_X.Width = 10
        self.Controls.Add(self.lbl_fr_X)

        self.user_input_txt_fr_H       = TextBox()
        self.user_input_txt_fr_H.Text  = "10"
        self.user_input_txt_fr_H.Top   = 157
        self.user_input_txt_fr_H.Left  = 210
        self.user_input_txt_fr_H.Width = 20
        self.Controls.Add(self.user_input_txt_fr_H)

        # user_input_fr_spacing Input
        self.lbl_user_input_fr_spacing       = Label()
        self.lbl_user_input_fr_spacing.Text  = "Spacing Between Filled Regions:"
        self.lbl_user_input_fr_spacing.Top   = 190
        self.lbl_user_input_fr_spacing.Left  = 20
        self.lbl_user_input_fr_spacing.Width = 175
        self.Controls.Add(self.lbl_user_input_fr_spacing)

        self.txt_user_input_fr_spacing       = TextBox()
        self.txt_user_input_fr_spacing.Text  = "5"
        self.txt_user_input_fr_spacing.Top   = 187
        self.txt_user_input_fr_spacing.Left  = 200
        self.txt_user_input_fr_spacing.Width = 30
        self.Controls.Add(self.txt_user_input_fr_spacing)

        # Max Per Row Input
        self.lbl_max_per_row       = Label()
        self.lbl_max_per_row.Text  = "# of Filled Regions Per Row:"
        self.lbl_max_per_row.Top   = 220
        self.lbl_max_per_row.Left  = 20
        self.lbl_max_per_row.Width = 175
        self.Controls.Add(self.lbl_max_per_row)

        self.txt_max_per_row       = TextBox()
        self.txt_max_per_row.Text  = "10"
        self.txt_max_per_row.Top   = 217
        self.txt_max_per_row.Left  = 200
        self.txt_max_per_row.Width = 30
        self.Controls.Add(self.txt_max_per_row)

        # Naming Convention Input
        self.lbl_naming_convention       = Label()
        self.lbl_naming_convention.Text  = "Filled Region Naming Convention: (example below)"
        self.lbl_naming_convention.Top   = 250
        self.lbl_naming_convention.Left  = 20
        self.lbl_naming_convention.Width = 175
        self.Controls.Add(self.lbl_naming_convention)

        self.txt_naming_convention       = TextBox()
        self.txt_naming_convention.Text  = "{fill_pattern_name}__{line_weight}_{color_name}_{masking}"
        self.txt_naming_convention.Top   = 270
        self.txt_naming_convention.Left  = 20
        self.txt_naming_convention.Width = 210
        self.Controls.Add(self.txt_naming_convention)

        # Variables Information Label
        self.lbl_variables_info       = Label()
        self.lbl_variables_info.Text  = "Available Variables:\nfill_pattern_name\n{color}\ncolor_name\nline_weight\nmasking"
        self.lbl_variables_info.Top   = 310
        self.lbl_variables_info.Left  = 20
        self.lbl_variables_info.Width = 210
        self.Controls.Add(self.lbl_variables_info)


        self.chk_create_text_notes = CheckBox()
        self.chk_create_text_notes.Text = "Create lables under newly created Filled Regions"
        self.chk_create_text_notes.Top = 370
        self.chk_create_text_notes.Left = 10
        self.chk_create_text_notes.AutoSize = True
        self.chk_create_text_notes.Checked = True
        self.Controls.Add(self.chk_create_text_notes)

        # Submit Button
        self.btn_submit       = Button()
        self.btn_submit.Text  = "Submit"
        self.btn_submit.Top   = 400
        self.btn_submit.Left  = 130
        self.btn_submit.Width = 100
        self.btn_submit.Click += self.submit
        self.Controls.Add(self.btn_submit)

        # Results
        self.result = None

    def submit(self, sender, args):
        self.result = {
            "user_selected_text_note_type"                : self.cmb_text_type.SelectedItem,
            "user_input_fr_spacing"                       : (float(self.txt_user_input_fr_spacing.Text)),
            "max_per_row"                                 : int(self.txt_max_per_row.Text),
            "fr_W"                                        : (float(self.user_input_txt_fr_W.Text)),
            "fr_H"                                        : (float(self.user_input_txt_fr_H.Text)),
            "create_filled_region_types_for_fill_patterns": self.chk_duplicate.Checked,
            "rename_filled_region_types"                  : self.chk_rename.Checked,
            "create_filled_regions_with_annotations"      : self.chk_create_filled.Checked,
            "create_text_notes_for_filled_regions"        : self.chk_create_text_notes.Checked,
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

    return naming_convention.format(
        fill_pattern_name = fill_pattern_name,
        color_name        = color_name,
        line_weight       = line_weight,
        masking           = masking
    )



# =============================================================
# Functions
# =============================================================

def create_filled_regions_with_annotations(doc, user_input_fr_spacing, max_per_row, user_input_fr_W, user_input_fr_H, fill_pattern_elements, filled_region_dict, create_text_notes_for_filled_regions, text_note_type_id):
    errors = []  # List to store error messages

    sorted_fill_patterns = sorted(fill_pattern_elements, key=lambda x: x.Name)
    
    active_view = doc.ActiveView
    if not isinstance(active_view, ViewDrafting):
        raise ValueError("The active view is not a Drafting View. Please activate a Drafting View and try again.")

    count_created_filled_regions = 0 # initiate a counter for how many filled regions have been created
    row = 0 # Parameters for grid layout
    col = 0 # Parameters for grid layout

    sorted_filled_regions_keys = sorted(filled_region_dict.keys(), key=lambda x: filled_region_dict[x]['name'])

    # Loop through all sorted fill patterns and create a filled region for each 
    
    try:
        for pattern in sorted_fill_patterns:
            fill_pattern_id = eid_int(pattern.Id) #getting all the id's of the patterns so we can match them to their filled region
            matching_filled_region_id = None # Initialize the id variable to None to handle cases where no match is found

            for key in sorted_filled_regions_keys:
                details = filled_region_dict[key]
                
                if details['foreground_fill_pattern_id'] == fill_pattern_id:
                    matching_filled_region_id = details['id']
                    filled_region_name        = details['name']
                    break
            
            # Now matching_id contains the id of the filled region that matches the foreground_fill_pattern_id, or None if no match was found
            if matching_filled_region_id is None:
                raise ValueError(f"\t\tERROR NO MATCH FOUND {fill_pattern_id} - {filled_region_name}.")
            
            # Calculate position based on row and column 
            x_offset = float(col * (user_input_fr_W + user_input_fr_spacing))
            y_offset = float(-row * (user_input_fr_H + user_input_fr_spacing))

            point1 = XYZ(x_offset, y_offset, 0.0)
            point2 = XYZ((x_offset), (y_offset + user_input_fr_H), 0.0)
            point3 = XYZ((x_offset + user_input_fr_W), (y_offset + user_input_fr_H), 0.0)
            point4 = XYZ((x_offset + user_input_fr_W), y_offset, 0.0)

            line_1 = Line.CreateBound(point1, point2)
            line_2 = Line.CreateBound(point2, point3)
            line_3 = Line.CreateBound(point3, point4)
            line_4 = Line.CreateBound(point4, point1)

            # Insert if statement for what happens if line_1 and line_2 are the same 
            # values which would create an error in Revit for short tolerance. 

            boundary = CurveLoop()
            boundary.Append(line_1)
            boundary.Append(line_2)
            boundary.Append(line_3)
            boundary.Append(line_4)

            list_boundaries = List[CurveLoop]()
            list_boundaries.Add(boundary)

            t = Transaction(doc, "Create Filled Regions")
            t.Start()
            
            try:
                filled_region_element_id = ElementId(matching_filled_region_id)
                active_view_id = active_view.Id
                FilledRegion.Create(doc, filled_region_element_id, active_view_id, list_boundaries)
                
                if create_text_notes_for_filled_regions:
                    create_text_note = TextNote.Create(doc, doc.ActiveView.Id, point1, str(filled_region_name), text_note_type_id)
                    if not create_text_note:
                        errors.append(f"\t\tFailed to create TextNote for {pattern.Name}")

                t.Commit()
                count_created_filled_regions +=1
                
            except Exception as e:
                t.RollBack()
                raise ValueError(f"\t\tFailed to create Filled Region for ID {filled_region_element_id}: {str(e)}")
            col += 1
            if col == max_per_row: 
                row += 1
                col = 0 

    except Exception as e: 
        errors.append(f"\t\tFailed: {str(e)}")

    print(f"\tCreated {count_created_filled_regions} Filled Regions and Annotations")
    # Print all collected errors at the end 
    if errors: 
        print("\tErrors:") 
        for error in errors: 
            print(error) 

# =============================================================
# Script Execution
# =============================================================

# Main function to execute the workflow
def main(doc):
    print("===================\nGetting Revit Elements & Building Dictionaries...\n===================\n\n")
    print("\n\nCOLLECTING TEXT NOTES TYPES...")
    text_note_types, text_note_dict = get_text_note_types(doc)
    text_type_names = [text_note_dict[tt.Id]['name'] for tt in text_note_types]
    
    print("\n\nCOLLECTING FILL PATTERN ELEMENTS...")
    fill_pattern_elements, fill_pattern_dict = get_fill_pattern_elements(doc)
    
    print("\n\nCOLLECTING FILLED REGION TYPES...")
    filled_region_dict = get_filled_region_types(doc, fill_pattern_dict)

    form = UserInputForm(text_type_names)
    if form.ShowDialog() == DialogResult.OK:
        user_input = form.result

        # Create reverse lookup dictionary
        text_note_name_to_id = {value["name"]: key for key, value in text_note_dict.items()}
        
        # Retrieve the ID using the selected name
        selected_text_note_name              = user_input["user_selected_text_note_type"]
        text_note_type_id                    = text_note_name_to_id[selected_text_note_name]
        user_input_fr_spacing                = float(user_input["user_input_fr_spacing"])
        max_per_row                          = float(user_input["max_per_row"])
        user_input_fr_W                      = float(user_input["fr_W"])
        user_input_fr_H                      = float(user_input["fr_H"])
        create_text_notes_for_filled_regions = user_input["create_text_notes_for_filled_regions"]
    
        try:
            print("\n\n===================\nCreating Revit Elements...\n===================\n\n")

            if user_input["create_filled_regions_with_annotations"]:
                print("\nCreating Filled Regions in Current View...")
                create_filled_regions_with_annotations(doc, user_input_fr_spacing, max_per_row, user_input_fr_W, user_input_fr_H, fill_pattern_elements, filled_region_dict, create_text_notes_for_filled_regions, text_note_type_id)

            MessageBox.Show("Success")

        except Exception as e:
            MessageBox.Show(f"Script Failed. An error occurred:\n{e}") 

    else:
        MessageBox.Show("Operation cancelled by user.", "Info", MessageBoxButtons.OK, MessageBoxIcon.Information)
        sys.exit()

main(doc)