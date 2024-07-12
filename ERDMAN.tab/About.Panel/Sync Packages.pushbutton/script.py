# !python3
import os
import subprocess
import clr
clr.AddReference('System.Windows.Forms')
from System.Windows.Forms import MessageBox, MessageBoxButtons, MessageBoxIcon

# Paths
source_path = r'L:\Homegrp\Virtual Design and Construction\REVIT-STDS\Dynamo-Deployments\Packages'
appdata_path = os.path.expanduser(r'~\AppData\Roaming\Dynamo\Dynamo Revit')

def open_folder(path):
    if os.path.exists(path):
        subprocess.Popen(f'explorer {os.path.realpath(path)}')
    else:
        raise Exception(f"Path does not exist: {path}")

# Main execution
try:
    # Open source and destination folders in Windows Explorer
    open_folder(source_path)
    open_folder(appdata_path)
    
    # Ensure the folders are opened first by adding a short delay
    import time
    time.sleep(2)  # Adjust the sleep duration if necessary
    
    # Show message box with instructions
    MessageBox.Show(
        f"Due to restricted firewall permissions, you have to manually copy the shared dynamo packages. I just opened up both source and destination folders for you.\n\nPlease manually copy all folders from the source folder:\n\n{source_path}\n\nto the destination folder:\n\n{appdata_path}\n\n"
        "When prompted, please make sure to select 'Overwrite' for all files to ensure that the latest versions are copied. If L:\ folder doesn't open, ensure VPN connection is working and run again.",
        "Message from BIM Manager: Manual Sync Required",
        MessageBoxButtons.OK,
        MessageBoxIcon.Information
    )

except Exception as e:
    MessageBox.Show(
        f"Failed to open folders: {e}",
        "Sync Error",
        MessageBoxButtons.OK,
        MessageBoxIcon.Error
    )