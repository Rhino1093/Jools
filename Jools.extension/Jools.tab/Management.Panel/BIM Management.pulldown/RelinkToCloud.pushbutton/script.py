#! python3
import os
import clr
import sys
from types import ModuleType

# --- CPYTHON WORKAROUND ---
# Bypasses "interface takes exactly one argument" error in pyrevit.revit.events
try:
    mock_events = ModuleType('pyrevit.revit.events')
    mock_events._HANDLER = None
    sys.modules['pyrevit.revit.events'] = mock_events
except Exception:
    pass
# --------------------------

# .NET / Revit API / WPF Imports
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference('System.Windows.Forms')

from Autodesk.Revit import DB # type: ignore
from Autodesk.Revit.UI import TaskDialog # type: ignore
from System.Windows.Forms import FolderBrowserDialog, DialogResult # type: ignore

# Load pyRevit framework for output
from pyrevit import script

# Standard pyRevit variables
uidoc = __revit__.ActiveUIDocument # type: ignore
doc = uidoc.Document
app = __revit__.Application # type: ignore

# Setup Logger and Output
logger = script.get_logger()
output = script.get_output()

def normalize_name(name):
    """Clean name for matching: lowercase, no extension."""
    if not name:
        return ""
    name = name.lower()
    if name.endswith('.rvt'): 
        name = name[:-4]
    return name.strip()


class OutputProgress(object):
    """///Summary: Drop-in replacement for forms.ProgressBar, CPython-safe.

    pyrevit.forms raises PyRevitCPythonNotSupported under `#! python3`
    (CLAUDE.md 2.2), but the pyRevit output window's own progress bar works on
    every engine. Closing the output window stands in for the old cancel button,
    so the `pb.cancelled` check in the loop keeps working unchanged.
    """

    def __init__(self, title, total):
        self._title = title
        self._total = max(1, total)

    def __enter__(self):
        output.set_title(self._title)
        output.update_progress(0, self._total)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        output.reset_progress()
        return False

    @property
    def cancelled(self):
        """True once the user closes the output window."""
        return output.is_closed_by_user

    def update_progress(self, current, total=None):
        output.update_progress(current, total or self._total)


def main():
    # 1. Select Folder
    dialog = FolderBrowserDialog()
    dialog.Description = "Select Folder containing Local Models"
    if dialog.ShowDialog() != DialogResult.OK:
        return
    
    folder_path = dialog.SelectedPath
    if not folder_path or not os.path.exists(folder_path):
        return

    local_rvt_files = {normalize_name(f): os.path.join(folder_path, f) 
                       for f in os.listdir(folder_path) if f.lower().endswith('.rvt')}

    # 2. Find Links in Host
    link_types = DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkType).ToElements()
    targets = []
    for lt in link_types:
        lt_name = lt.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString()
        norm_name = normalize_name(lt_name)
        if norm_name in local_rvt_files:
            targets.append((lt, local_rvt_files[norm_name], lt_name))

    if not targets:
        TaskDialog.Show("Relink to Cloud", "No matching links found in the active model.")
        return

    # 3. Process with Progress Bar
    results = []
    total = len(targets)
    
    with OutputProgress("Relinking to Cloud...", total) as pb:
        for i, (link_type, local_path, link_name) in enumerate(targets):
            if pb.cancelled:
                logger.warning("Process cancelled by user.")
                break
                
            pb.update_progress(i + 1, total)
            logger.info(f"Processing: {link_name}")
            
            temp_doc = None
            try:
                # Open normally (not detached) to preserve cloud path link
                options = DB.OpenOptions()
                m_path = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(local_path)
                
                try:
                    temp_doc = app.OpenDocumentFile(m_path, options)
                except Exception as e_open:
                    logger.warning(f"  Could not open {link_name}: {e_open}")
                    results.append(f"{link_name}: Open Failed")
                    continue

                if temp_doc:
                    # Try to get the Central Path
                    central_path = temp_doc.GetWorksharingCentralModelPath()
                    
                    if central_path and central_path.IsCloudPath():
                        p_guid = central_path.GetProjectGUID()
                        m_guid = central_path.GetModelGUID()
                        
                        logger.info(f"  Cloud IDs found. Project: {p_guid}, Model: {m_guid}")
                        
                        # Create Cloud Path
                        cloud_path = DB.ModelPathUtils.ConvertCloudGUIDsToCloudPath(p_guid, m_guid)
                        
                        # Relink in a Transaction
                        try:
                            with DB.Transaction(doc, f"Relink {link_name} to Cloud") as t:
                                t.Start()
                                ws_config = DB.WorksetConfiguration()
                                link_type.LoadFrom(cloud_path, ws_config)
                                t.Commit()
                            
                            logger.info("  Relinked successfully.")
                            results.append(f"{link_name}: Success")
                        except Exception as e_relink:
                            logger.error(f"  Relink failed for {link_name}: {e_relink}")
                            results.append(f"{link_name}: Relink Failed")
                    else:
                        logger.warning(f"  {link_name} does not appear to be a cloud-mapped file.")
                        results.append(f"{link_name}: No Cloud Path Found")
                    
                    temp_doc.Close(False)
                else:
                    results.append(f"{link_name}: Open returned None")

            except Exception as e:
                logger.error(f"  Error with {link_name}: {e}")
                results.append(f"{link_name}: Error - {e}")
                if temp_doc: 
                    try:
                        temp_doc.Close(False)
                    except:
                        pass

    # Final Summary
    output.print_md("# Relink to Cloud Summary")
    for r in results:
        status_icon = "✅" if "Success" in r else "❌"
        output.print_md(f"{status_icon} {r}")

if __name__ == "__main__":
    main()
