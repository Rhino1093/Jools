# Import necessary Revit API libraries
from Autodesk.Revit.DB import * 
from Autodesk.Revit.ApplicationServices import Application
from Autodesk.Revit.UI import *
from Autodesk.Revit.UI.Selection import ObjectType

# Import Python standard libraries
import csv
import os
import clr
import datetime
clr.AddReference('System.Windows.Forms')
from System.Windows.Forms import SaveFileDialog, DialogResult

UIAPP = __revit__.ActiveUIDocument #type: ignore
DOC = UIAPP.Document
ACTIVE_VIEW = UIAPP.ActiveView

# Define a function to check if a parameter is shared
def is_shared_parameter(param):
    return param.IsShared

def get_parameter_type(param):
    if param.IsShared:
        return "Shared Parameter"
    elif param.Definition.BuiltInParameter != BuiltInParameter.INVALID:
        return "Built-in Parameter"
    else:
        return "Project Parameter"

# Define the main function
def main():
    parameter_info = {} # Initialize a dictionary to store parameter information

    # Collect all elements in the document
    collector = FilteredElementCollector(DOC, ACTIVE_VIEW.Id)
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
                    "Categories": set([category]),
                    "Count": 1
                }

            else:
                parameter_info[param_key]["Categories"].add(category)
                parameter_info[param_key]["Count"] += 1

    dialog = SaveFileDialog() 
    dialog.Filter = "CSV Files (*.csv)|*.csv"
    dialog.Title = "Save CSV File"
    dialog.FileName = "ParameterInfo"

    if dialog.ShowDialog() == DialogResult.OK: 
        output_file = dialog.FileName

        # Write the parameter information to a CSV file
        with open(output_file, mode='w', newline='') as file:
            writer = csv.writer(file) 
            writer.writerow(["Parameter Name", "Parameter Type", "Categories", "Count"]) 
            for (param_name, param_type), info in parameter_info.items():
                writer.writerow([param_name, param_type, "; ".join(info["Categories"]), info["Count"]]) 

        dialog = TaskDialog("Export Complete")
        dialog.MainInstruction = "CSV Export Successful"
        dialog.MainContent = f"{len(parameter_info)} parameters export to CSV file to:\n{output_file}"
        dialog.Show()
    else:
        TaskDialog.Show("Export Canceled", "The file save operation was canceled.")

# Call the main function
if __name__ == "__main__":
    main()