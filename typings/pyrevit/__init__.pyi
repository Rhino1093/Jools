from typing import Any
from Autodesk.Revit.DB import Document
from Autodesk.Revit.UI import UIDocument

HOST_APP: Any
DOCS: Any
DB: Any
UI: Any

doc: Document
uidoc: UIDocument

class PyRevitException(Exception): ...
