#! python3
"""Upgrade a folder of Revit models to the running Revit version, in place."""

import os
import re
import shutil
import time

import joolslib          # Jools.extension/lib, see CLAUDE.md section 6a
joolslib.install_events_shim()   # must precede every pyrevit import (section 2.3)

import clr  # type: ignore
clr.AddReference("RevitAPI")              # type: ignore
clr.AddReference("RevitAPIUI")            # type: ignore
clr.AddReference("PresentationFramework") # type: ignore
clr.AddReference("PresentationCore")      # type: ignore
clr.AddReference("WindowsBase")           # type: ignore
clr.AddReference("System.Xml")            # type: ignore
clr.AddReference("System.Windows.Forms")  # type: ignore

from Autodesk.Revit import DB                                   # type: ignore
from Autodesk.Revit.DB.Events import FailuresProcessingEventArgs  # type: ignore
from Autodesk.Revit.UI import (TaskDialog, TaskDialogCommonButtons,
                               TaskDialogResult)                # type: ignore
from Autodesk.Revit.UI.Events import DialogBoxShowingEventArgs  # type: ignore
from System import EventHandler                                 # type: ignore
from System.IO import StringReader                              # type: ignore
from System.Xml import XmlReader                                # type: ignore
from System.Windows import FontStyles, Thickness               # type: ignore
from System.Windows.Controls import CheckBox, StackPanel, TextBlock, Orientation  # type: ignore
from System.Windows.Markup import XamlReader                    # type: ignore
from System.Windows.Forms import FolderBrowserDialog, DialogResult  # type: ignore

from pyrevit import script

__author__ = "Ryan Johnston"

uiapp = __revit__                 # type: ignore
app = uiapp.Application
output = script.get_output()
logger = script.get_logger()

TOOL_TITLE = "Bulk RVT Upgrade"
CURRENT_VERSION = int(app.VersionNumber)

# Revit's numbered backups (Model.0001.rvt) are old snapshots, never upgrade targets.
BACKUP_FILE_RE = re.compile(r"\.\d{4}\.rvt$", re.IGNORECASE)

# Standalone models are saved here first, then swapped over the original, so a
# failed save never leaves a half-written file where the original was.
TEMP_SUFFIX = ".jools_upgrade_tmp.rvt"

# Folders that hold copies, not live models. Upgrading these would be wrong.
SKIP_DIR_PREFIXES = ("_preupgrade_",)
SKIP_DIR_SUFFIXES = ("_backup",)          # worksharing backup folders
SKIP_DIR_NAMES = ("revit_temp",)

# Dialogs where "OK" is the wrong answer. Every dialog that gets dismissed is
# logged with its DialogId, so add entries here when the log shows one.
#   TaskDialog_Unresolved_References: 1002 = "Ignore and continue opening".
#   1001 would be "Open Manage Links", which opens a modal and stalls the batch.
KNOWN_DIALOG_RESULTS = {
    "TaskDialog_Unresolved_References": 1002,
}
# Tried in order for anything not listed above: OK, Close, Cancel.
# (Same numbers for TaskDialogResult and the Win32 IDOK/IDCLOSE/IDCANCEL codes.)
FALLBACK_DIALOG_RESULTS = (1, 8, 2)


# --------------------------------------------------------------------------
# File discovery
# --------------------------------------------------------------------------

