#! python3
"""Export the active view or sheet to PDF in the user's Downloads folder, no dialog."""

import joolslib          # Jools.extension/lib, see CLAUDE.md section 6a
joolslib.install_events_shim()   # must precede every pyrevit import (section 2.3)

import ctypes
import datetime
import os
import re
import uuid

import clr  # type: ignore
clr.AddReference("RevitAPI")     # type: ignore
clr.AddReference("RevitAPIUI")   # type: ignore

from Autodesk.Revit import DB              # type: ignore
from Autodesk.Revit.UI import TaskDialog   # type: ignore
from Autodesk.Revit.Exceptions import FileAccessException  # type: ignore
from System.Collections.Generic import List  # type: ignore

from pyrevit import revit, script

__author__ = "Ryan Johnston"

doc = revit.doc
uidoc = revit.uidoc
logger = script.get_logger()

TOOL_TITLE = "Quick PDF"

# Open the PDF once it is written. The opened file doubles as the "it worked"
# confirmation, so no dialog is needed on success.
OPEN_AFTER_EXPORT = True

# Windows' Known Folder ID for Downloads. Asking the shell for it (rather than
# joining ~/Downloads) follows OneDrive or GPO folder redirection per user.
_FOLDERID_DOWNLOADS = "{374DE290-123F-4565-9164-39C4925E467B}"

# Characters Windows forbids in file names; view names often contain "/" or ":".
_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class _GUID(ctypes.Structure):
    """///Summary: Win32 GUID layout, needed to call SHGetKnownFolderPath."""
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __init__(self, guid_text):
        super().__init__()
        raw = uuid.UUID(guid_text).bytes_le  # bytes_le matches the Win32 struct layout
        ctypes.memmove(ctypes.byref(self), raw, ctypes.sizeof(self))


def get_downloads_folder():
    """///Summary: The current user's Downloads folder, honoring redirection.

    Falls back to %USERPROFILE%\\Downloads if the shell call fails, so the tool
    still works on a machine with an odd shell setup.
    """
    try:
        path_ptr = ctypes.c_wchar_p()
        shell32 = ctypes.windll.shell32
        result = shell32.SHGetKnownFolderPath(
            ctypes.byref(_GUID(_FOLDERID_DOWNLOADS)), 0, None, ctypes.byref(path_ptr))
        if result == 0 and path_ptr.value:
            folder = path_ptr.value
            ctypes.windll.ole32.CoTaskMemFree(path_ptr)  # the shell allocates; we free
            return folder
        logger.debug("SHGetKnownFolderPath returned hr=%s", result)
    except Exception as ex:
        logger.debug("SHGetKnownFolderPath failed: %s", ex)
    return os.path.join(os.path.expanduser("~"), "Downloads")


def get_target_view():
    """///Summary: The active view if it can be exported, or None after telling the user why not."""
    view = uidoc.ActiveView
    if view is None or view.IsTemplate or not view.CanBePrinted:
        name = view.Name if view is not None else "(none)"
        TaskDialog.Show(
            TOOL_TITLE,
            "The active view '{}' can't be exported to PDF.\n\n"
            "Click into a sheet, plan, section, 3D view or schedule, then run again."
            .format(name))
        return None
    return view


def safe_file_stem(view):
    """///Summary: The view name with characters Windows rejects in file names removed.

    Matches the "{View Name}" naming in the PDF Export dialog.
    """
    stem = _ILLEGAL_CHARS.sub("", view.Name).strip(" .")
    return stem or "Revit Export"


def is_locked(path):
    """///Summary: True if the file exists and another program (a PDF viewer) holds it open.

    Revit raises FileAccessException in that case. Checking first lets us pick
    a different name instead of failing.
    """
    if not os.path.exists(path):
        return False
    try:
        with open(path, "a"):
            return False
    except OSError:
        return True


