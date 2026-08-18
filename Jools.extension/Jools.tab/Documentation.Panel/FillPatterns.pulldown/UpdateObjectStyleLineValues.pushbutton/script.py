#! python3
import clr
import sys
clr.AddReference('RevitAPI')
clr.AddReference('System.Windows.Forms')
from Autodesk.Revit.DB import *
from System.Windows.Forms import MessageBox, MessageBoxButtons, MessageBoxIcon, Form, Label, ComboBox, Button, DialogResult, ComboBoxStyle

class InputForm(Form):
    def __init__(self):
        self.Text = "ObjSty Line Weights"
        self.Width = 275
        self.Height = 600

        self.labels = []
        self.comboboxes = []
        self.result = {}

        for i in range(1, 17):
            lbl = Label()
            lbl.Text = f"Line Weight {i} to be reassigned to:"
            lbl.Top = 20 + (i - 1) * 30
            lbl.Left = 10
            lbl.AutoSize = True
            self.labels.append(lbl)
            self.Controls.Add(lbl)

            cmb = ComboBox()
            cmb.Top = lbl.Top
            cmb.Left = 200
            cmb.Width = 50
            cmb.DropDownStyle = ComboBoxStyle.DropDownList
            for j in range(1, 17):
                cmb.Items.Add(j)
            cmb.SelectedIndex = i - 1  # Default to the current line weight value
            self.comboboxes.append(cmb)
            self.Controls.Add(cmb)

        self.submit_button = Button()
        self.submit_button.Text = "Submit"
        self.submit_button.Top = self.comboboxes[-1].Top + 40
        self.submit_button.Left = 175
        self.submit_button.Click += self.submit
        self.Controls.Add(self.submit_button)

    def submit(self, sender, event):
        for i, cmb in enumerate(self.comboboxes):
            self.result[i + 1] = int(cmb.SelectedItem)
        self.DialogResult = DialogResult.OK
        self.Close()

# Get user input for line weight changes
form = InputForm()
if form.ShowDialog() == DialogResult.OK:
    line_weight_changes = form.result
else:
    MessageBox.Show("Operation cancelled by user.", "Info", MessageBoxButtons.OK, MessageBoxIcon.Information)
    sys.exit()

# Warning message
warning_message = (
    "This is a project-changing script and should only be ran 1 time per project when changing from old line standards to new. Running this more than 1 time will continue to step the wights down continuously."
    "\nPlease ensure the project has been recently published in case we need to roll it back.\n\n"
    "Are you sure you want to continue?"
)

# Show warning message box
response = MessageBox.Show(warning_message, "Warning", MessageBoxButtons.YesNo, MessageBoxIcon.Warning)

# Check user's response
if response == DialogResult.No:
    # User chose not to continue
    MessageBox.Show("Operation cancelled by user.", "Info", MessageBoxButtons.OK, MessageBoxIcon.Information)
    # Exit the script
    sys.exit()

# Getting the document
doc = __revit__.ActiveUIDocument.Document # type: ignore

changes_count = 0  # Counter for changes
changed_categories = []  # List to keep track of changed categories

def update_line_weights(category):
    global changes_count
    global changed_categories

    try:
        # Check and update Projection Line Weights
        projection_style = category.GetGraphicsStyle(GraphicsStyleType.Projection)
        if projection_style:
            current_proj_weight = category.GetLineWeight(GraphicsStyleType.Projection)
            print(f"Category: {category.Name}, Current Projection Line Weight: {current_proj_weight}")
            if current_proj_weight in line_weight_changes:
                new_proj_weight = line_weight_changes[current_proj_weight]
                if current_proj_weight != new_proj_weight:
                    category.SetLineWeight(new_proj_weight, GraphicsStyleType.Projection)
                    updated_proj_weight = category.GetLineWeight(GraphicsStyleType.Projection)
                    print(f"Updated Projection Line Weight: {updated_proj_weight}")
                    if updated_proj_weight == new_proj_weight:
                        changes_count += 1
                        changed_categories.append(f"{category.Name} (Projection)")
                    else:
                        print(f"Failed to update Projection Line Weight for {category.Name}")

        # Check and update Cut Line Weights
        cut_style = category.GetGraphicsStyle(GraphicsStyleType.Cut)
        if cut_style:
            current_cut_weight = category.GetLineWeight(GraphicsStyleType.Cut)
            print(f"Category: {category.Name}, Current Cut Line Weight: {current_cut_weight}")
            if current_cut_weight in line_weight_changes:
                new_cut_weight = line_weight_changes[current_cut_weight]
                if current_cut_weight != new_cut_weight:
                    category.SetLineWeight(new_cut_weight, GraphicsStyleType.Cut)
                    updated_cut_weight = category.GetLineWeight(GraphicsStyleType.Cut)
                    print(f"Updated Cut Line Weight: {updated_cut_weight}")
                    if updated_cut_weight == new_cut_weight:
                        changes_count += 1
                        changed_categories.append(f"{category.Name} (Cut)")
                    else:
                        print(f"Failed to update Cut Line Weight for {category.Name}")

        # Recursively update subcategories
        for subcategory in category.SubCategories:
            update_line_weights(subcategory)
    except Exception as cat_error:
        print(f"Error processing category {category.Name}: {str(cat_error)}")

# Start a transaction group to encapsulate all changes
tg = TransactionGroup(doc, "Update Line Weights")
tg.Start()

try:
    # Start a manual transaction to modify the document
    t = Transaction(doc, "Update Line Weights")
    t.Start()

    try:
        categories = doc.Settings.Categories
        for category in categories:
            update_line_weights(category)
        t.Commit()  # Commit the transaction if all changes are successful
        print("Transaction committed successfully.")
    except Exception as e:
        t.Rollback()  # Rollback the transaction if an error occurs
        print(f"Transaction Error: {str(e)}")
    finally:
        if t.HasStarted() and not t.HasEnded():
            t.Rollback()
except Exception as e:
    tg.RollBack()  # Rollback the transaction group if any transaction fails
    print(f"Transaction Group Error: {str(e)}")
else:
    tg.Assimilate()  # Commit the entire transaction group if all transactions are successful
    print("Transaction group assimilated successfully.")

# Output success message with detailed information on changes
summary = f"Line weights updated successfully! Total changes made: {changes_count}."

# Using MessageBox to show the summary
MessageBox.Show(summary, "Update Object Style Line Weights", MessageBoxButtons.OK, MessageBoxIcon.Information)
