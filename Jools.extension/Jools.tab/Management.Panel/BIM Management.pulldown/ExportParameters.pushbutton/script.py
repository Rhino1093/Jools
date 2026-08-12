#! python3
import clr
import sys
import csv
import os

# .NET / Revit API / WPF Imports
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference('System.Windows.Forms')

from Autodesk.Revit import DB # type: ignore
from Autodesk.Revit.UI import TaskDialog # type: ignore
from System.Windows.Forms import SaveFileDialog, DialogResult # type: ignore

# Standard pyRevit variables
uidoc = __revit__.ActiveUIDocument # type: ignore
doc = uidoc.Document
active_view = uidoc.ActiveView

def get_parameter_type(param):
    """Determine the type of the parameter."""
    if param.IsShared:
        return "Shared Parameter"
    elif param.Definition.BuiltInParameter != DB.BuiltInParameter.INVALID:
        return "Built-in Parameter"
    else:
        return "Project Parameter"

def main():
    parameter_info = {} # Initialize a dictionary to store parameter information

    # Collect all elements in the active view
    collector = DB.FilteredElementCollector(doc, active_view.Id)
    elements = collector.WhereElementIsNotElementType().ToElements()
    
    for elem in elements:
        category = elem.Category.Name if elem.Category else "No Category"
        param_set = elem.Parameters

        for param in param_set:
            param_name = param.Definition.Name
            param_type = get_parameter_type(param)
            param_key = (param_name, param_type)

            # Store parameter information in the dictionary
            if param_key not in parameter_info:
                parameter_info[param_key] = {
                    "Categories": {category},
                    "Count": 1
                }
            else:
                parameter_info[param_key]["Categories"].add(category)
                parameter_info[param_key]["Count"] += 1

    # Use SaveFileDialog for path selection
    dialog = SaveFileDialog() 
    dialog.Filter = "CSV Files (*.csv)|*.csv"
    dialog.Title = "Save CSV File"
    dialog.FileName = "ParameterInfo"

    if dialog.ShowDialog() == DialogResult.OK: 
        output_file = dialog.FileName

        # Write the parameter information to a CSV file
        try:
            with open(output_file, mode='w', newline='', encoding='utf-8') as file:
                writer = csv.writer(file) 
                writer.writerow(["Parameter Name", "Parameter Type", "Categories", "Count"]) 
                for (param_name, param_type), info in parameter_info.items():
                    writer.writerow([param_name, param_type, "; ".join(sorted(info["Categories"])), info["Count"]]) 

            completion_dialog = TaskDialog("Export Complete")
            completion_dialog.MainInstruction = "CSV Export Successful"
            completion_dialog.MainContent = f"{len(parameter_info)} parameters exported to:\n{output_file}"
            completion_dialog.Show()
        except Exception as e:
            TaskDialog.Show("Export Error", f"Failed to save file: {str(e)}")
    else:
        TaskDialog.Show("Export Canceled", "The file save operation was canceled.")

if __name__ == "__main__":
    main()