def choose_file_stem(folder, stem):
    """///Summary: The stem to export to: overwrite the old PDF, or add a timestamp if it is open."""
    if not is_locked(os.path.join(folder, stem + ".pdf")):
        return stem
    return "{} ({})".format(stem, datetime.datetime.now().strftime("%Y-%m-%d %H%M%S"))


def build_options(file_stem):
    """///Summary: PDF options matching the team's "Roughbooks PDF Export" setup.

    Hardcoded rather than loaded from the named setup so the tool behaves the
    same in every project, including ones that never had that setup created.
    """
    opts = DB.PDFExportOptions()
    opts.FileName = file_stem          # Revit appends ".pdf"
    opts.Combine = True                # required for FileName to be used

    # Size / placement
    opts.PaperFormat = DB.ExportPaperFormat.Default        # <Use Sheet Size>
    opts.ZoomType = DB.ZoomType.Zoom
    opts.ZoomPercentage = 100
    opts.PaperPlacement = DB.PaperPlacementType.Center
    opts.PaperOrientation = DB.PageOrientationType.Auto

    # Appearance / output
    opts.RasterQuality = DB.RasterQualityType.High
    opts.ColorDepth = DB.ColorDepthType.Color
    opts.AlwaysUseRaster = False       # vector processing

    # Options panel
    opts.ViewLinksInBlue = False
    opts.HideReferencePlane = True
    opts.HideUnreferencedViewTags = True
    opts.MaskCoincidentLines = False
    opts.HideScopeBoxes = True
    opts.HideCropBoundaries = True
    opts.ReplaceHalftoneWithThinLines = False

    # SetExportInBackground was added after Revit 2022, so guard the call.
    if hasattr(opts, "SetExportInBackground"):
        opts.SetExportInBackground(False)
    return opts


def export_view(view, folder, file_stem):
    """///Summary: Export one view to folder/file_stem.pdf. Returns the PDF path, or None after telling the user."""
    view_ids = List[DB.ElementId]()   # Python lists don't convert to IList under pythonnet
    view_ids.Add(view.Id)
    pdf_path = os.path.join(folder, file_stem + ".pdf")

    try:
        ok = doc.Export(folder, view_ids, build_options(file_stem))
    except FileAccessException:
        TaskDialog.Show(
            TOOL_TITLE,
            "Couldn't write:\n{}\n\nClose the PDF if it's open in a viewer, then run again."
            .format(pdf_path))
        return None
    except Exception as ex:
        logger.error("Export failed for view id=%s name=%s: %s",
                     joolslib.eid_int(view.Id), view.Name, ex)
        TaskDialog.Show(
            TOOL_TITLE,
            "Revit refused to export '{}':\n{}\n\n"
            "Try File > Export > PDF on this view to see Revit's own error."
            .format(view.Name, ex))
        return None

    if not ok or not os.path.exists(pdf_path):
        TaskDialog.Show(
            TOOL_TITLE,
            "Revit reported the export of '{}' did not complete.\n\n"
            "Try File > Export > PDF on this view to see Revit's own error.".format(view.Name))
        return None
    return pdf_path


def main():
    view = get_target_view()
    if view is None:
        return

    folder = get_downloads_folder()
    if not os.path.isdir(folder):
        TaskDialog.Show(
            TOOL_TITLE,
            "Your Downloads folder wasn't found:\n{}\n\n"
            "Create it (or ask IT about folder redirection), then run again.".format(folder))
        return

    file_stem = choose_file_stem(folder, safe_file_stem(view))
    logger.debug("folder=%s, file_stem=%s, view_id=%s",
                 folder, file_stem, joolslib.eid_int(view.Id))

    pdf_path = export_view(view, folder, file_stem)
    if pdf_path is None:
        return

    if OPEN_AFTER_EXPORT:
        try:
            os.startfile(pdf_path)
            return
        except OSError as ex:
            logger.debug("startfile failed: %s", ex)  # no PDF viewer associated; fall through
    TaskDialog.Show(TOOL_TITLE, "Saved:\n{}".format(pdf_path))


if __name__ == "__main__":
    main()