class ModelFile(object):
    """///Summary: One .rvt on disk, its saved version, and whether it can be upgraded."""

    def __init__(self, path, root, open_paths):
        """///Summary: Read the file header and decide if this file is a valid target."""
        self.path = path
        self.rel_path = os.path.relpath(path, root)
        self.version = "?"
        self.kind = "Standalone"
        self.reason = None        # None means upgradable; otherwise why not
        self._inspect(open_paths)

    @property
    def is_upgradable(self):
        """///Summary: True when nothing rules this file out."""
        return self.reason is None

    @property
    def is_central(self):
        """///Summary: True for a workshared central (or a copy of one)."""
        return self.kind == "Central"

    def _inspect(self, open_paths):
        """///Summary: Fill version/kind/reason from BasicFileInfo without opening the model."""
        try:
            info = DB.BasicFileInfo.Extract(self.path)
        except Exception as ex:
            self.reason = "Can't read file header ({})".format(ex)
            return

        self.version = info.Format or "?"
        if info.IsWorkshared:
            # A workshared file that is not a local is treated as a central,
            # which also covers a central copied to a new folder.
            self.kind = "Local" if info.IsLocal else "Central"

        if os.path.normcase(self.path) in open_paths:
            self.reason = "Open in this Revit session - close it first"
        elif info.IsSavedInLaterVersion:
            self.reason = "Newer than Revit {}".format(CURRENT_VERSION)
        elif info.IsSavedInCurrentVersion:
            self.reason = "Already Revit {}".format(CURRENT_VERSION)
        elif self.kind == "Local":
            self.reason = "Local copy - upgrade its central instead"
        elif not os.access(self.path, os.W_OK):
            self.reason = "Read-only file"


def _skip_dir(name):
    """///Summary: True for folders that hold backups/copies rather than live models."""
    lower = name.lower()
    return (lower.startswith(SKIP_DIR_PREFIXES)
            or lower.endswith(SKIP_DIR_SUFFIXES)
            or lower in SKIP_DIR_NAMES)


def _is_candidate(file_name):
    """///Summary: True for an .rvt that is not a Revit backup or one of our temp files."""
    lower = file_name.lower()
    return (lower.endswith(".rvt")
            and not lower.endswith(TEMP_SUFFIX)
            and not BACKUP_FILE_RE.search(lower))


def scan_folder(folder, recursive):
    """///Summary: Every candidate .rvt under folder, inspected and sorted by path."""
    # Models already open here can't be opened a second time in the background.
    open_paths = set()
    for d in app.Documents:
        if d.PathName:
            open_paths.add(os.path.normcase(d.PathName))

    paths = []
    if recursive:
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if not _skip_dir(d)]
            paths.extend(os.path.join(root, f) for f in files if _is_candidate(f))
    else:
        paths = [os.path.join(folder, f) for f in os.listdir(folder)
                 if _is_candidate(f) and os.path.isfile(os.path.join(folder, f))]

    models = [ModelFile(p, folder, open_paths) for p in paths]
    models.sort(key=lambda m: m.rel_path.lower())
    return models


def pick_folder(start_path=None):
    """///Summary: Folder picker. Returns the chosen path, or None on cancel."""
    dialog = FolderBrowserDialog()
    dialog.Description = "Pick the folder that holds the Revit models to upgrade"
    if start_path and os.path.isdir(start_path):
        dialog.SelectedPath = start_path
    if dialog.ShowDialog() != DialogResult.OK:
        return None
    return dialog.SelectedPath


# --------------------------------------------------------------------------
# File-selection window
# --------------------------------------------------------------------------

