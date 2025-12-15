## 1. Purpose of this agent

You are writing **small, quick-and-dirty pyRevit tools** for a single power user (the author of this repo).

These tools:
- Live inside a **pyRevit extension**
- Are **not** for external distribution
- Are used to fix **very specific, annoying gaps in Revit** (e.g. array an object along a curve, batch rename, auto-set parameters, etc.)
- Do **not** need polished architecture, fancy patterns, or UI frameworks

Your job:
- Generate **single-file**, self-contained `script.py` implementations for each pushbutton
- Use **pyRevit**’s helpers (`pyrevit.forms`, `pyrevit.revit`, etc.)
- Prefer **simple prompts and dialogs** over complex UI
- Favor **“works reliably”** over “elegant” or “beautiful”

If you start turning one of these into a full-blown app, you’re doing it wrong.

---
## 2. Runtime environment

### 2.1 Host

Code runs inside:
- Revit (various versions)
- Hosted by **pyRevit**

Runtime:
- Usually **IronPython 2.7** (pyRevit standard).  
- Default to running scripts under IronPython (use the `#! python` shebang) so pyRevit’s built-in `forms` helpers and WPF bits are available. Only opt into CPython if you have a specific reason and know the APIs you’re using are supported there.
- Scripts often use **.NET assemblies** via `clr.AddReference`
- Because IronPython 2.7 is the baseline, avoid Python 3-only syntax (f-strings, pathlib features that rely on newer stdlib behavior, etc.) unless you explicitly switch the script to CPython and document why.

Assume:
- No external Python packages (pip)
- Only standard library + .NET + pyRevit
### 2.2 Editor / analysis environment

Code is edited in **VSCode** using **CPython** for IntelliSense.

This means:
- Many `.NET` and Revit imports will look “unresolved” to CPython
- We accept using `# type: ignore` to silence noise
- We may use stubs and `extraPaths`, but the agent should still be tolerant of unresolved imports

You do **not** need to make IntelliSense perfect. Just avoid doing anything that makes it worse for no reason.

---
## 3. General design philosophy

These tools are:
- Single-purpose
- Short-lived
- Only used by the repo owner

Therefore:
1. **Keep each tool in one file** (`script.py`) unless:
    - There is obvious, repeated logic being copied around; then you may suggest or use a tiny helper module.
2. **Minimal UI:**
    - Use `pyrevit.forms` dialogs (simple prompts, selection dialogs, alerts).
    - Do **not** use WPF or WinForms unless explicitly requested.
3. **No premature architecture:**
    - No custom MVVM, no frameworks, no complex class hierarchies.
    - Functions + a simple `main()` is usually enough.
4. **Fail safe, not fancy:**
    - Prefer straightforward code that’s easy to understand.
    - Validate selections and user inputs, show a clear `forms.alert` on bad input.
5. **Don’t fight Revit:**
    - Use the Revit API patterns that are common and robust.
    - Wrap changes in `with revit.Transaction("Name"):`.
---
## 4. Imports and basic script structure

Every script should generally follow this pattern:

```python
# script.py

from pyrevit import revit, forms, script

# IronPython / .NET imports
import clr  # type: ignore

# Add any .NET references you need
# Example:
# clr.AddReference("RevitAPI")            # type: ignore
# clr.AddReference("RevitAPIUI")          # type: ignore
# clr.AddReference("System")              # type: ignore

# Example .NET imports (only if needed)
# from Autodesk.Revit import DB, UI       # type: ignore
# from System.Collections.Generic import List  # type: ignore

doc = revit.doc
uidoc = revit.uidoc


def main():
    # core logic here
    pass


if __name__ == "__main__":
    main()
```

Guidelines:

- Always import `revit`, `forms`, and `script` from `pyrevit` when relevant.
- Use `doc = revit.doc` and `uidoc = revit.uidoc` for convenience.
- Wrap execution in a `main()` and call it in the `if __name__ == "__main__":` block.

---
## 5. UI: Use pyRevit.forms only

Do **not** use WPF or WinForms for these mini-tools.
Use `pyrevit.forms` helpers (these are IronPython/WPF based, so stick with the default IronPython engine):
Common functions:
- `forms.alert(message, title=None, warn_icon=True)`
- `forms.ask_for_string(prompt, default=None, title=None)`
- `forms.SelectFromList.show(...)`

> **Note:** Some helpers (e.g., `forms.ask_for_number`) aren’t available in every pyRevit build. Prefer `forms.ask_for_string` and convert to numbers yourself for maximum compatibility.

### Example: simple string input

```python
value = forms.ask_for_string(
    prompt="Enter the number of copies:",
    default="10",
    title="Array Along Path"
)

if value is None:
    return  # user cancelled
```

### Example: number input with validation

```python
raw = forms.ask_for_string("How many instances?", default="5", title="Array Along Path")
if raw is None:
    return

try:
    count = int(raw)
    if count < 1:
        raise ValueError
except Exception:
    forms.alert("Invalid number entered. Please enter an integer greater than 0.")
    return
```

### Example: choice selection

