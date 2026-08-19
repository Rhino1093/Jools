#! python3
import os

import joolslib          # Jools.extension/lib, see CLAUDE.md section 6a
joolslib.install_events_shim()
import clr
import json

# Reference standard .NET and Revit API assemblies
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("System.Xaml")
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Xml")

from Autodesk.Revit import DB # type: ignore
from System import Guid # type: ignore
from System.Collections.Generic import List # type: ignore
from System.Windows import Window, Visibility # type: ignore
from System.Windows.Markup import XamlReader # type: ignore
from System.IO import StringReader # type: ignore
from System.Windows.Forms import FolderBrowserDialog, DialogResult # type: ignore
from System.Xml import XmlReader # type: ignore

from pyrevit import script

# Initialize pyRevit components
uidoc = __revit__.ActiveUIDocument # type: ignore
doc = uidoc.Document
app = __revit__.Application # type: ignore
logger = script.get_logger()
output = script.get_output()

# Settings file path in User Profile's AppData
SETTINGS_FILE = os.path.join(os.environ["APPDATA"], "pyRevit", "PDI_NWC_Only_Settings.json")

XAML_STR = """<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Batch Export NWC Files" 
        Height="520" Width="530"
        WindowStartupLocation="CenterScreen" 
        Background="#1E1E1E" Foreground="#FFFFFF"
        ResizeMode="NoResize" FontFamily="Segoe UI">
    <Grid Margin="15">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/> <!-- Title -->
            <RowDefinition Height="*"/>    <!-- Fields -->
            <RowDefinition Height="Auto"/> <!-- Buttons -->
        </Grid.RowDefinitions>

        <!-- Header -->
        <StackPanel Grid.Row="0" Margin="0,0,0,15">
            <TextBlock Text="Batch Export NWC Files" FontSize="18" FontWeight="Bold" Foreground="#3A96DD"/>
            <TextBlock Text="Configure coordination views, and batch export Navisworks NWC files without saving." FontSize="11" Foreground="#888888" Margin="0,2,0,0"/>
            <Separator Background="#333333" Margin="0,8,0,0"/>
        </StackPanel>

        <!-- Input Fields -->
        <ScrollViewer Grid.Row="1" VerticalScrollBarVisibility="Auto" Margin="0,0,0,10">
            <StackPanel>
                <!-- Source Folder -->
                <TextBlock Text="Source Local Folder (RVT files):" FontWeight="SemiBold" FontSize="11" Margin="0,0,0,4"/>
                <Grid Margin="0,0,0,10">
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="80"/>
                    </Grid.ColumnDefinitions>
                    <TextBox Name="txtSourcePath" Grid.Column="0" Height="24" Background="#2D2D2D" Foreground="#FFFFFF" BorderBrush="#3F3F46" Padding="3,1" VerticalContentAlignment="Center"/>
                    <Button Name="btnBrowseSource" Content="Browse..." Grid.Column="1" Margin="5,0,0,0" Height="24" Background="#3E3E42" Foreground="#FFFFFF" BorderBrush="#3F3F46"/>
                </Grid>

                <!-- Destination NWC Folder -->
                <TextBlock Text="Destination NWC Folder:" FontWeight="SemiBold" FontSize="11" Margin="0,0,0,4"/>
                <Grid Margin="0,0,0,10">
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="80"/>
                    </Grid.ColumnDefinitions>
                    <TextBox Name="txtDestNwcPath" Grid.Column="0" Height="24" Background="#2D2D2D" Foreground="#FFFFFF" BorderBrush="#3F3F46" Padding="3,1" VerticalContentAlignment="Center"/>
                    <Button Name="btnBrowseDestNwc" Content="Browse..." Grid.Column="1" Margin="5,0,0,0" Height="24" Background="#3E3E42" Foreground="#FFFFFF" BorderBrush="#3F3F46"/>
                </Grid>

                <!-- Target View Name & Checkboxes -->
                <Grid Margin="0,0,0,10">
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="220"/>
                        <ColumnDefinition Width="*"/>
                    </Grid.ColumnDefinitions>
                    <StackPanel Grid.Column="0" Margin="0,0,10,0">
                        <TextBlock Text="Target View Name:" FontWeight="SemiBold" FontSize="11" Margin="0,0,0,4"/>
                        <TextBox Name="txtViewName" Height="24" Text="PDI Coordination" Background="#2D2D2D" Foreground="#FFFFFF" BorderBrush="#3F3F46" Padding="3,1" VerticalContentAlignment="Center"/>
                    </StackPanel>
                    <StackPanel Grid.Column="1" VerticalAlignment="Bottom">
                        <CheckBox Name="chkRecursive" Content="Search Subdirectories" Foreground="#FFFFFF" IsChecked="False" Margin="0,0,0,4" FontSize="11"/>
                        <CheckBox Name="chkDryRun" Content="Dry Run (log only, no export)" Foreground="#E81123" FontWeight="Bold" IsChecked="False" FontSize="11"/>
                    </StackPanel>
                </Grid>

                <!-- NWC Settings Expander -->
                <Expander Name="expNwcSettings" Header="Navisworks NWC Export Options" Foreground="#FFFFFF" Margin="0,0,0,5" IsExpanded="False">
                    <Border BorderThickness="1" BorderBrush="#333333" Background="#252526" Padding="10" CornerRadius="3" Margin="0,4,0,0">
                        <StackPanel>
                            <Grid Margin="0,0,0,8">
                                <Grid.ColumnDefinitions>
                                    <ColumnDefinition Width="Auto"/>
                                    <ColumnDefinition Width="*"/>
                                </Grid.ColumnDefinitions>
                                <TextBlock Text="Coordinates:" VerticalAlignment="Center" Foreground="#FFFFFF" Margin="0,0,10,0" FontSize="11"/>
                                <ComboBox Name="cmbNwcCoordinates" Grid.Column="1" Height="22" Background="#2D2D2D" Foreground="#FFFFFF" BorderBrush="#3F3F46" SelectedIndex="0">
                                    <ComboBoxItem Content="Shared"/>
                                    <ComboBoxItem Content="ProjectInternal"/>
                                </ComboBox>
                            </Grid>
                            <CheckBox Name="chkNwcExportParts" Content="Export Parts" Foreground="#FFFFFF" IsChecked="True" Margin="0,2,0,2" FontSize="11"/>
                            <CheckBox Name="chkNwcExportLinks" Content="Export Links" Foreground="#FFFFFF" IsChecked="False" Margin="0,2,0,2" FontSize="11"/>
                            <CheckBox Name="chkNwcExportIds" Content="Export Element IDs" Foreground="#FFFFFF" IsChecked="True" Margin="0,2,0,2" FontSize="11"/>
                            <CheckBox Name="chkNwcExportRooms" Content="Export Rooms" Foreground="#FFFFFF" IsChecked="False" Margin="0,2,0,2" FontSize="11"/>
                            <CheckBox Name="chkNwcConvertCad" Content="Convert CAD Links" Foreground="#FFFFFF" IsChecked="True" Margin="0,2,0,2" FontSize="11"/>
                            <CheckBox Name="chkNwcDivideLevels" Content="Divide File by Levels" Foreground="#FFFFFF" IsChecked="True" Margin="0,2,0,2" FontSize="11"/>
                            <CheckBox Name="chkNwcConvertLights" Content="Convert Lights" Foreground="#FFFFFF" IsChecked="False" Margin="0,2,0,2" FontSize="11"/>
                        </StackPanel>
                    </Border>
                </Expander>
            </StackPanel>
        </ScrollViewer>

        <!-- Actions -->
        <StackPanel Grid.Row="2" Orientation="Horizontal" HorizontalAlignment="Right">
            <Button Name="btnCancel" Content="Cancel" Width="80" Height="26" Background="#3E3E42" Foreground="#FFFFFF" BorderBrush="#3F3F46"/>
            <Button Name="btnStart" Content="Run Export" Width="100" Height="26" Margin="10,0,0,0" Background="#0E639C" Foreground="#FFFFFF" BorderBrush="#3F3F46" FontWeight="SemiBold"/>
        </StackPanel>
    </Grid>
</Window>
"""