XAML = """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Bulk RVT Upgrade" Height="620" Width="820" MinHeight="420" MinWidth="600"
        WindowStartupLocation="CenterScreen" Background="#1E1E1E" Foreground="#FFFFFF"
        FontFamily="Segoe UI" FontSize="12">
    <Window.Resources>
        <Style TargetType="CheckBox">
            <Setter Property="Foreground" Value="#FFFFFF"/>
        </Style>
        <Style TargetType="Button">
            <Setter Property="Background" Value="#3E3E42"/>
            <Setter Property="Foreground" Value="#FFFFFF"/>
            <Setter Property="BorderBrush" Value="#3F3F46"/>
            <Setter Property="Padding" Value="10,3"/>
            <Setter Property="Margin" Value="6,0,0,0"/>
        </Style>
    </Window.Resources>
    <Grid Margin="15">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>

        <StackPanel Grid.Row="0" Margin="0,0,0,10">
            <TextBlock Name="txtTitle" FontSize="18" FontWeight="Bold" Foreground="#3A96DD"/>
            <TextBlock Text="Ticked files are upgraded and OVERWRITTEN in place. Upgraded files can't be opened in older Revit."
                       Foreground="#E8A33D" Margin="0,2,0,0" TextWrapping="Wrap"/>
        </StackPanel>

        <Grid Grid.Row="1" Margin="0,0,0,8">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>
            <TextBlock Name="txtFolder" Grid.Column="0" VerticalAlignment="Center"
                       TextTrimming="CharacterEllipsis" Foreground="#BBBBBB"/>
            <CheckBox Name="chkRecursive" Grid.Column="1" Content="Include subfolders"
                      VerticalAlignment="Center" Margin="10,0,0,0"/>
            <Button Name="btnFolder" Grid.Column="2" Content="Change Folder..."/>
        </Grid>

        <Border Grid.Row="2" BorderBrush="#3F3F46" BorderThickness="1" Background="#252526">
            <ScrollViewer VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Auto">
                <StackPanel Name="lstFiles" Margin="6"/>
            </ScrollViewer>
        </Border>

        <Grid Grid.Row="3" Margin="0,8,0,8">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>
            <TextBlock Name="txtCount" Grid.Column="0" VerticalAlignment="Center" Foreground="#BBBBBB"/>
            <Button Name="btnAll" Grid.Column="1" Content="Select All"/>
            <Button Name="btnNone" Grid.Column="2" Content="Select None"/>
        </Grid>

        <StackPanel Grid.Row="4" Margin="0,0,0,12">
            <CheckBox Name="chkBackup" Margin="0,2" IsChecked="True"
                      Content="Copy each original to a _PreUpgrade_&lt;year&gt; folder before overwriting (recommended)"/>
            <CheckBox Name="chkAudit" Margin="0,2"
                      Content="Audit while opening (slower; repairs minor corruption)"/>
            <CheckBox Name="chkSkipLinks" Margin="0,2"
                      Content="Don't load Revit links while upgrading (faster; test on one file first)"/>
            <CheckBox Name="chkCloseWorksets" Margin="0,2"
                      Content="Close all worksets while upgrading centrals (faster)"/>
        </StackPanel>

        <StackPanel Grid.Row="5" Orientation="Horizontal" HorizontalAlignment="Right">
            <Button Name="btnUpgrade" Content="Upgrade" IsDefault="True" FontWeight="Bold" Background="#0E639C"/>
            <Button Name="btnCancel" Content="Cancel" IsCancel="True"/>
        </StackPanel>
    </Grid>
</Window>
"""


class UpgradeOptions(object):
    """///Summary: The option checkboxes, read once when the user clicks Upgrade."""

    def __init__(self, backup, audit, skip_links, close_worksets):
        self.backup = backup
        self.audit = audit
        self.skip_links = skip_links
        self.close_worksets = close_worksets


