#! python3
__author__ = "Ryan Johnston"
__date__ = "2024-11-27"
__purpose__ = "To create 3D views for each level with set parameters for constraints."

import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')  
from Autodesk.Revit.DB import View3D, FilteredElementCollector, Level, Transaction, BoundingBoxXYZ, XYZ, ViewFamilyType, ViewFamily, RevitLinkInstance, View, ViewType
from Autodesk.Revit.UI import TaskDialog
import System #type: ignore
from System.Drawing import Size, Point #type: ignore
from System.Windows.Forms import Form, Label, MessageBox, ComboBox, ComboBoxStyle, TextBox, Button, DialogResult, CheckBox, FormStartPosition, CheckedListBox, Panel, SelectionMode, CheckState, BorderStyle #type: ignore


doc = __revit__.ActiveUIDocument.Document  #type: ignore

if doc is None:
    TaskDialog.Show("Error", "No active document found. Open a Revit file and try again")
    exit()

#User input dialog to select levels and set offsets
class UserInputForm(Form):
    def __init__(self, levels, view_templates):
        super().__init__()
        self.Text          = "Select Levels and Set Offsets"
        self.Width         = 285
        self.Height        = 425
        self.StartPosition = FormStartPosition.CenterScreen

        self.levels          = sorted(levels, key=lambda x: x.Elevation)
        self.selected_levels = []
        self.view_templates = view_templates
        self.selected_view_templates = None
        self.include_linked_models = False
        self.bottom_offset   = 0
        self.top_offset      = 0
        self.initialize_components()

    def initialize_components(self):
        # Panel to hold list and ensure it's scrollable
        self.panel            = Panel()
        self.panel.Size       = Size(250, 200)
        self.panel.Location   = Point(10, 10)
        self.panel.AutoScroll = True

        # Checkbox list for levels
        self.level_list              = CheckedListBox()
        self.level_list.Dock         = System.Windows.Forms.DockStyle.Fill
        self.level_list.CheckOnClick = True
                
        # Load levels into the list
        for level in self.levels:
            self.level_list.Items.Add(level.Name)

        self.panel.Controls.Add(self.level_list)
        self.Controls.Add(self.panel)

        # Checkbox for Select All
        self.chk_select_all                 = CheckBox()
        self.chk_select_all.Text            = "Select All Levels"
        self.chk_select_all.Location        = Point(10, 210)
        self.chk_select_all.AutoSize        = True
        self.chk_select_all.CheckedChanged += self.select_all_levels
        self.Controls.Add(self.chk_select_all)

        # Separator Bevel Line
        separator1             = Label()
        separator1.BorderStyle = System.Windows.Forms.BorderStyle.Fixed3D
        separator1.Location    = Point(10, 240)
        separator1.Size        = Size(250, 2)  # Full width of the form
        self.Controls.Add(separator1)

        #Label for Section Box Settings
        section_label = Label()
        section_label.Text     = "Section Box Settings:"
        section_label.Location = Point(10, 250)
        section_label.AutoSize = True
        self.Controls.Add(section_label)

        # Label for Bottom Offset
        bottom_label          = Label()
        bottom_label.Text     = "Bottom Offset (inches):"
        bottom_label.Location = Point(30, 270)
        bottom_label.AutoSize = True
        self.Controls.Add(bottom_label)

        # Input for Bottom Offset
        self.bottom_input          = TextBox()
        self.bottom_input.Text     = "1"  # Default value
        self.bottom_input.Location = Point(220, 265)
        self.bottom_input.Width    = 40
        self.Controls.Add(self.bottom_input)

        # Label for Top Offset
        top_label          = Label()
        top_label.Text     = "Top Offset (inches):"
        top_label.Location = Point(30, 290)
        top_label.AutoSize = True
        self.Controls.Add(top_label)

        # Input for Top Offset
        self.top_input          = TextBox()
        self.top_input.Text     = "-8"  # Default value
        self.top_input.Location = Point(220, 285)
        self.top_input.Width    = 40
        self.Controls.Add(self.top_input)

        # Checkbox for including Revit Links
        self.chk_include_linked          = CheckBox()
        self.chk_include_linked.Text     = "Include Linked Models in Section Box"
        self.chk_include_linked.Location = Point(30, 315)
        self.chk_include_linked.AutoSize = True
        self.chk_include_linked.Checked = True  # Set default to checked
        self.Controls.Add(self.chk_include_linked)

        # Separator Bevel Line
        separator2             = Label()
        separator2.BorderStyle = System.Windows.Forms.BorderStyle.Fixed3D
        separator2.Location    = Point(10, 340)
        separator2.Size        = Size(250, 2)  # Full width of the form
        self.Controls.Add(separator2)

        # Apply View Templates
        self.cmb_view_template = ComboBox()
        self.cmb_view_template.DropDownStyle = ComboBoxStyle.DropDownList
        self.cmb_view_template.Location = Point(10, 350)
        self.cmb_view_template.Width = 300

        # Add "No View Template" as the default option
        self.cmb_view_template.Items.Add("No View Template")
        for template in self.view_templates:
            self.cmb_view_template.Items.Add(template.Name)

        # Set default selection to "No View Template"
        self.cmb_view_template.SelectedIndex = 0
        self.Controls.Add(self.cmb_view_template)

        # Submit Button
        self.btn_submit           = Button()
        self.btn_submit.Text      = "Generate 3D Views for Selected Levels"
        self.btn_submit.Location  = Point(10, 345)
        self.btn_submit.AutoSize  = True
        self.btn_submit.Click    += self.on_submit
        self.Controls.Add(self.btn_submit)

        def update_submit_button_position(sender, event): 
            self.btn_submit.Left = (self.ClientSize.Width - self.btn_submit.Width) // 2
            self.btn_submit.Top  = self.ClientSize.Height - self.btn_submit.Height - 10

        update_submit_button_position(None, None)
        self.Resize += update_submit_button_position

        self.btn_submit.Click += self.on_submit


    def select_all_levels(self, sender, args):
        check_state = self.chk_select_all.Checked
        for i in range(self.level_list.Items.Count):
            self.level_list.SetItemChecked(i, check_state)

    def on_submit(self, sender, event):
        try:
            self.selected_levels        = [self.levels[i] for i in range(self.level_list.Items.Count) if self.level_list.GetItemChecked(i)]
            self.bottom_offset          = -float(self.bottom_input.Text) / 12  # Convert inches to feet
            self.top_offset             = float(self.top_input.Text) / 12  # Convert inches to feet
            self.include_linked_models  = self.chk_include_linked.Checked
            selected_index              = self.cmb_view_template.SelectedIndex
            self.selected_view_template = (None if selected_index == 0 else self.view_templates[selected_index - 1])
            self.DialogResult           = DialogResult.OK

        except ValueError:
            MessageBox.Show("Please enter valid numeric offsets.", "Error")
            self.DialogResult = DialogResult.Cancel
        self.Close()