def load_settings():
    """Loads settings from the pyRevit settings JSON file."""
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                return json.load(f)
        except Exception as ex:
            logger.warning("Failed to load settings: {}".format(ex))
    return {}

def save_settings(settings):
    """Saves settings to the pyRevit settings JSON file."""
    try:
        dir_path = os.path.dirname(SETTINGS_FILE)
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=4)
    except Exception as ex:
        logger.warning("Failed to save settings: {}".format(ex))

class StartupWindow(object):
    """Handles loading the XAML UI and managing window state/events."""
    def __init__(self):
        reader = StringReader(XAML_STR)
        xml_reader = XmlReader.Create(reader)
        self.window = XamlReader.Load(xml_reader)
        
        # Get UI controls
        self.txt_source = self.window.FindName("txtSourcePath")
        self.txt_dest_nwc = self.window.FindName("txtDestNwcPath")
        self.txt_view_name = self.window.FindName("txtViewName")
        self.chk_recursive = self.window.FindName("chkRecursive")
        self.chk_dry_run = self.window.FindName("chkDryRun")
        
        self.exp_nwc_settings = self.window.FindName("expNwcSettings")
        self.cmb_nwc_coords = self.window.FindName("cmbNwcCoordinates")
        self.chk_nwc_parts = self.window.FindName("chkNwcExportParts")
        self.chk_nwc_links = self.window.FindName("chkNwcExportLinks")
        self.chk_nwc_ids = self.window.FindName("chkNwcExportIds")
        self.chk_nwc_rooms = self.window.FindName("chkNwcExportRooms")
        self.chk_nwc_cad = self.window.FindName("chkNwcConvertCad")
        self.chk_nwc_levels = self.window.FindName("chkNwcDivideLevels")
        self.chk_nwc_lights = self.window.FindName("chkNwcConvertLights")
        
        self.btn_browse_source = self.window.FindName("btnBrowseSource")
        self.btn_browse_dest_nwc = self.window.FindName("btnBrowseDestNwc")
        self.btn_start = self.window.FindName("btnStart")
        self.btn_cancel = self.window.FindName("btnCancel")
        
        # Register Event Handlers
        self.btn_browse_source.Click += self.on_browse_source
        self.btn_browse_dest_nwc.Click += self.on_browse_dest_nwc
        
        self.btn_start.Click += self.on_start
        self.btn_cancel.Click += self.on_cancel
        
        # Load saved settings
        self.settings = load_settings()
        self.txt_source.Text = self.settings.get("source_path", "")
        self.txt_dest_nwc.Text = self.settings.get("dest_nwc_path", "")
        self.txt_view_name.Text = self.settings.get("view_name", "PDI Coordination")
        self.chk_recursive.IsChecked = self.settings.get("recursive_search", False)
        self.chk_dry_run.IsChecked = self.settings.get("dry_run", False)
        
        # Load NWC Options
        self.chk_nwc_parts.IsChecked = self.settings.get("nwc_export_parts", True)
        self.chk_nwc_links.IsChecked = self.settings.get("nwc_export_links", False)
        self.chk_nwc_ids.IsChecked = self.settings.get("nwc_export_ids", True)
        self.chk_nwc_rooms.IsChecked = self.settings.get("nwc_export_rooms", False)
        self.chk_nwc_cad.IsChecked = self.settings.get("nwc_convert_cad", True)
        self.chk_nwc_levels.IsChecked = self.settings.get("nwc_divide_levels", True)
        self.chk_nwc_lights.IsChecked = self.settings.get("nwc_convert_lights", False)
        
        nwc_coords = self.settings.get("nwc_coordinates", "Shared")
        self.cmb_nwc_coords.SelectedIndex = 1 if nwc_coords == "ProjectInternal" else 0
        
        self.is_running = False

    def on_browse_source(self, sender, args):
        dialog = FolderBrowserDialog()
        dialog.Description = "Select Source Folder containing Revit Models"
        if self.txt_source.Text and os.path.exists(self.txt_source.Text):
            dialog.SelectedPath = self.txt_source.Text
        if dialog.ShowDialog() == DialogResult.OK:
            self.txt_source.Text = dialog.SelectedPath

    def on_browse_dest_nwc(self, sender, args):
        dialog = FolderBrowserDialog()
        dialog.Description = "Select Destination NWC Folder"
        if self.txt_dest_nwc.Text and os.path.exists(self.txt_dest_nwc.Text):
            dialog.SelectedPath = self.txt_dest_nwc.Text
        if dialog.ShowDialog() == DialogResult.OK:
            self.txt_dest_nwc.Text = dialog.SelectedPath

    def on_cancel(self, sender, args):
        self.window.Close()

    def on_start(self, sender, args):
        # Validate Inputs
        source = self.txt_source.Text.strip()
        dest_nwc = self.txt_dest_nwc.Text.strip()
        view_name = self.txt_view_name.Text.strip()
        
        if not source or not os.path.exists(source):
            joolslib.alert("Please select a valid source local folder.", title="Input Error")
            return
            
        if not dest_nwc or not os.path.exists(dest_nwc):
            joolslib.alert("Please select a valid destination NWC folder.", title="Input Error")
            return
            
        if not view_name:
            joolslib.alert("Please enter a valid target view name.", title="Input Error")
            return
            
        # Save settings
        self.settings["source_path"] = source
        self.settings["dest_nwc_path"] = dest_nwc
        self.settings["view_name"] = view_name
        self.settings["recursive_search"] = self.chk_recursive.IsChecked
        self.settings["dry_run"] = self.chk_dry_run.IsChecked
        
        self.settings["nwc_coordinates"] = "ProjectInternal" if self.cmb_nwc_coords.SelectedIndex == 1 else "Shared"
        self.settings["nwc_export_parts"] = self.chk_nwc_parts.IsChecked
        self.settings["nwc_export_links"] = self.chk_nwc_links.IsChecked
        self.settings["nwc_export_ids"] = self.chk_nwc_ids.IsChecked
        self.settings["nwc_export_rooms"] = self.chk_nwc_rooms.IsChecked
        self.settings["nwc_convert_cad"] = self.chk_nwc_cad.IsChecked
        self.settings["nwc_divide_levels"] = self.chk_nwc_levels.IsChecked
        self.settings["nwc_convert_lights"] = self.chk_nwc_lights.IsChecked
        
        save_settings(self.settings)
        
        self.is_running = True
        self.window.Close()