class UpgradeWindow(object):
    """///Summary: Lists the .rvt files in a folder so the user can tick which to upgrade."""

    def __init__(self, folder):
        """///Summary: Build the window and fill it from folder."""
        self.folder = folder
        self.models = []
        self.rows = []            # (CheckBox, ModelFile) for upgradable files only
        self.selected = None      # list of ModelFile once the user confirms
        self.options = None

        self.window = XamlReader.Load(XmlReader.Create(StringReader(XAML)))
        find = self.window.FindName
        self.txt_title = find("txtTitle")
        self.txt_folder = find("txtFolder")
        self.txt_count = find("txtCount")
        self.lst_files = find("lstFiles")
        self.chk_recursive = find("chkRecursive")
        self.chk_backup = find("chkBackup")
        self.chk_audit = find("chkAudit")
        self.chk_skip_links = find("chkSkipLinks")
        self.chk_close_ws = find("chkCloseWorksets")

        self.txt_title.Text = "Upgrade Revit models to Revit {}".format(CURRENT_VERSION)

        find("btnFolder").Click += self._on_change_folder
        find("btnAll").Click += lambda s, e: self._set_all(True)
        find("btnNone").Click += lambda s, e: self._set_all(False)
        find("btnUpgrade").Click += self._on_upgrade
        find("btnCancel").Click += lambda s, e: self.window.Close()
        self.chk_recursive.Click += lambda s, e: self._refresh()

        self._refresh()

    def show(self):
        """///Summary: Show modally. Afterwards .selected is None if cancelled."""
        self.window.ShowDialog()

    def _refresh(self):
        """///Summary: Rescan the folder and rebuild the file list."""
        self.txt_folder.Text = self.folder
        self.models = scan_folder(self.folder, self.chk_recursive.IsChecked == True)
        self.rows = []
        self.lst_files.Children.Clear()

        for model in self.models:
            # Rows are built in code rather than bound, so nothing here relies on
            # pythonnet turning a Python list into a WPF ItemsSource (CLAUDE.md section 3).
            box = CheckBox()
            box.Margin = Thickness(0, 2, 0, 2)
            panel = StackPanel()
            panel.Orientation = Orientation.Horizontal
            panel.Children.Add(self._cell(model.version, 50))
            panel.Children.Add(self._cell(model.kind, 85))
            panel.Children.Add(self._cell(model.rel_path, None))
            if model.reason:
                note = self._cell("   - " + model.reason, None)
                note.FontStyle = FontStyles.Italic
                panel.Children.Add(note)
            box.Content = panel

            if model.is_upgradable:
                box.IsChecked = True
                box.Click += lambda s, e: self._update_count()
                self.rows.append((box, model))
            else:
                box.IsChecked = False
                box.IsEnabled = False
                box.Opacity = 0.45
            self.lst_files.Children.Add(box)

        if not self.models:
            empty = TextBlock()
            empty.Text = "No .rvt files found here. Change the folder or tick 'Include subfolders'."
            self.lst_files.Children.Add(empty)
        self._update_count()

    @staticmethod
    def _cell(text, width):
        """///Summary: A fixed-width text column for one file row."""
        block = TextBlock()
        block.Text = text
        if width:
            block.Width = width
        return block

    def _set_all(self, state):
        """///Summary: Tick or untick every upgradable file."""
        for box, _model in self.rows:
            box.IsChecked = state
        self._update_count()

    def _checked_models(self):
        """///Summary: Upgradable files the user has ticked."""
        return [model for box, model in self.rows if box.IsChecked == True]

    def _update_count(self):
        """///Summary: Refresh the 'n of m selected' line."""
        self.txt_count.Text = "{} of {} upgradable selected  ({} files found)".format(
            len(self._checked_models()), len(self.rows), len(self.models))

    def _on_change_folder(self, sender, args):
        """///Summary: Pick another folder and rescan."""
        folder = pick_folder(self.folder)
        if folder:
            self.folder = folder
            self._refresh()

    def _on_upgrade(self, sender, args):
        """///Summary: Validate the selection, confirm the overwrite, and close."""
        chosen = self._checked_models()
        if not chosen:
            TaskDialog.Show(TOOL_TITLE, "Tick at least one file to upgrade, then click Upgrade.")
            return

        backup = self.chk_backup.IsChecked == True
        confirm = TaskDialog(TOOL_TITLE)
        confirm.MainInstruction = "Upgrade {} file(s) to Revit {}?".format(len(chosen), CURRENT_VERSION)
        confirm.MainContent = (
            "The originals are overwritten and can't be opened in older Revit again.\n\n"
            + ("Backup copies will be kept in _PreUpgrade_<year> folders."
               if backup else "No backup copies will be made.")
            + "\n\nRevit is unusable until the batch finishes.")
        confirm.CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No
        confirm.DefaultButton = TaskDialogResult.No
        if confirm.Show() != TaskDialogResult.Yes:
            return

        self.selected = chosen
        self.options = UpgradeOptions(
            backup=backup,
            audit=self.chk_audit.IsChecked == True,
            skip_links=self.chk_skip_links.IsChecked == True,
            close_worksets=self.chk_close_ws.IsChecked == True)
        self.window.Close()


