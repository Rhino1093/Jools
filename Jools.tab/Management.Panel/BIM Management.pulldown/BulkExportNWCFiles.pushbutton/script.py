#! python3
import clr
import os
import re

clr.AddReference("RevitAPI")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")

from Autodesk.Revit import DB  # type: ignore
from pyrevit import script, forms

# ── pyRevit environment ────────────────────────────────────────────────────────
uidoc = __revit__.ActiveUIDocument # type: ignore
doc = uidoc.Document
app = doc.Application
output = script.get_output()
logger = script.get_logger()

# ── XAML UI ───────────────────────────────────────────────────────────────────
XAML = """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Bulk NWC Export (v2.1)" Height="360" Width="460"
        WindowStartupLocation="CenterScreen" Topmost="True" ResizeMode="NoResize"
        Background="#F0F0F0">
    <StackPanel Margin="20">
        <TextBlock Text="Source Directory:" Margin="0,0,0,5" FontWeight="Bold" FontSize="12"/>
        <Grid Margin="0,0,0,15">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>
            <TextBox x:Name="SourcePath" VerticalAlignment="Center" Height="25"
                     VerticalContentAlignment="Center" Background="White"/>
            <Button Grid.Column="1" Content="Browse" Width="70" Margin="5,0,0,0"
                    Click="browse_source"/>
        </Grid>

        <TextBlock Text="View Filter (comma-separated keywords):" Margin="0,0,0,5"
                   FontWeight="Bold" FontSize="12"/>
        <TextBox x:Name="ViewFilter" Text="Navis, Coord" Height="25"
                 VerticalContentAlignment="Center" Margin="0,0,0,10" Background="White"/>

        <CheckBox x:Name="CreateIfNotFound"
                  Content="Create temporary 3D view if no matching views found"
                  IsChecked="True" Margin="0,0,0,5" FontSize="11"/>
        <CheckBox x:Name="RecursiveSearch" Content="Search Subdirectories"
                  IsChecked="True" Margin="0,0,0,5" FontSize="11"/>
        <CheckBox x:Name="DryRun" Content="Dry Run (log only, no export)"
                  IsChecked="False" Margin="0,0,0,20" FontSize="11"
                  Foreground="DarkRed" FontWeight="Bold"/>

        <Button Content="START BULK EXPORT" Height="40" Click="start_export"
                FontWeight="Bold" Background="#FF007ACC" Foreground="White"
                BorderThickness="0">
            <Button.Resources>
                <Style TargetType="Border">
                    <Setter Property="CornerRadius" Value="3"/>
                </Style>
            </Button.Resources>
        </Button>
    </StackPanel>
</Window>
"""

# ── UI Window ─────────────────────────────────────────────────────────────────
class BulkExportWindow(forms.WPFWindow):
    def __init__(self, xaml_str):
        forms.WPFWindow.__init__(self, xaml_str)
        self.export_triggered = False

    def browse_source(self, sender, e):
        folder = forms.pick_folder(title="Select Source Directory")
        if folder:
            self.SourcePath.Text = folder

    def start_export(self, sender, e):
        if not self.SourcePath.Text or not os.path.exists(self.SourcePath.Text):
            forms.alert("Please select a valid source directory.")
            return
        self.export_triggered = True
        self.Close()


# ── Helpers ───────────────────────────────────────────────────────────────────
def sanitize_filename(name):
    """Strip characters illegal in file names and collapse whitespace."""
    name = re.sub(r'[\\/*?:"<>|{}]', "_", name)
    name = name.replace(" ", "_")
    return name.strip("_") or "view"