def setup_coordination_view(temp_doc, view_name):
    """Finds or creates a 3D coordination view, configures view properties/visibility overrides."""
    logger.info("Initializing view configuration for '{}'".format(view_name))
    
    # Find view family type for 3D views
    vft = next((v for v in DB.FilteredElementCollector(temp_doc)
                .OfClass(DB.ViewFamilyType)
                if v.ViewFamily == DB.ViewFamily.ThreeDimensional), None)
    if not vft:
        logger.warning("No 3D view family type found. Cannot create coordination view.")
        return None

    # Check if view already exists
    views = DB.FilteredElementCollector(temp_doc).OfClass(DB.View).ToElements()
    view = next((v for v in views if v.Name == view_name and not v.IsTemplate), None)

    with DB.Transaction(temp_doc, "Setup PDI Coordination View") as t:
        t.Start()
        if not view:
            view = DB.View3D.CreateIsometric(temp_doc, vft.Id)
            view.Name = view_name
            logger.info("Created new 3D Coordination View: '{}'".format(view_name))
        else:
            logger.info("Found existing 3D Coordination View: '{}'".format(view_name))

        # Clear any applied View Template to allow programmatic overrides
        if view.ViewTemplateId != DB.ElementId.InvalidElementId:
            view.ViewTemplateId = DB.ElementId.InvalidElementId
            logger.info("Cleared applied View Template from '{}'".format(view_name))

        # Configure detail level (Fine)
        view.DetailLevel = DB.ViewDetailLevel.Fine
        logger.info("Set detail level to Fine")

        # Hide annotation categories globally
        view.AreAnnotationCategoriesHidden = True
        logger.info("Disabled visibility for all Annotation Categories")

        # Hide import categories globally
        view.AreImportCategoriesHidden = True
        logger.info("Disabled visibility for all Import Categories")

        # Hide analytical categories, ensure model categories are visible
        categories = temp_doc.Settings.Categories
        analytical_count = 0
        model_count = 0
        
        for category in categories:
            # Hide Analytical
            if category.CategoryType == DB.CategoryType.AnalyticalModel:
                if view.CanCategoryBeHidden(category.Id):
                    view.SetCategoryHidden(category.Id, True)
                    analytical_count += 1
            # Show Model
            elif category.CategoryType == DB.CategoryType.Model:
                if view.CanCategoryBeHidden(category.Id):
                    view.SetCategoryHidden(category.Id, False)
                    model_count += 1

        logger.info("Hiding {} analytical categories. Ensuring {} model categories are visible.".format(analytical_count, model_count))

        # Hide Revit Links category
        try:
            rvt_links_cat = temp_doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_RvtLinks)
            if rvt_links_cat and view.CanCategoryBeHidden(rvt_links_cat.Id):
                view.SetCategoryHidden(rvt_links_cat.Id, True)
                logger.info("Disabled visibility for Revit Links category (OST_RvtLinks)")
            else:
                logger.warning("Revit Links category cannot be hidden in this view configuration.")
        except Exception as ex:
            logger.warning("Could not set Revit Links category visibility: {}".format(ex))

        # Show all User Worksets in this view
        if temp_doc.IsWorkshared:
            try:
                worksets = DB.FilteredWorksetCollector(temp_doc).OfKind(DB.WorksetKind.UserWorkset)
                workset_count = 0
                for workset in worksets:
                    view.SetWorksetVisibility(workset.Id, DB.WorksetVisibility.Visible)
                    workset_count += 1
                logger.info("Set all {} user worksets to Visible in the view".format(workset_count))
            except Exception as ex:
                logger.warning("Failed to configure workset visibilities: {}".format(ex))

        t.Commit()
    return view