# --------------------------------------------------------------------------
# Dialog / warning suppression
# --------------------------------------------------------------------------

class DialogSilencer(object):
    """///Summary: Auto-answers Revit dialogs and warnings for the duration of a with-block.

    Two separate Revit mechanisms can stop a batch:
      * DialogBoxShowing - modal message boxes and task dialogs.
      * FailuresProcessing - the warning/error list a transaction or upgrade raises.
    Both are subscribed on entry and always removed on exit, so normal Revit
    behaviour comes back even if the batch crashes.

    Dialogs that don't go through Revit's UI layer (some third-party add-ins,
    Windows file-access prompts) can't be caught by either event.
    """

    def __init__(self):
        self.dismissed = []       # DialogIds dismissed since the last reset()
        # Explicit delegates so the exact same object can be removed in __exit__.
        self._dialog_handler = EventHandler[DialogBoxShowingEventArgs](self._on_dialog)
        self._failure_handler = EventHandler[FailuresProcessingEventArgs](self._on_failures)

    def __enter__(self):
        uiapp.DialogBoxShowing += self._dialog_handler
        app.FailuresProcessing += self._failure_handler
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        uiapp.DialogBoxShowing -= self._dialog_handler
        app.FailuresProcessing -= self._failure_handler
        return False

    def reset(self):
        """///Summary: Clear the per-file list of dismissed dialogs."""
        self.dismissed = []

    def _on_dialog(self, sender, args):
        """///Summary: Answer a dialog with the safest 'carry on' result.

        Must never raise: an exception escaping a Revit event handler can leave
        the dialog showing, which is exactly what this exists to prevent.
        """
        try:
            dialog_id = args.DialogId or "(no id)"
            codes = ([KNOWN_DIALOG_RESULTS[dialog_id]] if dialog_id in KNOWN_DIALOG_RESULTS
                     else list(FALLBACK_DIALOG_RESULTS))
            for code in codes:
                try:
                    if args.OverrideResult(code):
                        self.dismissed.append(dialog_id)
                        logger.info("Dismissed dialog %s with result %s", dialog_id, code)
                        return
                except Exception:
                    continue
            logger.warning("Could not dismiss dialog %s", dialog_id)
        except Exception as ex:
            logger.warning("Dialog handler error: %s", ex)

    def _on_failures(self, sender, args):
        """///Summary: Delete warnings and apply Revit's default fix to errors.

        Warnings are informational, so deleting them changes nothing in the
        model. Errors get their default resolution (usually 'delete element' or
        'unjoin'), which is what clicking the default button in Revit would do.
        """
        try:
            accessor = args.GetFailuresAccessor()
            resolved_any = False
            for message in accessor.GetFailureMessages():
                if message.GetSeverity() == DB.FailureSeverity.Warning:
                    accessor.DeleteWarning(message)
                elif message.HasResolutions():
                    accessor.ResolveFailure(message)
                    resolved_any = True
            # ProceedWithCommit makes Revit re-check after our resolutions;
            # Continue is correct when only warnings were removed.
            args.SetProcessingResult(
                DB.FailureProcessingResult.ProceedWithCommit if resolved_any
                else DB.FailureProcessingResult.Continue)
        except Exception as ex:
            logger.warning("Failure handler error: %s", ex)


# --------------------------------------------------------------------------
# Upgrade
# --------------------------------------------------------------------------

def backup_original(model):
    """///Summary: Copy the pre-upgrade file to _PreUpgrade_<year> beside it."""
    folder = os.path.join(os.path.dirname(model.path), "_PreUpgrade_{}".format(model.version))
    if not os.path.isdir(folder):
        os.makedirs(folder)
    shutil.copy2(model.path, os.path.join(folder, os.path.basename(model.path)))


