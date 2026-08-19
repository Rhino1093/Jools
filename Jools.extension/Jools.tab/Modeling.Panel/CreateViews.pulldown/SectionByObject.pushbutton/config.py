#! python3
"""Shift+Click settings for Section by Object: section type and edge offset."""

import traceback

import clr  # type: ignore
clr.AddReference("RevitAPI")                # type: ignore
clr.AddReference("RevitAPIUI")              # type: ignore
clr.AddReference("PresentationFramework")   # type: ignore
clr.AddReference("PresentationCore")        # type: ignore
clr.AddReference("System.Xaml")             # type: ignore
clr.AddReference("WindowsBase")             # type: ignore

import System  # type: ignore
from System.Collections.Generic import List  # type: ignore
from System.Windows.Markup import XamlReader  # type: ignore

from Autodesk.Revit import DB  # type: ignore
from Autodesk.Revit.UI import TaskDialog  # type: ignore

import joolslib          # Jools.extension/lib, see CLAUDE.md section 6a
joolslib.install_events_shim()   # must precede any pyrevit import

from pyrevit import revit, script

__author__ = "Ryan Johnston"

doc = revit.doc
logger = script.get_logger()

TOOL_TITLE = "Section by Object settings"

# Must match script.py, which reads the same config section.
DEFAULT_OFFSET_FT = 1.0

XAML = """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Section by Object settings" Width="460" SizeToContent="Height"
        ResizeMode="NoResize" WindowStartupLocation="CenterScreen"
        Topmost="True" Background="#F0F0F0">
    <StackPanel Margin="15">
        <TextBlock Text="Section view type for new sections"
                   FontWeight="Bold" Margin="0,0,0,4"/>
        <ComboBox x:Name="TypeCombo" Height="26" Margin="0,0,0,14"/>

        <TextBlock Text="Offset beyond the object's edges"
                   FontWeight="Bold" Margin="0,0,0,4"/>
        <TextBlock Text="Added to each end of the section line and to the front and back of its depth."
                   TextWrapping="Wrap" Foreground="#555555" Margin="0,0,0,6"/>
        <StackPanel Orientation="Horizontal" Margin="0,0,0,16">
            <TextBox x:Name="OffsetBox" Width="70" Height="24"/>
            <TextBlock Text="inches" VerticalAlignment="Center" Margin="8,0,0,0"/>
        </StackPanel>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="BtnSave" Content="Save" Width="90" Height="30"
                    Margin="0,0,10,0" IsDefault="True"/>
            <Button x:Name="BtnCancel" Content="Cancel" Width="80" Height="30"
                    IsCancel="True"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


def _net_list(values):
    """///Summary: A Python sequence as a .NET list, fit for an ItemsSource.

    pythonnet will not convert a Python list to IEnumerable (CLAUDE.md § 3).
    """
    items = List[System.Object]()
    for value in values:
        items.Add(value)
    return items


def _guard(handler):
    """///Summary: Stop a Python exception from crossing back into WPF.

    An exception raised inside a .NET event callback reaches the user as a bare
    "Object reference not set to an instance of an object" naming nothing. Log
    the real stack instead.
    """
    def wrapped(sender, args):
        try:
            handler(sender, args)
        except Exception:
            logger.error("%s: handler %s failed\n%s", TOOL_TITLE,
                         getattr(handler, "__name__", handler),
                         traceback.format_exc())
    return wrapped


def section_view_type_names(host_doc):
    """///Summary: Names of every ViewFamilyType CreateSection will accept."""
    names = [vft.Name for vft in
             DB.FilteredElementCollector(host_doc).OfClass(DB.ViewFamilyType)
             if vft.ViewFamily == DB.ViewFamily.Section]
    names.sort(key=lambda n: n.lower())
    return names


class SettingsWindow(object):
    """///Summary: The Shift+Click settings dialog.

    Wraps a Window rather than subclassing it, so pythonnet never has to build a
    proxy type for a .NET base class.
    """

    def __init__(self, type_names, current_type, offset_inches):
        self.window = XamlReader.Parse(XAML)
        self.accepted = False
        self.type_name = current_type
        self.offset_inches = offset_inches

        self.type_combo = self.window.FindName("TypeCombo")
        self.offset_box = self.window.FindName("OffsetBox")

        self.type_combo.ItemsSource = _net_list(type_names)
        if current_type and current_type in type_names:
            self.type_combo.SelectedItem = current_type
        elif type_names:
            self.type_combo.SelectedIndex = 0

        self.offset_box.Text = "{:g}".format(offset_inches)

        self._save_handler = _guard(self.on_save)
        self._cancel_handler = _guard(self.on_cancel)
        self.window.FindName("BtnSave").Click += self._save_handler
        self.window.FindName("BtnCancel").Click += self._cancel_handler

    def on_save(self, sender, args):
        """Validate, then close."""
        selected = self.type_combo.SelectedItem
        if not selected:
            TaskDialog.Show(TOOL_TITLE, "Pick a section view type.")
            return
        try:
            offset = float((self.offset_box.Text or "").strip())
        except ValueError:
            TaskDialog.Show(
                TOOL_TITLE,
                "The offset must be a number of inches, such as 12.\n\nCorrect "
                "it and click Save again.")
            return
        if offset < 0:
            TaskDialog.Show(
                TOOL_TITLE,
                "The offset cannot be negative — that would pull the section "
                "inside the object.\n\nUse 0 or more.")
            return

        self.type_name = selected
        self.offset_inches = offset
        self.accepted = True
        self.window.Close()

    def on_cancel(self, sender, args):
        self.window.Close()

    def show(self):
        """Returns True if the user saved."""
        self.window.ShowDialog()
        return self.accepted


def main():
    if doc is None:
        TaskDialog.Show(TOOL_TITLE, "Open a project first, then run this again.")
        return

    type_names = section_view_type_names(doc)
    if not type_names:
        TaskDialog.Show(
            TOOL_TITLE,
            "This project has no Section view type, so there is nothing to "
            "choose.\n\nAdd one under View > View Types first.")
        return

    config = script.get_config()
    saved_type = config.get_option("view_type_name", "")
    saved_offset_ft = float(config.get_option("offset_ft", DEFAULT_OFFSET_FT))

    dialog = SettingsWindow(type_names, saved_type, saved_offset_ft * 12.0)
    if not dialog.show():
        return

    config.view_type_name = dialog.type_name
    config.offset_ft = dialog.offset_inches / 12.0
    script.save_config()

    TaskDialog.Show(
        TOOL_TITLE,
        "Saved.\n\nNew sections will use {} with a {:g} in. offset."
        .format(dialog.type_name, dialog.offset_inches))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.error("%s failed\n%s", TOOL_TITLE, traceback.format_exc())
        raise
