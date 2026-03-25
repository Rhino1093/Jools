#! python3
import clr
import os
from datetime import datetime

# Revit API / WPF Imports
clr.AddReference("RevitAPI")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
from Autodesk.Revit import DB # type: ignore
from System.Windows import Window, WindowStartupLocation # type: ignore

from pyrevit import revit, script, forms

# Get pyRevit environment
doc = revit.doc
app = doc.Application
output = script.get_output()
logger = script.get_logger()

# XAML UI Definition
XAML = """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Bulk NWC Export (v2.0)" Height="340" Width="450"
        WindowStartupLocation="CenterScreen" Topmost="True" ResizeMode="NoResize"
        Background="#F0F0F0">
    <StackPanel Margin="20">
        <TextBlock Text="Source Directory:" Margin="0,0,0,5" FontWeight="Bold" FontSize="12"/>
        <Grid Margin="0,0,0,15">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>
            <TextBox x:Name="SourcePath" VerticalAlignment="Center" Height="25" VerticalContentAlignment="Center" Background="White"/>
            <Button Grid.Column="1" Content="Browse" Width="70" Margin="5,0,0,0" Click="browse_source"/>
        </Grid>

        <TextBlock Text="View Filter (keywords):" Margin="0,0,0,5" FontWeight="Bold" FontSize="12"/>
        <TextBox x:Name="ViewFilter" Text="Navis, Coord" Height="25" VerticalContentAlignment="Center" Margin="0,0,0,10" Background="White"/>

        <CheckBox x:Name="CreateIfNotFound" Content="Create New 3D View if no matching views found" IsChecked="True" Margin="0,0,0,5" FontSize="11"/>
        <CheckBox x:Name="RecursiveSearch" Content="Search Subdirectories" IsChecked="True" Margin="0,0,0,5" FontSize="11"/>
        <CheckBox x:Name="DryRun" Content="Dry Run (Log only, no export)" IsChecked="False" Margin="0,0,0,20" FontSize="11" Foreground="DarkRed" FontWeight="Bold"/>

        <Button Content="START BULK EXPORT" Height="40" Click="start_export" FontWeight="Bold" Background="#FF007ACC" Foreground="White" BorderThickness="0">
             <Button.Resources>
                <Style TargetType="Border">
                    <Setter Property="CornerRadius" Value="3"/>
                </Style>
            </Button.Resources>
        </Button>
    </StackPanel>
</Window>
"""

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

def get_nwc_options():
    options = DB.NavisworksExportOptions()
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
    options.ExportScope              = DB.NavisworksExportScope.View
    options.ExportUrls               = True
    options.FacetingFactor           = 1.0
    options.FindMissingMaterials     = True
    options.Parameters               = DB.NavisworksParameters.All
    return options

def create_export_view(doc):
    """Creates a standardized 3D view for NWC export."""
    # 1. Find 3D ViewFamilyType
    collector = DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType)
    vft_id = None
    for vft in collector:
        if vft.ViewFamily == DB.ViewFamily.ThreeDimensional:
            vft_id = vft.Id
            break
    
    if not vft_id:
        return None

    # 2. Create the View
    t = DB.Transaction(doc, "Create Export View")
    t.Start()
    try:
        new_view = DB.View3D.CreateIsometric(doc, vft_id)
        # Ensure name is unique
        base_name = "_NAVIS_BULK_EXPORT"
        final_name = base_name
        suffix = 1
        while True:
            try:
                new_view.Name = final_name
                break
            except:
                final_name = "{}_{}".format(base_name, suffix)
                suffix += 1
        
        # 3. Configure Visibility (Basic Clean-up)
        new_view.DetailLevel = DB.ViewDetailLevel.Fine
        new_view.PartsVisibility = DB.PartsVisibility.ShowParts
        
        # Hide standard annotative categories if possible
        anno_cats = [
            DB.BuiltInCategory.OST_Levels,
            DB.BuiltInCategory.OST_Grids,
            DB.BuiltInCategory.OST_ReferencePlanes,
            DB.BuiltInCategory.OST_SectionBox,
            DB.BuiltInCategory.OST_ScopeBoxes
        ]
        for cat_enum in anno_cats:
            cat = doc.Settings.Categories.get_Item(cat_enum)
            if cat and new_view.CanCategoryBeHidden(cat.Id):
                new_view.SetCategoryHidden(cat.Id, True)

        t.Commit()
        return new_view
    except Exception as e:
        logger.error("Failed to create export view: {}".format(e))
        t.RollBack()
        return None

