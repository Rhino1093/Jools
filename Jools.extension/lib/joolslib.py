"""Shared helpers for Jools pyRevit tools.

pyRevit puts `<extension>/lib` on sys.path automatically
(`pyrevit/extensions/__init__.py`: COMP_LIBRARY_DIR_NAME = 'lib'), so any
script in this extension can `import joolslib` with no path setup.

IMPORTANT: this module must NOT import pyrevit at module scope. install_events_shim()
has to run *before* the first `from pyrevit import ...`, and that is impossible if
importing joolslib pulls pyrevit in first. Anything needing pyrevit takes it as an
argument instead.

Usage in a new tool:

    import joolslib
    joolslib.install_events_shim()      # before any pyrevit import
    from pyrevit import revit, script
"""

import sys
from types import ModuleType


def install_events_shim():
    """///Summary: Pre-empt pyrevit.revit.events, which can fail under CPython.

    `from pyrevit import script` reaches pyrevit.revit, which imports
    pyrevit.revit.events; that module subclasses a .NET interface at module
    scope and can raise "interface takes exactly one argument" under pythonnet.
    Registering a stub in sys.modules first makes the import a no-op.

    Safe to call unconditionally and more than once. Returns True if a stub was
    installed, False if the real module was already imported.
    """
    if 'pyrevit.revit.events' in sys.modules:
        return False
    stub = ModuleType('pyrevit.revit.events')
    stub._HANDLER = None
    sys.modules['pyrevit.revit.events'] = stub
    return True


def unique_namespace(prefix="Jools"):
    """///Summary: A per-execution __namespace__ for .NET interface implementations.

    pythonnet emits a proxy type named <__namespace__>.<ClassName> into a dynamic
    assembly that lives for the whole Revit session. A fixed __namespace__ therefore
    raises "Duplicate type name within an assembly" the second time the tool runs.

    pyRevit solves this with EXEC_PARAMS.exec_id, which the C# executor injects fresh
    per run (see pyrevit/revit/events.py). Falls back to a uuid if that is absent.

        _NS = joolslib.unique_namespace("ArrayOnPath")

        class MyFilter(UI.Selection.ISelectionFilter):
            __namespace__ = _NS
    """
    exec_id = None
    try:
        from pyrevit import EXEC_PARAMS  # imported late; see module docstring
        exec_id = EXEC_PARAMS.exec_id
    except Exception:
        pass
    if not exec_id:
        import uuid
        exec_id = uuid.uuid4().hex
    return "{}_{}".format(prefix, str(exec_id).replace("-", ""))


def eid_int(element_id):
    """///Summary: An ElementId's integer value, valid in Revit 2022-2026.

    Autodesk added ElementId.Value in 2024 and removed ElementId.IntegerValue
    in 2026, so neither attribute alone covers every Revit this extension is
    attached to. Written for both CPython 3 and IronPython 2.7.
    """
    value = getattr(element_id, "Value", None)
    return int(value) if value is not None else element_id.IntegerValue


def alert(message, title="Jools"):
    """///Summary: Message box that works on every engine.

    Replaces forms.alert, which raises PyRevitCPythonNotSupported under
    `#! python3` (CLAUDE.md section 2.2).
    """
    from Autodesk.Revit.UI import TaskDialog  # type: ignore
    TaskDialog.Show(title, message)


def ask_for_string(prompt, default="", title="Input"):
    """///Summary: Single-line text prompt. Replaces forms.ask_for_string.

    WinForms rather than WPF, so it matches the FolderBrowserDialog/DialogResult
    convention already used across this extension. Returns the entered text, or
    None if the user cancels.
    """
    import clr  # type: ignore
    clr.AddReference('System.Windows.Forms')  # type: ignore
    clr.AddReference('System.Drawing')        # type: ignore
    from System.Windows.Forms import (Form, Label, TextBox, Button, DialogResult,
                                      FormStartPosition, FormBorderStyle)  # type: ignore
    from System.Drawing import Size, Point  # type: ignore

    form = Form()
    form.Text = title
    form.ClientSize = Size(620, 120)
    form.FormBorderStyle = FormBorderStyle.FixedDialog
    form.StartPosition = FormStartPosition.CenterScreen
    form.MinimizeBox = False
    form.MaximizeBox = False

    label = Label()
    label.Text = prompt
    label.Location = Point(12, 12)
    label.AutoSize = True
    form.Controls.Add(label)

    textbox = TextBox()
    textbox.Text = default or ""
    textbox.Location = Point(12, 38)
    textbox.Size = Size(596, 24)
    form.Controls.Add(textbox)

    ok = Button()
    ok.Text = "OK"
    ok.DialogResult = DialogResult.OK
    ok.Location = Point(452, 78)
    form.Controls.Add(ok)

    cancel = Button()
    cancel.Text = "Cancel"
    cancel.DialogResult = DialogResult.Cancel
    cancel.Location = Point(533, 78)
    form.Controls.Add(cancel)

    form.AcceptButton = ok
    form.CancelButton = cancel

    if form.ShowDialog() != DialogResult.OK:
        return None
    return textbox.Text


class OutputProgress(object):
    """///Summary: Drop-in replacement for forms.ProgressBar, CPython-safe.

    Built on the pyRevit output window's progress bar, which works on every
    engine. Closing the output window stands in for the old cancel button, so
    a `while not pb.cancelled` loop keeps working.

    Pass the object from script.get_output():

        output = script.get_output()
        with joolslib.OutputProgress(output, "Working...", len(items)) as pb:
            for i, item in enumerate(items):
                if pb.cancelled:
                    break
                pb.update_progress(i + 1)
    """

    def __init__(self, output, title, total):
        self._output = output
        self._title = title
        self._total = max(1, total)

    def __enter__(self):
        self._output.set_title(self._title)
        self._output.update_progress(0, self._total)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._output.reset_progress()
        return False

    @property
    def cancelled(self):
        """True once the user closes the output window."""
        return self._output.is_closed_by_user

    def update_progress(self, current, total=None):
        self._output.update_progress(current, total or self._total)
