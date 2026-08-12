#! python3
import os
from shutil import copyfile
import clr

# .NET / Revit API / WPF Imports
clr.AddReference("RevitAPIUI")
from Autodesk.Revit.UI import TaskDialog # type: ignore

# Get script Path
current_path = os.path.realpath(__file__)

# Construct the hook file paths using the current script path as a base
# We replace the path from the extension tab down to the script with the relative paths to our target files
target_part = os.path.join('Jools.tab', 'Management.Panel', 'BIM Management.pulldown', 'Switch.pushbutton', 'script.py')

path_hook = current_path.replace(target_part, os.path.join('hooks', 'doc-saved.py'))
path_active = current_path.replace(target_part, os.path.join('bin', 'active hooks', 'doc-saved.py'))
path_inactive = current_path.replace(target_part, os.path.join('bin', 'inactive hooks', 'doc-saved.py'))
path_on = current_path.replace(target_part, os.path.join('bin', 'hook states', 'doc-saved-on.py'))
path_off = current_path.replace(target_part, os.path.join('bin', 'hook states', 'doc-saved-off.py'))

# Ensure the hooks directory exists (hook states and active/inactive hooks should already exist)
hooks_dir = os.path.dirname(path_hook)
if not os.path.exists(hooks_dir):
    os.makedirs(hooks_dir)

is_active = os.path.exists(path_on)

if is_active:
    if os.path.exists(path_inactive):
        copyfile(path_inactive, path_hook)
    if os.path.exists(path_on):
        os.rename(path_on, path_off)
    outcome = 'Run Model Health Check on Save: DEACTIVATED'
else:
    # If it's not active, we look for the 'off' state file
    if os.path.exists(path_off):
        os.rename(path_off, path_on)
    
    if os.path.exists(path_active):
        copyfile(path_active, path_hook)
    
    outcome = 'Run Model Health Check on Save: ACTIVATED'

TaskDialog.Show("Jools pyRevit", outcome)