def export_nwc_file(temp_doc, view, folder_path, file_name, nwc_options):
    """Exports the specified view to an NWC file using configured options."""
    nwc_name = os.path.splitext(file_name)[0] + ".nwc"
    logger.info("Exporting NWC for view '{}' to: {}".format(view.Name, os.path.join(folder_path, nwc_name)))
    
    try:
        # Recreate option fields for this view ID
        nwc_options.ViewId = view.Id
        temp_doc.Export(folder_path, nwc_name, nwc_options)
        logger.info("Successfully exported NWC: {}".format(nwc_name))
        return True
    except Exception as ex:
        logger.error("Failed to export NWC file: {}".format(ex))
        return False

def failures_processing_handler(sender, args):
    """Automatically dismisses warnings and handles failures during transactions and document loading."""
    try:
        accessor = args.GetFailuresAccessor()
        failures = accessor.GetFailureMessages()
        for failure in failures:
            if failure.GetSeverity() == DB.FailureSeverity.Warning:
                accessor.DeleteWarning(failure)
        args.SetProcessingResult(DB.FailureProcessingResult.Continue)
    except Exception as ex:
        logger.warning("Error in failures processing: {}".format(ex))

def dismiss_dialog(sender, args):
    """Automatically dismisses dialog boxes that appear during background processing."""
    if args.DialogId == "Dialog_Revit_DocWarnDialog":
        logger.info("Skipped Dialog_Revit_DocWarnDialog to allow standard warning processing.")
        return

    try:
        args.OverrideResult(1)
        logger.info("Auto-dismissed dialog box (ID: {})".format(args.DialogId))
    except Exception:
        try:
            args.OverrideResult(2)
            logger.info("Auto-dismissed dialog box with Cancel (ID: {})".format(args.DialogId))
        except Exception as ex:
            logger.warning("Could not auto-dismiss dialog box (ID: {}): {}".format(args.DialogId, ex))

