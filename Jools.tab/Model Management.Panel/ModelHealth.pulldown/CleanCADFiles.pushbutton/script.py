#! python3
import clr

# Load Revit API before pyrevit to avoid interface instantiation errors in CPython
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from pyrevit import revit, script
from Autodesk.Revit import DB # type: ignore

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
logger = script.get_logger()

# logger.info("Script initialized") 

def main():
    # 1. Selection/Collection
    # Collect all CAD imports and links (ImportInstance includes both)
    cad_elements = DB.FilteredElementCollector(doc)\
                    .OfClass(DB.ImportInstance)\
                    .ToElements()

    if not cad_elements:
        print("No CAD imports or links found in the project.")
        return

    # 2. Validation / UI
    count_deleted = 0
    count_skipped = 0
    
    # 3. Model Modification in Transaction
    with revit.Transaction("Wipe All CAD Files"):
        for cad in cad_elements:
            try:
                # Ensure it's not pinned (Revit won't delete pinned elements)
                if cad.Pinned:
                    cad.Pinned = False
                
                # Get the name for reporting
                param = cad.get_Parameter(DB.BuiltInParameter.IMPORT_SYMBOL_NAME)
                cad_name = param.AsString() if param else "Unknown CAD File"
                
                doc.Delete(cad.Id)
                count_deleted += 1
                print("- Deleted: {}".format(cad_name))
                
            except Exception as e:
                logger.error("Could not delete CAD element {}: {}".format(cad.Id, e))
                count_skipped += 1

    # 4. Final Reporting
    print("\n" + "="*40)
    print("CAD CLEANUP COMPLETE")
    print("Deleted: {}".format(count_deleted))
    if count_skipped > 0:
        print("Skipped: {}".format(count_skipped))
    print("="*40)

if __name__ == "__main__":
    main()