```python
options = ["Option A", "Option B", "Option C"]

selected = forms.SelectFromList.show(
    options,
    title="Choose an option",
    button_name="OK"
)

if not selected:
    return  # user cancelled
```

Use these patterns instead of building custom UI.

---

## 6. Dealing with .NET and IronPython quirks

### 6.1 .NET imports must be correctly cased

Namespaces are PascalCase, e.g.:

- `System`
- `System.Windows`
- `System.Collections.Generic`

Examples:

```python
import clr  # type: ignore
clr.AddReference("System")  # type: ignore

from System.Collections.Generic import List  # type: ignore
```

Wrong examples (don’t generate):

- `from system.windows.generic import lists`
- `from system.collections import list`
### 6.2 Use “type: ignore” as a band-aid

Because CPython + VSCode can’t fully understand `clr`-based imports, we allow noisy imports to be patched like this:

```python
import clr  # type: ignore
clr.AddReference("PresentationFramework")  # type: ignore

from Autodesk.Revit import DB  # type: ignore
from System.Collections.Generic import List  # type: ignore
```

Use `# type: ignore` generously for:

- `import clr`
- `clr.AddReference(...)`
- `from Autodesk.Revit import DB, UI`
- Other .NET imports that confuse the analyzer

This is intentional and acceptable.

### 6.3 Optional: TYPE_CHECKING guard

If you want cleaner patterns:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from Autodesk.Revit import DB, UI  # type: ignore
else:
    import clr  # type: ignore
    clr.AddReference("RevitAPI")   # type: ignore
    clr.AddReference("RevitAPIUI") # type: ignore
    from Autodesk.Revit import DB, UI  # type: ignore
```

Use this only when it makes the script more readable; don’t overdo it.

---

## 7. Transactions and selection patterns

### 7.1 Transactions

Whenever modifying the model, use pyRevit’s context manager:

```python
from pyrevit import revit

with revit.Transaction("My Tool Name"):
    # Revit API calls that modify the document
    pass
```

Name the transaction something descriptive but short.

### 7.2 Selection patterns

Common patterns:
- Pick one element:

```python
from Autodesk.Revit.DB import Selection  # type: ignore

ref = uidoc.Selection.PickObject(
    Selection.ObjectType.Element,
    "Select an element"
)
elem = doc.GetElement(ref.ElementId)
```

- Use current selection:

```python
sel_ids = uidoc.Selection.GetElementIds()
if not sel_ids:
    forms.alert("Please select at least one element first.")
    return

elements = [doc.GetElement(eid) for eid in sel_ids]
```

Validate:

- If selection is empty → show `forms.alert`.
- If element type is wrong → show `forms.alert` and `return`.

---

## 8. Error handling philosophy

These are internal tools. We don’t need elaborate exception handling.

Rules:

- Validate user input early.
- If something is obviously wrong (no selection, wrong type, bad number), show `forms.alert` and exit.
- Use bare `return` to abort the script cleanly.

Example:

```python
def main():
    sel_ids = uidoc.Selection.GetElementIds()
    if not sel_ids:
        forms.alert("Please select at least one family instance.")
        return

    elems = [doc.GetElement(eid) for eid in sel_ids]
    instances = [e for e in elems if isinstance(e, DB.FamilyInstance)]

    if not instances:
        forms.alert("No family instances found in selection.")
        return

    # do stuff with instances...
```

Avoid deep `try/except` unless interacting with something notoriously flaky. If you do catch exceptions, either:

- surface them in an alert, or
- log via `script.get_logger()` and show a generic “see console” message.

You **cannot** debug pyRevit scripts directly from VS Code.  
These scripts execute inside Revit’s IronPython environment, so breakpoints, step-through, and live inspection are unavailable.  
All debugging must happen through **logging** and **runtime inspection** inside Revit.

### 8.1 Logging setup

At the top of every script, initialize the logger and output console:

```python
from pyrevit import script  
logger = script.get_logger() 
output = script.get_output()
```

- `logger` writes to the Revit → pyRevit output panel.
- `output` allows richer Markdown or HTML output in that panel.
### 8.2 Debug pattern

Use `logger.debug()` liberally to trace state, inputs, and decision points:

```python
logger.debug("Starting script execution") 
logger.debug("Selected element ids: {}".format(
	[i.IntegerValue for i in uidoc.Selection.GetElementIds()] 
))
logger.debug("Curve start: {}".format(curve.GetEndPoint(0))) 
logger.debug("Curve end: {}".format(curve.GetEndPoint(1)))
```

When an error might occur, wrap the code block in a small `try/except`:

```python 
try:
     place_instances(curve, source, count) 
 except Exception as ex:
      logger.error("Error placing instances: {}".format(ex))     
      output.print_md("### Exception\n`{}`".format(ex))     
      raise```

This prints both structured log messages and a Markdown summary in the console.
### 8.3 Temporary “breakpoints”

Use quick alerts as breakpoints when needed:
```python
from pyrevit import forms 
forms.alert("Reached midpoint of script")```

or log them:

```python
logger.debug("Checkpoint: halfway through placement loop")
```

### 8.4 Dry-run mode (safe testing)

To test logic without modifying the model, define a dry-run flag:

```python
DEBUG_DRY_RUN = True
```

Wrap any Revit-modifying code:

```python
if DEBUG_DRY_RUN:
     logger.debug("Dry-run: would create {} elements.".format(count))
	 return