def main():
    # Check if NWC Exporter is available
    if not DB.OptionalFunctionalityUtils.IsNavisworksExporterAvailable():
        joolslib.alert("Navisworks exporter utility is not available on this machine.", title="Error")
        return

    # 1. Initialize and Display UI
    form = StartupWindow()
    form.window.ShowDialog()
    
    if not form.is_running:
        logger.info("Execution cancelled by user.")
        return

    # 2. Extract settings from form
    source_folder = form.txt_source.Text.strip()
    dest_nwc_folder = form.txt_dest_nwc.Text.strip()
    view_name = form.txt_view_name.Text.strip()
    recursive = form.chk_recursive.IsChecked
    is_dry_run = form.chk_dry_run.IsChecked

    # 3. Gather local files
    rvt_files = []
    if recursive:
        for root, _dirs, files in os.walk(source_folder):
            for f in files:
                if f.lower().endswith(".rvt"):
                    rvt_files.append(os.path.join(root, f))
    else:
        for f in os.listdir(source_folder):
            if f.lower().endswith(".rvt"):
                rvt_files.append(os.path.join(source_folder, f))

    if not rvt_files:
        joolslib.alert("No Revit models (.rvt) found in the selected source folder.")
        return

    # Build NWC options object
    nwc_options = DB.NavisworksExportOptions()
    nwc_options.ExportScope = DB.NavisworksExportScope.View
    
    # Coordinates
    if form.cmb_nwc_coords.SelectedIndex == 1:
        nwc_options.Coordinates = DB.NavisworksCoordinates.Internal
    else:
        nwc_options.Coordinates = DB.NavisworksCoordinates.Shared
        
    nwc_options.ExportParts = form.chk_nwc_parts.IsChecked
    nwc_options.ExportLinks = form.chk_nwc_links.IsChecked
    nwc_options.ExportElementIds = form.chk_nwc_ids.IsChecked
    nwc_options.ExportRoomGeometry = form.chk_nwc_rooms.IsChecked
    nwc_options.ConvertLinkedCADFormats = form.chk_nwc_cad.IsChecked
    nwc_options.DivideFileIntoLevels = form.chk_nwc_levels.IsChecked
    nwc_options.ConvertLights = form.chk_nwc_lights.IsChecked
    nwc_options.ConvertElementProperties = True

    output.print_md("# Batch Export NWC Files")
    if is_dry_run:
        output.print_md("### ⚠️ DRY RUN - log only, no NWC files will be written.")
        
    # Setup results container for summary reporting
    results = []
    
    # Subscribe to dialog box showing events to suppress popups
    uiapp = __revit__ # type: ignore
    app = uiapp.Application
    
    uiapp.DialogBoxShowing += dismiss_dialog
    app.FailuresProcessing += failures_processing_handler
    logger.info("Enabled automatic dialog box suppression and failures/warnings preprocessor.")
    
    try:
        total_files = len(rvt_files)
        with joolslib.OutputProgress(output, "Exporting NWC Files", total_files) as pb:
            for i, file_path in enumerate(rvt_files):
                file_name = os.path.basename(file_path)
                
                # Check for cancellation
                if pb.cancelled:
                    logger.info("Export cancelled by user.")
                    output.print_md("### ⚠️ Export Cancelled by User")
                    break
                    
                # Update progress bar
                pb.update_progress(i, total_files)
                
                output.print_md("---")
                output.print_md("### Model: **{}**".format(file_name))
                
                temp_doc = None
                status = "Failed"
                details = "Unknown error"
                
                try:
                    # Set open options: DETACH & PRESERVE WORKSETS (per user feedback)
                    options = DB.OpenOptions()
                    options.DetachFromCentralOption = DB.DetachFromCentralOption.DetachAndPreserveWorksets
                    
                    logger.info("Opening detached model: {}".format(file_name))
                    m_path = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(file_path)
                    temp_doc = app.OpenDocumentFile(m_path, options)
                    
                    if temp_doc:
                        # Step A: Setup Coordination View
                        coord_view = setup_coordination_view(temp_doc, view_name)
                        
                        if not coord_view:
                            logger.warning("Coordination view was not created.")
                            
                        # Step B: Export NWC
                        if coord_view:
                            if is_dry_run:
                                logger.info("[DRY RUN] Would export NWC for: {}".format(file_name))
                                status = "Success"
                                details = "Dry Run (no NWC written)"
                            else:
                                nwc_success = export_nwc_file(temp_doc, coord_view, dest_nwc_folder, file_name, nwc_options)
                                if nwc_success:
                                    status = "Success"
                                    details = "NWC Exported"
                                else:
                                    status = "Failed"
                                    details = "NWC Export Failed"
                        else:
                            details = "Coordination view not found/created"
                            
                        temp_doc.Close(False) # Always close without saving
                    else:
                        logger.error("Failed to open document file.")
                        details = "Could not open document background"
                except Exception as e:
                    logger.error("Error processing model {}: {}".format(file_name, e))
                    details = str(e)
                    if temp_doc:
                        try:
                            temp_doc.Close(False)
                        except:
                            pass
                            
                results.append([file_name, status, details])
    finally:
        # Unsubscribe to restore normal UI behavior
        uiapp.DialogBoxShowing -= dismiss_dialog
        app.FailuresProcessing -= failures_processing_handler
        logger.info("Disabled automatic dialog box suppression and failures/warnings preprocessor.")

    # 4. Print Summary table
    output.print_md("---")
    output.print_md("# Export Summary")
    output.print_table(results, columns=["Model Name", "Status", "Details"])

if __name__ == "__main__":
    main()