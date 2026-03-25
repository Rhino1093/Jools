# IronPython (Python 2.7) for stability
import os
import clr

# Load Revit API
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import *

# Load pyRevit framework
from pyrevit import script, forms
from System import Guid

# Setup Logger and Output
logger = script.get_logger()
output = script.get_output()

def normalize_name(name):
    """Clean name for matching: lowercase, no extension."""
    name = name.lower()
    if name.endswith('.rvt'): name = name[:-4]
    return name.strip()

def main():
    # 1. Select Folder
    folder_path = forms.pick_folder(title="Select Folder containing Local Models")
    if not folder_path: return

    local_rvt_files = {normalize_name(f): os.path.join(folder_path, f) 
                       for f in os.listdir(folder_path) if f.lower().endswith('.rvt')}

    # 2. Setup Revit Context
    try:
        uiapp = __revit__
        host_doc = uiapp.ActiveUIDocument.Document
        app = uiapp.Application
    except Exception as e:
        logger.critical("Could not access Revit: {}".format(e))
        return

    # 3. Find Links in Host
    link_types = FilteredElementCollector(host_doc).OfClass(RevitLinkType).ToElements()
    targets = []
    for lt in link_types:
        lt_name = lt.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM).AsString()
        norm_name = normalize_name(lt_name)
        if norm_name in local_rvt_files:
            targets.append((lt, local_rvt_files[norm_name], lt_name))

    if not targets:
        forms.alert("No matching links found in the active model.", title="No Matches")
        return

    # 4. Process with Progress Bar
    results = []
    total = len(targets)
    
    with forms.ProgressBar(title="Relinking to Cloud...", total=total, cancellable=True) as pb:
        for i, (link_type, local_path, link_name) in enumerate(targets):
            if pb.cancelled:
                logger.warning("Process cancelled by user.")
                break
                
            pb.update_progress(i + 1, total)
            logger.info("Processing: {}".format(link_name))
            
            temp_doc = None
            try:
                # Open normally (not detached) to preserve cloud path link
                options = OpenOptions()
                m_path = ModelPathUtils.ConvertUserVisiblePathToModelPath(local_path)
                
                # We use a try-except specifically for the open
                try:
                    temp_doc = app.OpenDocumentFile(m_path, options)
                except Exception as e_open:
                    logger.warning("  Could not open {}: {}".format(link_name, e_open))
                    results.append("{}: Open Failed".format(link_name))
                    continue

                if temp_doc:
                    # Try to get the Central Path
                    central_path = temp_doc.GetWorksharingCentralModelPath()
                    
                    if central_path and central_path.IsCloudPath():
                        p_guid = central_path.GetProjectGUID()
                        m_guid = central_path.GetModelGUID()
                        
                        logger.info("  Cloud IDs found. Project: {}, Model: {}".format(p_guid, m_guid))
                        
                        # Create Cloud Path
                        cloud_path = ModelPathUtils.ConvertCloudGUIDsToCloudPath(p_guid, m_guid)
                        
                        # Relink
                        ws_config = WorksetConfiguration()
                        link_type.LoadFrom(cloud_path, ws_config)
                        
                        logger.info("  Relinked successfully.")
                        results.append("{}: Success".format(link_name))
                    else:
                        logger.warning("  {} does not appear to be a cloud-mapped file.".format(link_name))
                        results.append("{}: No Cloud Path Found".format(link_name))
                    
                    temp_doc.Close(False)
                else:
                    results.append("{}: Open returned None".format(link_name))

            except Exception as e:
                logger.error("  Error with {}: {}".format(link_name, e))
                results.append("{}: Error - {}".format(link_name, e))
                if temp_doc: temp_doc.Close(False)

    # Final Summary
    output.print_md("# Relink to Cloud Summary")
    for r in results:
        status_icon = ":white_check_mark:" if "Success" in r else ":x:"
        output.print_md("{} {}".format(status_icon, r))

if __name__ == "__main__":
    main()