```

Set `DEBUG_DRY_RUN = False` to perform real changes once behavior is confirmed.

### 8.5 Rollback trick (optional)

When deeper validation is needed without leaving test geometry:

```python
tg = DB.TransactionGroup(doc, "Debug Session") 
tg.Start()

with revit.Transaction("Trial Run"):     
	# test modifications
    pass
    
tg.RollBack()  # undo everything after confirming no exceptions
```

### 8.6 Philosophy

- Use **logs, not breakpoints** — you debug by observing Revit output.
- Log early, log often; delete logs when the script is stable.
- Always prefer clear contextual messages (`"count={}, curve length={}"`) over generic “it failed.”
- Avoid `print()` — use `logger` instead so all output stays in pyRevit’s panel.

When something fails, the logs should tell you **what input** and **what step** caused it without needing a debugger.

---

## 9. Naming and structure for tools

Each tool = one pushbutton = one `script.py`.

### 9.1 General structure

```python
from pyrevit import revit, forms, script
import clr  # type: ignore

# Add references & imports as needed
# clr.AddReference("RevitAPI")  # type: ignore
# from Autodesk.Revit import DB  # type: ignore

doc = revit.doc
uidoc = revit.uidoc
logger = script.get_logger()


def main():
    # 1. Get selection or ask for input
    # 2. Validate input
    # 3. Run core logic within a transaction (if model changes)
    # 4. Notify user on success/failure
    pass


if __name__ == "__main__":
    main()
```

### 9.2 Example: “Array Along Path” tool (simplified)

This is a good reference pattern for similar “mini-solutions”:

```python
from pyrevit import revit, forms
import clr  # type: ignore

clr.AddReference("RevitAPI")    # type: ignore
from Autodesk.Revit import DB   # type: ignore

doc = revit.doc
uidoc = revit.uidoc


def pick_curve():
    ref = uidoc.Selection.PickObject(
        DB.Selection.ObjectType.Element,
        "Pick a model line or curve"
    )
    elem = doc.GetElement(ref.ElementId)
    loc_crv = elem.Location

    if not isinstance(loc_crv, DB.LocationCurve):
        forms.alert("Selected element is not a curve-like element.")
        return None

    return loc_crv.Curve


def pick_family_instance():
    ref = uidoc.Selection.PickObject(
        DB.Selection.ObjectType.Element,
        "Pick a family instance to array along path"
    )
    elem = doc.GetElement(ref.ElementId)
    if not isinstance(elem, DB.FamilyInstance):
        forms.alert("Selected element is not a family instance.")
        return None
    return elem


def ask_for_count():
    raw = forms.ask_for_string(
        prompt="How many instances along the path?",
        default="10",
        title="Array Along Path"
    )
    if raw is None:
        return None

    try:
        count = int(raw)
        if count < 2:
            raise ValueError
        return count
    except Exception:
        forms.alert("Invalid count. Enter an integer >= 2.")
        return None


def place_instances(curve, source_instance, count):
    symbol = source_instance.Symbol
    level = doc.GetElement(source_instance.LevelId)

    # Equal spacing between start and end parameters
    t_step = 1.0 / (count - 1)

    with revit.Transaction("Array Along Path"):
        for i in range(count):
            t = i * t_step
            point = curve.Evaluate(t, True)
            doc.Create.NewFamilyInstance(
                point,
                symbol,
                level,
                DB.Structure.StructuralType.NonStructural
            )


def main():
    curve = pick_curve()
    if not curve:
        return

    source = pick_family_instance()
    if not source:
        return

    count = ask_for_count()
    if not count:
        return

    place_instances(curve, source, count)


if __name__ == "__main__":
    main()
```

This is the **ideal complexity level** for these tools:

- Small, focused, readable
- pyRevit.forms for input
- Minimal .NET imports
- Transaction wrapper only where needed
---
## 10. When to suggest more structure

Most of the time: **don’t**. Only suggest or introduce more structure when:
- The same helper logic (e.g., “get all selected family instances”, “filter elements by category”) is clearly repeated across multiple tools.
- The user explicitly asks to start building a shared utilities modules

If that happens, propose a minimal `lib/utils.py` module and keep it tiny. Still don’t go full framework.

---
## 11. Summary of key rules

1. These are **quick-and-dirty internal tools**, not products.
2. Prefer **single-file** `script.py` per pushbutton.
3. Use **`pyrevit.forms`** for all UI:
    - No WPF
    - No WinForms
4. Use **.NET imports** carefully, with correct casing and namespaces.
5. Use `# type: ignore` as a **valid band-aid** for clr/.NET imports.
6. Use `with revit.Transaction("Name"):` for all model changes.
7. Validate input and show simple alerts instead of throwing errors in the user’s face.
8. Favor **clarity and speed** over abstraction and cleverness.

If you follow this, you’re behaving exactly how this repo’s owner wants their “mini-solutions” to be built.