def get_nwc_options(view_id):
    """
    Build a fresh NavisworksExportOptions for a specific view.
    BUG FIX: options must be recreated (or ViewId reset) per view because
    ExportScope=View requires a valid ViewId before every Export() call.
    """
    options = DB.NavisworksExportOptions()
    options.ExportScope              = DB.NavisworksExportScope.View
    options.ViewId                   = view_id          # ← THE critical fix
    options.ConvertElementProperties = True
    options.ConvertLights            = True
    options.ConvertLinkedCADFormats  = True
    options.Coordinates              = DB.NavisworksCoordinates.Shared
    options.DivideFileIntoLevels     = True
    options.ExportElementIds         = True
    options.ExportLinks              = True
    options.ExportParts              = True
    options.ExportRoomAsAttribute    = True
    options.ExportRoomGeometry       = True
    options.ExportUrls               = True
    options.FacetingFactor           = 1.0
    options.FindMissingMaterials     = True
    options.Parameters               = DB.NavisworksParameters.All
    return options


def create_export_view(bg_doc):
    """
    Creates a clean isometric 3D view for NWC export and returns it.
    Returns None on failure.
    NOTE: The transaction is committed to bg_doc. The caller MUST close
    bg_doc with saveModified=False so the view is not persisted to disk.
    """
    collector = DB.FilteredElementCollector(bg_doc).OfClass(DB.ViewFamilyType)
    vft_id = next(
        (vft.Id for vft in collector
         if vft.ViewFamily == DB.ViewFamily.ThreeDimensional),
        None
    )
    if not vft_id:
        logger.error("No 3D ViewFamilyType found in document.")
        return None

    t = DB.Transaction(bg_doc, "Create Temp Export View")
    t.Start()
    try:
        new_view = DB.View3D.CreateIsometric(bg_doc, vft_id)

        base_name = "_NAVIS_BULK_EXPORT"
        final_name = base_name
        suffix = 1
        while True:
            try:
                new_view.Name = final_name
                break
            except Exception:
                final_name = "{}_{}".format(base_name, suffix)
                suffix += 1

        new_view.DetailLevel   = DB.ViewDetailLevel.Fine
        new_view.PartsVisibility = DB.PartsVisibility.ShowParts

        anno_cats = [
            DB.BuiltInCategory.OST_Levels,
            DB.BuiltInCategory.OST_Grids,
            DB.BuiltInCategory.OST_ReferencePlanes,
            DB.BuiltInCategory.OST_SectionBox,
            DB.BuiltInCategory.OST_ScopeBoxes,
        ]
        for cat_enum in anno_cats:
            cat = bg_doc.Settings.Categories.get_Item(cat_enum)
            if cat and new_view.CanCategoryBeHidden(cat.Id):
                new_view.SetCategoryHidden(cat.Id, True)

        t.Commit()
        return new_view

    except Exception as ex:
        logger.error("Failed to create export view: {}".format(ex))
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        return None


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if not DB.OptionalFunctionalityUtils.IsNavisworksExporterAvailable():
        forms.alert("Navisworks exporter is not available on this machine.",
                    title="Error")
        return

    # 1. Show UI
    window = BulkExportWindow(XAML)
    window.show_dialog()
    if not window.export_triggered:
        return

    source_dir    = window.SourcePath.Text
    view_filters  = [x.strip().lower() for x in window.ViewFilter.Text.split(",") if x.strip()]
    recursive     = window.RecursiveSearch.IsChecked
    create_if_none = window.CreateIfNotFound.IsChecked
    is_dry_run    = window.DryRun.IsChecked

    # 2. Collect .rvt files
    rvt_files = []
    if recursive:
        for root, _dirs, files in os.walk(source_dir):
            for f in files:
                if f.lower().endswith(".rvt"):
                    rvt_files.append(os.path.join(root, f))
    else:
        for f in os.listdir(source_dir):
            if f.lower().endswith(".rvt"):
                rvt_files.append(os.path.join(source_dir, f))

    if not rvt_files:
        forms.alert("No Revit files found in the selected directory.",
                    title="No Files Found")
        return

    # 3. Process files
    results = []
    output.print_md("# Bulk NWC Export Progress")
    if is_dry_run:
        output.print_md("### ⚠ DRY RUN — no files will be written.")

    with forms.ProgressBar(title="Processing Revit Files...",
                           total=len(rvt_files)) as pb:

        for i, file_path in enumerate(rvt_files):
            file_name    = os.path.basename(file_path)
            project_name = os.path.splitext(file_name)[0]
            dir_path     = os.path.dirname(file_path)

            pb.update_progress(i + 1, len(rvt_files))
            output.print_md("---")
            output.print_md("**File:** `{}`".format(file_name))

            # ── Open background document ──────────────────────────────────
            try:
                model_path   = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(file_path)
                open_options = DB.OpenOptions()
                open_options.DetachFromCentralOption = (
                    DB.DetachFromCentralOption.DetachAndPreserveWorksets
                )
                bg_doc = app.OpenDocumentFile(model_path, open_options)
            except Exception as ex:
                output.print_md("  ❌ **Could not open:** `{}`".format(ex))
                results.append([file_name, "Failed to Open", str(ex)])
                continue

            created_view = None
            try:
                # ── Find matching 3D views ────────────────────────────────
                all_3d = DB.FilteredElementCollector(bg_doc)\
                           .OfClass(DB.View3D)\
                           .ToElements()

                views_to_export = [
                    v for v in all_3d
                    if not v.IsTemplate
                    and any(f in v.Name.lower() for f in view_filters)
                ]

                # ── Optionally create a temp view ─────────────────────────
                if not views_to_export and create_if_none:
                    output.print_md(
                        "  ℹ No matching views found — creating temporary export view..."
                    )
                    created_view = create_export_view(bg_doc)
                    if created_view:
                        views_to_export.append(created_view)
                    else:
                        output.print_md("  ❌ Could not create temporary view. Skipping.")

                if not views_to_export:
                    output.print_md("  ⚠ No views to export. Skipping.")
                    results.append([file_name, "Skipped — no views", "N/A"])
                    continue

                # ── Export each view ──────────────────────────────────────
                export_count  = 0
                error_count   = 0

                for view in views_to_export:
                    # BUG FIX: sanitize the name to avoid illegal-char failures
                    safe_view_name = sanitize_filename(view.Name)
                    nwc_name       = "{}_{}".format(
                        sanitize_filename(project_name), safe_view_name
                    )

                    if is_dry_run:
                        output.print_md(
                            "  📋 [DRY-RUN] Would export view `{}` → `{}.nwc`".format(
                                view.Name, nwc_name
                            )
                        )
                        export_count += 1
                        continue

                    try:
                        # BUG FIX: build fresh options with ViewId set per view
                        view_options = get_nwc_options(view.Id)
                        bg_doc.Export(dir_path, nwc_name, view_options)
                        output.print_md(
                            "  ✅ Exported `{}` → `{}.nwc`".format(view.Name, nwc_name)
                        )
                        export_count += 1
                    except Exception as ex:
                        output.print_md(
                            "  ❌ Export failed for `{}`: `{}`".format(view.Name, ex)
                        )
                        error_count += 1

                # ── Build row status ──────────────────────────────────────
                if error_count and not export_count:
                    status = "All exports failed"
                elif error_count:
                    status = "Partial ({} ok, {} failed)".format(export_count, error_count)
                else:
                    status = "Success ({} view{})".format(
                        export_count, "s" if export_count != 1 else ""
                    )
                if is_dry_run:
                    status = "[Dry Run] " + status

                method = "Created temp view" if created_view else "Used existing views"
                results.append([file_name, status, method])

            except Exception as ex:
                output.print_md("  ❌ **Unexpected error:** `{}`".format(ex))
                results.append([file_name, "Processing Error", str(ex)])

            finally:
                # Always close without saving — temp view changes are discarded
                bg_doc.Close(False)

    # 4. Summary report
    output.print_md("---")
    output.print_md("## ✅ Final Export Summary")
    output.print_table(
        table_data=results,
        columns=["File Name", "Status", "Method"]
    )


if __name__ == "__main__":
    main()