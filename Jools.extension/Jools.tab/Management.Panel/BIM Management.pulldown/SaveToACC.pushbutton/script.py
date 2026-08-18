#! python3
import os
import re
import clr
import json

# .NET / Revit API / WPF Imports
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference('System.Windows.Forms')

from Autodesk.Revit import DB # type: ignore
from Autodesk.Revit.UI import TaskDialog # type: ignore
from System.Windows.Forms import FolderBrowserDialog, DialogResult # type: ignore
from System import Guid # type: ignore

# Load pyRevit framework
from pyrevit import script, forms

# Constants
ACCOUNT_ID = "3dd53f14-c0db-4114-94e6-8a8097c16177"

# Standard pyRevit variables
uidoc = __revit__.ActiveUIDocument # type: ignore
doc = uidoc.Document
app = __revit__.Application # type: ignore

# Setup Logger and Output
logger = script.get_logger()
output = script.get_output()

def parse_acc_url(url):
    """Extracts Project ID and Folder URN from ACC URL."""
    try:
        proj_match = re.search(r'projects/([a-f0-9\-]{36})', url)
        folder_match = re.search(r'folderUrn=([^&]+)', url)
        if not proj_match or not folder_match: return None, None
        
        project_id = proj_match.group(1)
        from urllib.parse import unquote
        folder_urn = unquote(folder_match.group(1))
        return project_id, folder_urn
    except Exception as e:
        logger.warning(f"Error parsing URL: {e}")
        return None, None

def normalize_name(name):
    """Clean name for matching: lowercase, no extension, no ' (1)' suffix."""
    if not name:
        return ""
    name = name.lower()
    if name.endswith('.rvt'): 
        name = name[:-4]
    # Remove common Revit suffixes like ' - 1' or ' (1)'
    name = re.sub(r'[\s\-_]\d+$', '', name)
    name = re.sub(r'\(\d+\)$', '', name)
    return name.strip()

def main():
    # 1. Setup
    default_url = "https://acc.autodesk.com/docs/files/projects/947fcf1b-06b3-4fd2-9663-66efca3906cc?folderUrn=urn%3Aadsk.wipprod%3Afs.folder%3Aco.56KXdIDQQzGEMyctS4O2SQ&viewModel=detail&moduleId=folders"
    url = forms.ask_for_string(default=default_url, title="Upload & Relink", prompt="Paste ACC Folder URL:")
    if not url: return

    project_id_str, folder_id = parse_acc_url(url)
    if not project_id_str:
        TaskDialog.Show("URL Error", "Could not parse Project ID from URL.")
        return

    # Folder Picker
    dialog = FolderBrowserDialog()
    dialog.Description = "Select Folder containing Local Models"
    if dialog.ShowDialog() != DialogResult.OK:
        return
    folder_path = dialog.SelectedPath
    if not folder_path or not os.path.exists(folder_path):
        return

    local_rvt_files = [f for f in os.listdir(folder_path) if f.lower().endswith('.rvt')]
    
    # 2. PHASE 1: UPLOAD & CAPTURE GUIDS
    cloud_mapping = {} # norm_name -> {proj_id, model_id, name}
    account_guid = Guid(ACCOUNT_ID)
    project_guid = Guid(project_id_str)

    output.print_md("# Phase 1: Uploading Models")
    
    for file_name in local_rvt_files:
        full_path = os.path.join(folder_path, file_name)
        norm_name = normalize_name(file_name)
        
        logger.info(f"Uploading: {file_name}")
        temp_doc = None
        try:
            options = DB.OpenOptions()
            options.DetachFromCentralOption = DB.DetachFromCentralOption.DetachAndPreserveWorksets
            m_path = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(full_path)
            
            temp_doc = app.OpenDocumentFile(m_path, options)
            if temp_doc:
                if not temp_doc.IsWorkshared:
                    with DB.Transaction(temp_doc, "Enable Worksharing") as t:
                        t.Start()
                        temp_doc.EnableWorksharing("Shared Levels and Grids", "Workset1")
                        t.Commit()
                
                # Upload to ACC
                temp_doc.SaveAsCloudModel(account_guid, project_guid, folder_id, file_name)
                
                # Capture the GUIDs while doc is open and in-cloud
                summary = temp_doc.GetCloudModelSummary()
                p_id = summary.ProjectId.ToString()
                m_id = summary.ModelId.ToString()
                
                cloud_mapping[norm_name] = {
                    'project_id': p_id,
                    'model_id': m_id,
                    'original_name': file_name
                }
                
                logger.info(f"  SUCCESS. Captured IDs: Project={p_id}, Model={m_id}")
                temp_doc.Close(False)
            else:
                logger.warning("  Failed to open local file background.")
        except Exception as e:
            logger.error(f"  Upload failed for {file_name}: {e}")
            if temp_doc: 
                try:
                    temp_doc.Close(False)
                except:
                    pass

    # Save mapping to file as a backup
    mapping_file = os.path.join(folder_path, "_cloud_mapping.json")
    try:
        with open(mapping_file, 'w') as f:
            json.dump(cloud_mapping, f, indent=4)
        logger.info(f"Mapping saved to: {mapping_file}")
    except:
        pass

    # 3. PHASE 2: RELINK IN HOST MODEL
    output.print_md("# Phase 2: Relinking in Host Model")
    
    if not cloud_mapping:
        output.print_md("No successful uploads to relink.")
        return

    link_types = DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkType).ToElements()
    relink_count = 0

    for lt in link_types:
        # Get link name from parameter
        lt_name = lt.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString()
        norm_lt_name = normalize_name(lt_name)
        
        logger.info(f"Checking Link: '{lt_name}' (Normalized: '{norm_lt_name}')")

        if norm_lt_name in cloud_mapping:
            logger.info("  MATCH FOUND. Preparing to swap...")
            try:
                data = cloud_mapping[norm_lt_name]
                p_guid = Guid(data['project_id'])
                m_guid = Guid(data['model_id'])
                
                # Create Cloud Path
                cloud_path = DB.ModelPathUtils.ConvertCloudGUIDsToCloudPath(p_guid, m_guid)
                
                # Relink in a Transaction
                with DB.Transaction(doc, f"Relink {lt_name} to ACC") as t:
                    t.Start()
                    lt.LoadFrom(cloud_path, DB.WorksetConfiguration())
                    t.Commit()
                
                output.print_md(f"- ✅ Relinked: **{lt_name}** to Cloud")
                relink_count += 1
            except Exception as e_link:
                output.print_md(f"- ❌ Failed to relink **{lt_name}**: {e_link}")
        else:
            logger.info("  No match in upload list.")

    output.print_md(f"\n**Summary:** Uploaded {len(cloud_mapping)} files, Relinked {relink_count} links.")

if __name__ == "__main__":
    main()
