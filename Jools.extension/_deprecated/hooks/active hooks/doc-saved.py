#! python
# Stays on IronPython deliberately: this hook uses pyrevit.forms, which raises
# PyRevitCPythonNotSupported under CPython 3 (CLAUDE.md 2.2).
#import revitron and pyrevit modules

from pyrevit import forms
from pyrevit import script
from pyrevit import revit,DB
from Autodesk.Revit.DB import Transaction

# get document
doc = revit.doc

#check if home view is present in the model, abort if not found
fec_sheets = DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_Sheets).ToElements()

name_match = 'OPEN/CLOSE'
match_found = False

for s in fec_sheets:
	if name_match in s.Name:
		match_found = True

if not match_found:
	forms.alert('View with name "OPEN/CLOSE" not found. Health check not undertaken')
	script.exit()


# element collectors
fec_rooms = DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_Rooms).ToElements()
fec_gans = DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_GenericAnnotation).ToElements()
fec_views = DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_Views).ToElements()
fec_viewports = DB.FilteredElementCollector(doc).OfClass(DB.Viewpot).WhereElementIsNotElementType

# find the revit dials in the model
dial_instances, dial_names = [],[]

# retrieve dials which contain the metric parameter
for dial in fec_gans:
	try:
		param = dial.LookupParameter('Metric Name')
		metric = param.AsString()
		if metric !="":
			dial_instances.append(dial)
			dial_names.append(metric)
	except:
		pass

# Lists for updating objects
set_dials = []
set_values = []



#Metric 1 - warning count
warnings_all = doc.GetWarnings
warnings_count = len(warnings_all)

if 'WARNINGS' in dial_names:
	ind = dial_names.index('WARNINGS')
	dial = dial_instances[ind]
	set_dials.append(dial)
	set_values.append(warnings_count)



#Metric 2 - workset count
workset_list = DB.FilteredElementCollector(doc).OfKind(DB.WorksetKind.UserWorkset)

for w in workset_list:
	if isinstance(workset_list, list):
		worksets_count = len(workset_list)
	else:
		worksets_count = 1

if 'WORKSET' in dial_names:
	ind = dial_names.index('WORKSET')
	dial = dial_instances[ind]
	set_dials.append(dial)
	set_values.append(worksets_count)



#Metric 3 - purgeable elements



#Metric 4 - File Size (Mb)


#Transaction to print values to dials
if len(set_dials) > 0:

	t = Transaction(doc, "Updating Dials")

	t.Start()

	for d,v in zip(set_dials,set_values):
		param = d.LookupParameter('Value')
		if param:
			param.Set(v)

	t.Commit()