def get_local_model_extents(doc): 
    """Calculate extents for only the local model."""
    min_x, min_y, min_z = float('inf'), float('inf'), float('inf')
    max_x, max_y, max_z = float('-inf'), float('-inf'), float('-inf')

    model_elements = FilteredElementCollector(doc).WhereElementIsNotElementType().ToElements()

    for elem in model_elements:
        bbox = elem.get_BoundingBox(None)
        if bbox:
            min_x = min(min_x, bbox.Min.X)
            min_y = min(min_y, bbox.Min.Y)
            min_z = min(min_z, bbox.Min.Z)
            max_x = max(max_x, bbox.Max.X)
            max_y = max(max_y, bbox.Max.Y)
            max_z = max(max_z, bbox.Max.Z)

    project_bbox = BoundingBoxXYZ()
    project_bbox.Min = XYZ(min_x, min_y, min_z)
    project_bbox.Max = XYZ(max_x, max_y, max_z)

    return project_bbox

def get_model_and_linked_extents(doc):
    """Calculate extents for both local and linked models."""
    min_x, min_y, min_z = float('inf'), float('inf'), float('inf')
    max_x, max_y, max_z = float('-inf'), float('-inf'), float('-inf')

    # Local elements
    model_elements = FilteredElementCollector(doc).WhereElementIsNotElementType().ToElements()
    for elem in model_elements:
        bbox = elem.get_BoundingBox(None)
        if bbox:
            min_x = min(min_x, bbox.Min.X)
            min_y = min(min_y, bbox.Min.Y)
            min_z = min(min_z, bbox.Min.Z)
            max_x = max(max_x, bbox.Max.X)
            max_y = max(max_y, bbox.Max.Y)
            max_z = max(max_z, bbox.Max.Z)

    # Linked models
    link_instances = FilteredElementCollector(doc).OfClass(RevitLinkInstance).ToElements()
    for link in link_instances:
        linked_doc = link.GetLinkDocument()
        if not linked_doc:
            continue

        linked_elements = FilteredElementCollector(linked_doc).WhereElementIsNotElementType().ToElements()
        for linked_elem in linked_elements:
            bbox = linked_elem.get_BoundingBox(None)
            if bbox:
                transform = link.GetTransform()
                transformed_min = transform.OfPoint(bbox.Min)
                transformed_max = transform.OfPoint(bbox.Max)

                min_x = min(min_x, transformed_min.X)
                min_y = min(min_y, transformed_min.Y)
                min_z = min(min_z, transformed_min.Z)
                max_x = max(max_x, transformed_max.X)
                max_y = max(max_y, transformed_max.Y)
                max_z = max(max_z, transformed_max.Z)

    project_bbox = BoundingBoxXYZ()
    project_bbox.Min = XYZ(min_x, min_y, min_z)
    project_bbox.Max = XYZ(max_x, max_y, max_z)

    return project_bbox

