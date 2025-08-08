# !python3
import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitServices')
clr.AddReference('RevitNodes')

from Autodesk.Revit.DB import *
from RevitServices.Persistence import DocumentManager
from RevitServices.Transactions import TransactionManager
import os
from datetime import datetime

# Initialize the current document
doc = __revit__.ActiveUIDocument.Document # type: ignore

# Function to export views containing "NAVIS_" in their name
def export_navis_views():
    if not OptionalFunctionalityUtils.IsNavisworksExporterAvailable():
        print("Navisworks exporter is not available. Please install the necessary functionality.")
        return
    else:
        print("Navisworks Exporter installation files are installed...\nProceeding")
    
    today = datetime.now().strftime("%Y-%m-%d")

    print("\nStarting export_navis_views function")
    # Get all views in the project
    views_in_doc = FilteredElementCollector(doc).OfClass(View3D).ToElements()
    print(f"\tFound {len(views_in_doc)} views in the document")

    
    # Filter views with "NAVIS_" in the name
    navis_view_elements = [view for view in views_in_doc if "NAVIS_" in view.Name]
    print(f"\nnavis_view_elements = {navis_view_elements}")
    print(f"\tFiltered {len(navis_view_elements)} views containing 'NAVIS_' in the name")

    
    # Set up Navisworks export options
    options = NavisworksExportOptions()
    options.ConvertElementProperties = True
    options.ConvertLights            = True
    options.ConvertLinkedCADFormats  = True
    options.Coordinates              = NavisworksCoordinates.Shared
    options.DivideFileIntoLevels     = True
    options.ExportElementIds         = True
    options.ExportLinks              = True
    options.ExportParts              = True
    options.ExportRoomAsAttribute    = True
    options.ExportRoomGeometry       = True
    options.ExportScope              = NavisworksExportScope.View
    options.ExportUrls               = True
    options.FacetingFactor           = float(1.0)
    options.FindMissingMaterials     = True
    options.Parameters               = NavisworksParameters.All

    print(f"Options: {options}")

    # Directory to save the exported NWC files
    export_dir = "C:\\Users\\rjohnston\\OneDrive - Erdman Company\\Documents\\05_In-Progress-Content"
    print(f"\nexport_dir = {export_dir}")
    
    if not os.path.exists(export_dir):
        print("\tDirectory Path Entered Does Not Exist.")
        return
    else:
        print("\tDirectory exists. Proceeding with export...")

    # Start transaction
    t = Transaction(doc, "Bulk Export to NWC")
    t.Start()
    
    try:
        # Export each view
        for view in navis_view_elements:
                try:
                # Create file path
                    file_name = f"{view.Name}_{today}"
                    print(f'\nPreparing to export view: {view.Name} with file name: {file_name}')
                    # Export view to NWC
                    doc.Export(export_dir, file_name, NavisworksExportOptions())
                    print("Exported Executed.")
                
                except Exception as e:
                    print(f"\tException occurred while exporting {view.Name}: {e}")
                    t.RollBack()
                    return

    except Exception as e:
        print(f"\tTransaction Failed: {e}")
        t.RollBack()
        return
    
        # End transaction
    t.Commit()
    print("\n\nTransaction Committed")
    

# Call the function to export Navisworks views
export_navis_views()
print("Script execution completed")