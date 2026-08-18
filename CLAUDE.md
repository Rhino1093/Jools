# Jools pyRevit Extension — Agent Instructions

Single-user pyRevit extension. Each tool fills one specific gap in Revit.
Priority: **works reliably** > elegant architecture. One `script.py` per pushbutton.

This file is the **only** source of truth for how to write tools here.
`GEMINI.md` and `Jools.extension/AGENTS.md` are historical and now point back here.

---

## 1. Verified environment

Do not guess these. They were read off this machine.

| Thing | Value |
|---|---|
| pyRevit | 6.4.0 (`%APPDATA%\pyRevit-Master`) |
| CPython engine | 3.12.3 (`bin/cengines/CPY3123`), set via `cpyengine = 3123` |
| IronPython engines | 2.7.12 (default), 3.4.2 — both present but **not used by this repo** |
| Revit installed | 2019 – 2026 |
| Extension registered as | `userextensions` → this repo root |
| Local stubs | `Jools.extension/typings/RVT 20…25` (**no RVT 26 stubs**) |
| Live API docs | `C:\Program Files\Autodesk\Revit 2026\RevitAPI.xml` (13 MB, full member docs) |

---

## 2. Hard rules — violating these produces a tool that cannot run

### 2.1 Every script starts with exactly `#! python3`

pyRevit resolves the engine with a literal substring test
(`pyrevit/extensions/__init__.py`: `CPYTHON_HASHBANG = '#! python3'`, matched with `in` against line 1).

```python
#! python3        # ✅ only accepted form
```

Anything else silently falls back to **IronPython 2.7.12**:

```python
# !python3        # ❌ space in wrong place — 4 files in this repo have this bug
#!python3         # ❌ missing space
#! python          # ❌ explicitly IronPython
(no first line)   # ❌ IronPython
```

A wrong shebang plus an f-string is a `SyntaxError` at load time, with a stack trace
that points at the f-string and never mentions the shebang. This is the single most
expensive failure mode in this repo — **check line 1 first when a tool won't start.**

### 2.2 Never import `pyrevit.forms`

pyRevit 6.4 split `forms` into two backends (`pyrevit/forms/__init__.py`):

```python
if IRONPY:  from ._ipy import *   # the real implementation
else:       from ._cpy import *   # stubs that raise
```

Under CPython 3 the `_cpy` backend stubs 13 symbols to raise
`PyRevitCPythonNotSupported`, and a module-level `__getattr__` raises for
**everything else** — so `forms.alert`, `forms.select_views`, `forms.check_selection`,
`forms.ask_for_*`, `forms.SelectFromList`, `forms.ProgressBar`, `forms.pick_file`
all fail. There is no working subset. Use the replacements in §3.

Also: `forms.ask_for_number` does not exist in **any** engine. It is `ask_for_number_slider`.

### 2.3 Install the events shim before any `pyrevit` import

`from pyrevit import script` → `pyrevit/script.py:29` → `from pyrevit import revit`
→ `pyrevit/revit/__init__.py:26` → `from pyrevit.revit import events`, whose line 13
subclasses a .NET interface at module scope. Under pythonnet this can fail with
`interface takes exactly one argument`.

The shim must run **before the first `pyrevit` import**, because `script` pulls
`revit` in transitively:

```python
import sys
from types import ModuleType

# Pre-empt pyrevit.revit.events, which fails to import under CPython/pythonnet.
# Must precede every `from pyrevit import ...` — script.py imports revit transitively.
_mock = ModuleType('pyrevit.revit.events')
_mock._HANDLER = None
sys.modules['pyrevit.revit.events'] = _mock
```

Five scripts here already carry it. If a `#! python3` tool dies on its import block
with no line of your own code in the trace, this is why.

### 2.4 `ElementId.IntegerValue` is removed in Revit 2026

Verified absent from `Revit 2026\RevitAPI.xml`. Deprecated in 2024, gone in 2026.

```python
eid.IntegerValue    # ❌ AttributeError in Revit 2026
eid.Value           # ✅ Int64, available 2024+
```

Five scripts in this repo still use `IntegerValue`. Do not write new ones.

### 2.5 Never import Dynamo modules