def main():
    if not DB.OptionalFunctionalityUtils.IsNavisworksExporterAvailable():
        forms.alert("Navisworks exporter is not available on this machine.", title="Error")
        return

    # 1. Show UI
    window = BulkExportWindow(XAML)
    window.show_dialog()

    if not window.export_triggered:
        return

    source_dir = window.SourcePath.Text
    view_filters = [x.strip().lower() for x in window.ViewFilter.Text.split(',')]
    recursive = window.RecursiveSearch.IsChecked
    create_if_none = window.CreateIfNotFound.IsChecked
    is_dry_run = window.DryRun.IsChecked

    # 2. Collect Revit Files
    rvt_files = []
    if recursive:
        for root, dirs, files in os.walk(source_dir):
            for file in files:
                if file.lower().endswith('.rvt'):
                    rvt_files.append(os.path.join(root, file))
    else:
        for file in os.listdir(source_dir):
            if file.lower().endswith('.rvt'):
                rvt_files.append(os.path.join(source_dir, file))

    if not rvt_files:
        forms.alert("No Revit files found in the selected directory.", title="No Files Found")
        return

    # 3. Process Files
    options = get_nwc_options()
    results = [] 

    output.print_md("# Bulk NWC Export Progress")
    if is_dry_run:
        output.print_md("### **DRY RUN MODE ENABLED** - No files will be exported.")
    
    with forms.ProgressBar(title="Processing Revit Files...", total=len(rvt_files)) as pb:
        for i, file_path in enumerate(rvt_files):
            file_name = os.path.basename(file_path)
            project_name = os.path.splitext(file_name)[0]
            dir_path = os.path.dirname(file_path)
            
            pb.update_progress(i+1, len(rvt_files))
            print("\n" + "-"*50)
            print("File: {}".format(file_name))

            # Open background doc
            try:
                model_path = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(file_path)
                open_options = DB.OpenOptions()
                open_options.DetachFromCentralOption = DB.DetachFromCentralOption.DetachAndPreserveWorksets
                
                bg_doc = app.OpenDocumentFile(model_path, open_options)
            except Exception as e:
                print("  [ERROR] Could not open file: {}".format(e))
                results.append([file_name, "Failed to Open", str(e)])
                continue

            try:
                # 4. Find matching views
                views = DB.FilteredElementCollector(bg_doc).OfClass(DB.View3D).ToElements()
                views_to_export = []
                
                for view in views:
                    if view.IsTemplate: continue
                    match = any(f in view.Name.lower() for f in view_filters)
                    if match:
                        views_to_export.append(view)
                
                # 5. Create view if none found
                created_view = None
                if not views_to_export and create_if_none:
                    print("  No matching views found. Creating temporary export view...")
                    created_view = create_export_view(bg_doc)
                    if created_view:
                        views_to_export.append(created_view)

                # 6. Export Views
                export_count = 0
                for view in views_to_export:
                    nwc_name = "{}_{}".format(project_name, view.Name)
                    
                    if is_dry_run:
                        print("  [DRY-RUN] Would export: {}".format(view.Name))
                        export_count += 1
                    else:
                        try:
                            bg_doc.Export(dir_path, nwc_name, options)
                            print("  [SUCCESS] Exported: {}".format(view.Name))
                            export_count += 1
                        except Exception as e:
                            print("  [ERROR] Failed to export {}: {}".format(view.Name, e))
                
                status = "Success ({} views)".format(export_count) if export_count > 0 else "Skipped"
                if is_dry_run: status = "[Dry Run] " + status
                
                results.append([file_name, status, "Created Temp View" if created_view else "Used Existing"])
            
            except Exception as e:
                print("  [ERROR] Unexpected processing error: {}".format(e))
                results.append([file_name, "Processing Error", str(e)])
            
            finally:
                bg_doc.Close(False) # Always discard changes

    # 7. Final Report
    print("\n" + "="*50)
    output.print_md("## Final Export Summary")
    output.print_table(
        table_data=results,
        columns=["File Name", "Status", "Method"]
    )

if __name__ == "__main__":
    main()
