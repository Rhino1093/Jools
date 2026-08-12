# Jools pyRevit Extension - Development Context

This extension provides a collection of "quick-and-dirty" automation tools for Revit, designed for a single power user.

## Project Philosophy
- **Speed & Reliability:** Prioritize "works reliably" over "elegant architecture."
- **Single-Purpose:** Each tool should solve one specific, annoying gap in Revit.
- **Flat Structure:** Prefer single-file `script.py` implementations for pushbuttons. Avoid complex class hierarchies or multi-file modules unless logic is heavily repeated.
- **Direct UI:** Use simple dialogs. For complex UI, favor **XAML-based WPF** (stored as a multi-line string in the script) to ensure robust CPython compatibility and professional appearance.

## Environment & Tech Stack
- **Host:** Revit (Multiple versions) via **pyRevit**.
- **Runtime:** **CPython 3** is preferred (`#! python3` shebang).
- **Compatibility:** Avoid IronPython-exclusive features. **Deprecate WinForms** and `pyrevit.forms` for complex interactions; they are prone to failure in modern pyRevit environments.
- **Language:** Python 3. Standard library + Revit API + pyRevit libraries.
- **Linting:** VSCode/CPython. Use `# type: ignore` generously for `.NET` and Revit API imports (e.g., `import clr`, `from Autodesk.Revit import DB`).

## Setting Up a New Tool

### 1. Directory Structure
Each tool must reside in a `.pushbutton` folder inside a `.Panel` folder within the `.tab`.
```text
Jools.tab/
  MyPanel.Panel/
    bundle.yaml
    MyTool.pushbutton/
      bundle.yaml
      icon.png
      script.py
```

### 2. Panel Configuration (`bundle.yaml`)
Edit the `bundle.yaml` in the `.Panel` folder to define the panel's title and the layout of its tools.
- **Title:** The display name of the panel in the Revit ribbon.
- **Layout:** A list of tool folder names **without** the `.pushbutton` extension.
```yaml
title: "My Panel Name"

layout:
  - MyFirstTool
  - MySecondTool
```

### 3. Tool Configuration (`bundle.yaml`)
Edit the `bundle.yaml` inside the `.pushbutton` folder to define tool-specific metadata.
```yaml
title: "Display Name"
tooltip: "A clear, concise description of what the tool does. \nUse \\n for new lines."
help_url: "Optional link to documentation"
```

### 4. Tab Layout
If adding a **new panel**, ensure it is listed in the `Jools.tab/bundle.yaml` file under the `layout` section (without the `.Panel` extension).

## Coding Conventions

### Script Structure
Every `script.py` should follow this general template:
```python
#! python3
import clr
import sys
from pyrevit import revit, script

# .NET / Revit API / WPF Imports
clr.AddReference("RevitAPI")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
from Autodesk.Revit import DB # type: ignore
from System.Windows import Window, WindowStartupLocation # type: ignore

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
logger = script.get_logger()

# Use logging.INFO and above
# Always include during initial development, then comment out when script is stable
# logger.info("Script initialized") 

def main():
    # 1. Input/Selection (WPF XAML String if complex)
    # 2. Validation
    # 3. Model Modification in Transaction
    with revit.Transaction("Tool Name"):
        pass

if __name__ == "__main__":
    main()
```

### Transactions
- Always wrap model modifications in `with revit.Transaction("Description"):`.
- For complex operations, consider `TransactionGroup` to allow a single undo for multiple steps.

### UI Guidelines
- **Simple Alerts:** Use `Autodesk.Revit.UI.TaskDialog.Show("Title", "Message")` for basic notifications.
- **Complex Dialogs:** Use **WPF via XAML**. Define the XAML layout as a multi-line string within the `script.py` to keep the tool self-contained. 
    - This approach avoids the fragility of `pyrevit.forms` and the dated look/API of WinForms.
- **Input:** For single inputs, a minimal WPF Window built in code is acceptable.

### Debugging & Logging
- **pyRevit Logger:** Use `script.get_logger()` with levels `INFO`, `WARNING`, or `ERROR`.
- **Development Cycle:** Keep active `logger.info()` calls during creation and testing. **Comment them out** once the script works properly to keep the production output clean.
- **Dry Run:** Include a "Dry Run" mode for complex scripts to validate logic before committing transactions.
- **Reporting:** Print a summary table using `output.print_table()` after batch operations.

## Project Structure
- `Jools.extension/`: Root of the pyRevit extension.
- `Jools.extension/Jools.tab/`: Main ribbon tab definition.
- `Jools.extension/bin/`: Contains hooks and shared binaries.
    - `active hooks/`: Hooks currently in use (e.g., `doc-saved.py`).
    - `inactive hooks/`: Repository of available but disabled hooks.
- `Jools.extension/typings/`: Stubs for IntelliSense.

## Workflow Patterns
1. **Research & Planning:** For non-trivial tools, create a `PLAN.md` or similar document (e.g., `LightsToCeiling-accuracy-plan.md`) to outline logic and edge cases.
2. **Implementation:** Write a self-contained `script.py`.
3. **Validation:** Test in Revit using various selection sets and model conditions.

## Key Revit API Patterns
- **Selection:** Use `uidoc.Selection.GetElementIds()` for current selection or `PickObject`/`PickObjects` for interactive selection.
- **Collectors:** Use `FilteredElementCollector(doc)` with appropriate filters (e.g., `OfCategory`, `OfClass`, `WhereElementIsNotElementType`).
- **Linking:** When dealing with linked models, use `ReferenceIntersector` with `FindReferencesInRevitLinks = True`.