`RevitServices`, `DocumentManager`, `DSCore`, `Revit.Elements`, `IN[0]`, `OUT =` are
**Dynamo**, not pyRevit. Those assemblies are not loaded in a pyRevit session.
When porting a Dynamo script, replace:

```python
doc = DocumentManager.Instance.CurrentDBDocument   # ❌ Dynamo
doc = __revit__.ActiveUIDocument.Document          # ✅ pyRevit
```

Note: `.dyn` files under a `.pushbutton` folder are fine — pyRevit runs those through
Dynamo itself. This rule is only about `.py` scripts.

---

## 3. UI — the CPython-safe toolkit

| Need | Use | Import |
|---|---|---|
| Alert / error / confirm | `TaskDialog.Show(title, msg)` | `from Autodesk.Revit.UI import TaskDialog` |
| Yes/No, command links | `TaskDialog(title)` + `AddCommandLink` | same |
| Open / save file | `OpenFileDialog`, `SaveFileDialog` | `from Microsoft.Win32 import OpenFileDialog` |
| Pick folder | `FolderBrowserDialog` | `clr.AddReference("System.Windows.Forms")` |
| Anything with >2 inputs | WPF window from a XAML string | see §4 |
| Progress bar | `output.update_progress(i, total)` | `script.get_output()` |
| Result summary | `output.print_table(...)` / `print_md(...)` | `script.get_output()` |

Rules:
- User-facing errors get a `TaskDialog` with **what went wrong and what to do about it** —
  never a bare traceback.
- `OpenFileDialog.ShowDialog()` returns a nullable bool. Compare with `== True`,
  not truthiness.
- `System.Windows.Forms.Form.ShowDialog()` returns `DialogResult`. Compare with
  `== DialogResult.OK`. Do not mix the two conventions in one script.
- Keep XAML as a triple-quoted string inside `script.py`. Self-contained beats tidy.

---

## 4. Canonical script template

```python
#! python3
"""One-line description of what this tool does."""

import sys
from types import ModuleType

# See CLAUDE.md §2.3 — must precede all pyrevit imports.
_mock = ModuleType('pyrevit.revit.events')
_mock._HANDLER = None
sys.modules['pyrevit.revit.events'] = _mock

import clr  # type: ignore
clr.AddReference("RevitAPI")     # type: ignore
clr.AddReference("RevitAPIUI")   # type: ignore

from Autodesk.Revit import DB              # type: ignore
from Autodesk.Revit.UI import TaskDialog   # type: ignore

from pyrevit import revit, script

__author__ = "Ryan Johnston"

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
logger = script.get_logger()

TOOL_TITLE = "Tool Name"


def get_targets():
    """Return validated elements, or None after telling the user why not."""
    ids = uidoc.Selection.GetElementIds()
    if not ids:
        TaskDialog.Show(TOOL_TITLE, "Select at least one element, then run again.")
        return None
    return [doc.GetElement(i) for i in ids]


def main():
    targets = get_targets()
    if not targets:
        return

    with revit.Transaction(TOOL_TITLE):
        for el in targets:
            pass

    output.print_md("**{}:** processed {} elements.".format(TOOL_TITLE, len(targets)))


if __name__ == "__main__":
    main()
```

Shape every tool as: **select/input → validate & bail early → transact → report.**

---

## 5. Transactions

```python
with revit.Transaction("Short Description"):   # preferred
    ...
```

Use `DB.Transaction(doc, name)` only when the target is not `revit.doc`
(a background-opened document, a linked doc) — several scripts here do that
for temp docs. Always use it as a context manager, or `Commit()`/`RollBack()` in
a `try/finally`.

Multiple related transactions that must undo as one → wrap in `DB.TransactionGroup`
and `Assimilate()`.

---

## 6. Bundle layout

```
Jools.extension/
  Jools.tab/
    bundle.yaml                    # layout: lists panels, no .Panel suffix
    MyPanel.Panel/
      bundle.yaml                  # title + layout: tool names, no suffix
      MyTool.pushbutton/
        bundle.yaml                # title, tooltip, help_url
        icon.png
        script.py
  lib/                             # auto-added to sys.path — shared helpers go here
  bin/
    active hooks/                  # hooks currently loaded
    inactive hooks/                # disabled, kept for reference
  typings/                         # stubs, gitignored except the submodule
```

