# Boilerplate
import os
from shutil import copyfile
from pyrevit import forms

# Get script Path
current_path = os.path.realpath(__file__)

# Construct the hook file paths
path_hook = current_path.replace(r'ERDMAN.tab\Model Management.Panel\Switch.pushbutton\script.py')
path_active = current_path.replace(r'ERDMAN.tab\Model Management.Panel\Switch.pushbutton\script.py')
path_inactive = current_path.replace(r'ERDMAN.tab\Model Management.Panel\Switch.pushbutton\script.py') 
#need to replace this with the dormat copy that's store in \ERDMAN.extension\bin\

path_on = current_path.replace(r'ERDMAN.tab\Model Management.Panel\Switch.pushbutton\script.py')

#need to replace this with the dormat copy that's store in \ERDMAN.extension\bin\
path_off = current_path.replace(r'ERDMAN.tab\Model Management.PanelSwitch.pushbutton\script.py')

is_active = os.path.exists(path_on)

if is_active:
	copyfile(path_inactive, path_hook)
	os.rename(path_on,path_off)
	outcome = 'Run Model Health Check on Save: DEACTIVATED'

else:
	os.rename(path_off,path_on)
	copyfile(path_active, path_hook)
	outcome = 'Run Model Health Check on Save: ACTIVATED'

pyrevit.forms.alert(outcome)