def get_view_templates(doc):
    """Retrieve all view templates from the Revit document."""
    return [view for view in FilteredElementCollector(doc)
            .OfClass(View)
            .WhereElementIsNotElementType()
            .ToElements()
            if view.IsTemplate and view.ViewType == ViewType.ThreeD]

# Create 3D Views
def create_3d_view_for_level(doc, level, bottom_offset, top_offset, include_linked_models=False, view_template=None):
    # Establish Extents for BoundingBoxXYZ

    #Gather X or Y extents for linked models and host models or just for host models
    if include_linked_models:
        project_bbox = get_model_and_linked_extents(doc)
    else:
        project_bbox = get_local_model_extents(doc)

    # Gather Z extents for the level and next level to establish the Z extents
    levels = FilteredElementCollector(doc).OfClass(Level).ToElements()
    levels = sorted(levels, key=lambda x: x.Elevation)

    # Find the current level's index using explicit iteration for matching
    level_index = -1
    for i, lvl in enumerate(levels):
        if lvl.Id == level.Id:  # Match based on unique Id
            level_index = i
            break

    if level_index == -1:
        raise ValueError(f"Level '{level.Name}' not found in the list of levels.")

    if level_index + 1 >= len(levels):
        return None  # Skip for topmost level

    next_level = levels[level_index + 1]

    # Adjust the bounding box extents for Z (vertical) based on the level and offsets
    project_bbox.Min = XYZ(project_bbox.Min.X, project_bbox.Min.Y, level.Elevation + bottom_offset)
    project_bbox.Max = XYZ(project_bbox.Max.X, project_bbox.Max.Y, next_level.Elevation + top_offset)

    view_family_type = next((vft for vft in FilteredElementCollector(doc)
                            .OfClass(ViewFamilyType)
                            .ToElements()
                             if vft.ViewFamily == ViewFamily.ThreeDimensional), None)
    
    if not view_family_type:
        raise Exception("No suitable ViewFamilyType for 3D views found.")

    t = Transaction(doc, "Bulk Create 3D Views")
    t.Start()

    try:
        new_view      = View3D.CreateIsometric(doc, view_family_type.Id)
        new_view.Name = f"3D-Level-Isolated_{level.Name}"
        new_view.SetSectionBox(project_bbox)

        if view_template:
            new_view.ViewTemplateId = view_template.Id

        t.Commit()

    except Exception as e:
        t.RollBack()
        print(f"Error creating 3D View for Level '{level.Name}': {str(e)}")
        return None

    return new_view

def main(doc):
    levels = FilteredElementCollector(doc).OfClass(Level).ToElements()
    view_templates = get_view_templates(doc)
    form = UserInputForm(levels, view_templates)

    if  form.ShowDialog() == DialogResult.OK: 
        for level in form.selected_levels: 
            if  form.include_linked_models:
                project_bbox = get_model_and_linked_extents(doc)
            else:
                project_bbox = get_local_model_extents(doc)

            create_3d_view_for_level(
                doc,
                level,
                form.bottom_offset,
                form.top_offset,
                form.include_linked_models,
                form.selected_view_template
            )

            TaskDialog.Show("Info", f"Bounding Box Set to\nX: {round(project_bbox.Min.X)} : {round(project_bbox.Max.X)}\nY: {round(project_bbox.Min.Y)} : {round(level.Elevation)}\nZ: Not defined yet\n3D View created for {level.Name}")

    else:
        TaskDialog.Show("Info", "No Levels Selected")


if __name__ == "__main__":
    main(doc)