def build_open_options(model, options):
    """///Summary: OpenOptions for a background, upgrade-on-open load of model."""
    open_opts = DB.OpenOptions()
    open_opts.Audit = options.audit
    open_opts.DoNotLoadLinks = options.skip_links
    if model.is_central:
        # Opening a central attached would take ownership of it and require a
        # sync; detaching lets us write it straight back as a fresh central.
        open_opts.DetachFromCentralOption = DB.DetachFromCentralOption.DetachAndPreserveWorksets
        ws_option = (DB.WorksetConfigurationOption.CloseAllWorksets if options.close_worksets
                     else DB.WorksetConfigurationOption.OpenAllWorksets)
        open_opts.SetOpenWorksetsConfiguration(DB.WorksetConfiguration(ws_option))
    return open_opts


def upgrade_model(model, options):
    """///Summary: Open model (Revit upgrades it on load), save it over itself, close it.

    Standalone files save to a temp name first and are swapped in only after
    Revit has finished writing, so a failure leaves the original untouched.
    Centrals must be saved at their real path (the central location is stored
    inside the file), so they are written in place as a new central.
    """
    if options.backup:
        backup_original(model)

    model_path = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(model.path)
    temp_path = model.path[:-4] + TEMP_SUFFIX

    doc = app.OpenDocumentFile(model_path, build_open_options(model, options))
    try:
        save_opts = DB.SaveAsOptions()
        save_opts.OverwriteExistingFile = True
        save_opts.Compact = True
        if doc.IsWorkshared:
            ws_opts = DB.WorksharingSaveAsOptions()
            ws_opts.SaveAsCentral = True
            save_opts.SetWorksharingOptions(ws_opts)
            doc.SaveAs(model.path, save_opts)
        else:
            doc.SaveAs(temp_path, save_opts)
    finally:
        doc.Close(False)

    if os.path.exists(temp_path):
        os.replace(temp_path, model.path)


def run_batch(models, options):
    """///Summary: Upgrade each model, keep going past failures, and print a summary."""
    output.print_md("## {} -> Revit {}".format(TOOL_TITLE, CURRENT_VERSION))
    rows = []
    batch_start = time.time()

    with DialogSilencer() as silencer, \
            joolslib.OutputProgress(output, TOOL_TITLE, len(models)) as progress:
        for index, model in enumerate(models):
            if progress.cancelled:
                rows.append([model.rel_path, model.version, "Not run", "", "Batch cancelled"])
                continue

            output.print_md("**{}/{}** Upgrading `{}` (Revit {}, {})...".format(
                index + 1, len(models), model.rel_path, model.version, model.kind))
            silencer.reset()
            start = time.time()
            try:
                upgrade_model(model, options)
                status, detail = "Upgraded", ""
            except Exception as ex:
                # First line only: Revit exception text can run to a paragraph.
                message = str(ex).strip()
                status, detail = "FAILED", (message.splitlines()[0] if message else repr(ex))
                logger.error("%s failed: %s", model.path, ex)
            if silencer.dismissed:
                note = "Dismissed: " + ", ".join(sorted(set(silencer.dismissed)))
                detail = (detail + "; " + note) if detail else note

            rows.append([model.rel_path, model.version, status,
                         "{:.0f}s".format(time.time() - start), detail])
            progress.update_progress(index + 1)

    upgraded = sum(1 for r in rows if r[2] == "Upgraded")
    failed = sum(1 for r in rows if r[2] == "FAILED")
    output.print_md("---")
    output.print_md("**{} upgraded, {} failed, {} not run** in {:.1f} min.".format(
        upgraded, failed, len(rows) - upgraded - failed, (time.time() - batch_start) / 60.0))
    output.print_md(joolslib.markdown_table(
        rows, ["File", "From", "Result", "Time", "Notes"]))
    if failed:
        output.print_md("Failed files were not changed unless noted. "
                        "Open one manually in Revit to see the full error.")


def main():
    """///Summary: Pick folder -> tick files -> upgrade -> report."""
    folder = pick_folder()
    if not folder:
        return

    picker = UpgradeWindow(folder)
    picker.show()
    if not picker.selected:
        return

    run_batch(picker.selected, picker.options)


if __name__ == "__main__":
    main()