Containers also in use: `.pulldown`, `.stack`, `.urlbutton`.
Folder names may contain spaces (`BIM Management.pulldown`) — quote paths in shell commands.

`bundle.yaml` keys used here: `title`, `tooltip`, `help_url`, `layout`, `hyperlink`,
`description`, `author`. Adding a new panel means editing `Jools.tab/bundle.yaml` too,
or it won't appear.

---

## 7. Debugging

You **cannot** attach a debugger. Revit hosts the interpreter; VS Code sees nothing.
Everything is logging plus re-running the button.

```python
logger = script.get_logger()
logger.debug("count=%s, curve_len=%s", count, curve.Length)
```

- `logger.debug` is hidden unless the pyRevit output window is in debug mode
  (Ctrl+Click the button). `logger.info` and up always show.
- Never `print()` — it bypasses the pyRevit output panel.
- Log **values**, not milestones. `"reached step 3"` teaches nothing;
  `"count=0, ids=[]"` finds the bug.
- Include a `DRY_RUN = True` constant on anything that writes to the model, and log
  what it *would* do.
- To inspect without leaving geometry behind: run inside a `TransactionGroup`
  and `RollBack()`.

Reloading after an edit: pyRevit **Reload** button, or Ctrl+Click a button to run
the latest source without a full reload.

---

## 8. Before you hand a script back

Run the validator — it catches every failure mode in §2 statically:

```powershell
python tools\validate_extension.py            # full report
python tools\validate_extension.py --quiet    # errors only
```

In VS Code: **Ctrl+Shift+B**, or Terminal → Run Task → *Validate pyRevit extension*.
Findings land in the Problems panel. Exit code is 1 on any error, so it also works
as a pre-commit hook.

Then check by hand:
1. Line 1 is exactly `#! python3`.
2. No `pyrevit.forms` import.
3. Events shim precedes all `pyrevit` imports.
4. No `IntegerValue`, no Dynamo imports.
5. Every early-exit path tells the user what to do next.
6. Model changes are inside a transaction.

---

## 9. Reference material — look it up, don't recall it

**Never write a Revit API call from memory.** Confirm it first:

```powershell
python tools\revit_api.py ElementId                  # type + every member, Revit 2026
python tools\revit_api.py ElementId.Value            # summary, params, returns, since
python tools\revit_api.py --exists Element.LookupParameter   # exit 0 / 1
python tools\revit_api.py --search "reference intersector"   # when you don't know the name
python tools\revit_api.py --diff ElementId 2024 2026         # what Autodesk removed
```

It reads `C:\Program Files\Autodesk\Revit <year>\RevitAPI.xml` — Autodesk's own
shipped documentation, present for 2022–2026 here. **A member absent from that file
does not exist in that version.** This is the authority; nothing else is.

Other sources, in descending trustworthiness:

- **`%APPDATA%\pyRevit-Master\pyrevitlib\pyrevit\`** — pyRevit's real source. When a
  pyRevit helper misbehaves under CPython, read the module. `forms/__init__.py`,
  `forms/_cpy.py`, and `compat.py` explain most surprises in §2.
- **`Jools.extension/typings/RVT 25/`** — Pylance stubs, one release behind. They
  still autocomplete members Autodesk deleted in 2026, `IntegerValue` included.
  Good for editor completion, **wrong for version questions.**
- Existing scripts as patterns: `ModelDiff` (file picker + graphic overrides),
  `EqualDist` (code-built WPF), `CopyViewTemplatesFromOtherProjects` (XAML string +
  background docs), `SaveToACC` (cloud model handling).

---

## 10. Style

- `///Summary`-style docstring on every class, method, and non-obvious function.
- Inline comments explain **why**, never what.
- `# type: ignore` freely on `clr`, `clr.AddReference`, and every `Autodesk.*` /
  `System.*` import — Pylance cannot resolve them and the noise is not worth fighting.
- No abstraction until the same logic appears in a third script; then put it in
  `Jools.extension/lib/`.
- Don't build a framework. If a tool grows past ~500 lines, that's a signal it
  should be two tools.
