#! python
import os
import re
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

from pyrevit import forms, script

# Initialize pyRevit components
uidoc = __revit__.ActiveUIDocument # type: ignore
doc = uidoc.Document
app = __revit__.Application # type: ignore
logger = script.get_logger()
output = script.get_output()

# Settings file path in User Profile's AppData
SETTINGS_FILE = os.path.join(os.environ["APPDATA"], "pyRevit", "PDI_Startup_Settings.json")

XAML_STR = """<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="PDI Project Startup - Setup &amp; Export" 
        Height="600" Width="530"
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
            <TextBlock Text="PDI Project Startup" FontSize="18" FontWeight="Bold" Foreground="#3A96DD"/>
            <TextBlock Text="Configure coordination views, save backgrounds, and export Navisworks NWC files." FontSize="11" Foreground="#888888" Margin="0,2,0,0"/>
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

                <!-- Processing Run Modes -->
                <TextBlock Text="Run Actions:" FontWeight="SemiBold" FontSize="11" Margin="0,0,0,4"/>
                <StackPanel Orientation="Horizontal" Margin="0,0,0,10">
                    <CheckBox Name="chkProcessRvt" Content="Save Revit Backgrounds" Foreground="#FFFFFF" IsChecked="True" VerticalContentAlignment="Center" Margin="0,0,20,0"/>
                    <CheckBox Name="chkProcessNwc" Content="Export NWC Files" Foreground="#FFFFFF" IsChecked="True" VerticalContentAlignment="Center"/>
                </StackPanel>

                <!-- Destination RVT Folder -->
                <StackPanel Name="pnlDestRvt" Margin="0,0,0,10">
                    <TextBlock Text="Destination Autodesk Docs Folder (RVT):" FontWeight="SemiBold" FontSize="11" Margin="0,0,0,4"/>
                    <Grid>
                        <Grid.ColumnDefinitions>
                            <ColumnDefinition Width="*"/>
                            <ColumnDefinition Width="80"/>
                        </Grid.ColumnDefinitions>
                        <TextBox Name="txtDestRvtPath" Grid.Column="0" Height="24" Background="#2D2D2D" Foreground="#FFFFFF" BorderBrush="#3F3F46" Padding="3,1" VerticalContentAlignment="Center"/>
                        <Button Name="btnBrowseDestRvt" Content="Browse..." Grid.Column="1" Margin="5,0,0,0" Height="24" Background="#3E3E42" Foreground="#FFFFFF" BorderBrush="#3F3F46"/>
                    </Grid>
                </StackPanel>

                <!-- Destination NWC Folder -->
                <StackPanel Name="pnlDestNwc" Margin="0,0,0,10">
                    <TextBlock Text="Destination NWC Folder:" FontWeight="SemiBold" FontSize="11" Margin="0,0,0,4"/>
                    <Grid>
                        <Grid.ColumnDefinitions>
                            <ColumnDefinition Width="*"/>
                            <ColumnDefinition Width="80"/>
                        </Grid.ColumnDefinitions>
                        <TextBox Name="txtDestNwcPath" Grid.Column="0" Height="24" Background="#2D2D2D" Foreground="#FFFFFF" BorderBrush="#3F3F46" Padding="3,1" VerticalContentAlignment="Center"/>
                        <Button Name="btnBrowseDestNwc" Content="Browse..." Grid.Column="1" Margin="5,0,0,0" Height="24" Background="#3E3E42" Foreground="#FFFFFF" BorderBrush="#3F3F46"/>
                    </Grid>
                </StackPanel>

                <!-- Cloud Option Checkbox -->
                <CheckBox Name="chkSaveAsCloud" Content="Upload RVT as true Revit Cloud Model (via API)" Foreground="#FFFFFF" Margin="0,0,0,10" VerticalContentAlignment="Center"/>

                <!-- Cloud Settings Panel -->
                <Border Name="brdCloudSettings" BorderThickness="1" BorderBrush="#333333" Background="#252526" Padding="10" CornerRadius="3" Visibility="Collapsed" Margin="0,0,0,10">
                    <StackPanel>
                        <TextBlock Text="ACC Account ID (GUID):" FontWeight="SemiBold" FontSize="11" Margin="0,0,0,4"/>
                        <TextBox Name="txtAccountId" Height="24" Background="#2D2D2D" Foreground="#FFFFFF" BorderBrush="#3F3F46" Padding="3,1" VerticalContentAlignment="Center" Margin="0,0,0,8"/>

                        <TextBlock Text="ACC Project / Folder URL:" FontWeight="SemiBold" FontSize="11" Margin="0,0,0,4"/>
                        <TextBox Name="txtAccUrl" Height="24" Background="#2D2D2D" Foreground="#FFFFFF" BorderBrush="#3F3F46" Padding="3,1" VerticalContentAlignment="Center"/>
                    </StackPanel>
                </Border>

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
            <Button Name="btnStart" Content="Run Startup" Width="100" Height="26" Margin="10,0,0,0" Background="#0E639C" Foreground="#FFFFFF" BorderBrush="#3F3F46" FontWeight="SemiBold"/>
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
        self.txt_dest_rvt = self.window.FindName("txtDestRvtPath")
        self.txt_dest_nwc = self.window.FindName("txtDestNwcPath")
        
        self.chk_process_rvt = self.window.FindName("chkProcessRvt")
        self.chk_process_nwc = self.window.FindName("chkProcessNwc")
        self.chk_cloud = self.window.FindName("chkSaveAsCloud")
        self.brd_cloud = self.window.FindName("brdCloudSettings")
        self.txt_account = self.window.FindName("txtAccountId")
        self.txt_url = self.window.FindName("txtAccUrl")
        
        self.pnl_dest_rvt = self.window.FindName("pnlDestRvt")
        self.pnl_dest_nwc = self.window.FindName("pnlDestNwc")
        
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
        self.btn_browse_dest_rvt = self.window.FindName("btnBrowseDestRvt")
        self.btn_browse_dest_nwc = self.window.FindName("btnBrowseDestNwc")
        self.btn_start = self.window.FindName("btnStart")
        self.btn_cancel = self.window.FindName("btnCancel")
        
        # Register Event Handlers
        self.btn_browse_source.Click += self.on_browse_source
        self.btn_browse_dest_rvt.Click += self.on_browse_dest_rvt
        self.btn_browse_dest_nwc.Click += self.on_browse_dest_nwc
        self.chk_cloud.Checked += self.on_cloud_checked_changed
        self.chk_cloud.Unchecked += self.on_cloud_checked_changed
        
        self.chk_process_rvt.Checked += self.on_process_rvt_changed
        self.chk_process_rvt.Unchecked += self.on_process_rvt_changed
        self.chk_process_nwc.Checked += self.on_process_nwc_changed
        self.chk_process_nwc.Unchecked += self.on_process_nwc_changed
        
        self.btn_start.Click += self.on_start
        self.btn_cancel.Click += self.on_cancel
        
        # Load saved settings
        self.settings = load_settings()
        self.txt_source.Text = self.settings.get("source_path", "")
        self.txt_dest_rvt.Text = self.settings.get("dest_rvt_path", self.settings.get("dest_path", ""))
        self.txt_dest_nwc.Text = self.settings.get("dest_nwc_path", "")
        
        self.chk_process_rvt.IsChecked = self.settings.get("process_rvt", True)
        self.chk_process_nwc.IsChecked = self.settings.get("process_nwc", True)
        self.chk_cloud.IsChecked = self.settings.get("save_as_cloud", False)
        
        self.txt_account.Text = self.settings.get("account_id", "")
        self.txt_url.Text = self.settings.get("acc_url", "")
        
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
        
        self.update_rvt_visibility()
        self.update_nwc_visibility()
        self.is_running = False

    def update_cloud_visibility(self):
        """Toggles visibility of the Cloud Settings panel based on the checkbox state."""
        if self.chk_cloud.IsChecked:
            self.brd_cloud.Visibility = Visibility.Visible
        else:
            self.brd_cloud.Visibility = Visibility.Collapsed

    def on_cloud_checked_changed(self, sender, args):
        self.update_cloud_visibility()

    def on_process_rvt_changed(self, sender, args):
        self.update_rvt_visibility()

    def on_process_nwc_changed(self, sender, args):
        self.update_nwc_visibility()

    def update_rvt_visibility(self):
        if self.chk_process_rvt.IsChecked:
            self.pnl_dest_rvt.Visibility = Visibility.Visible
            self.chk_cloud.Visibility = Visibility.Visible
            self.update_cloud_visibility()
        else:
            self.pnl_dest_rvt.Visibility = Visibility.Collapsed
            self.chk_cloud.Visibility = Visibility.Collapsed
            self.brd_cloud.Visibility = Visibility.Collapsed

    def update_nwc_visibility(self):
        if self.chk_process_nwc.IsChecked:
            self.pnl_dest_nwc.Visibility = Visibility.Visible
            self.exp_nwc_settings.Visibility = Visibility.Visible
        else:
            self.pnl_dest_nwc.Visibility = Visibility.Collapsed
            self.exp_nwc_settings.Visibility = Visibility.Collapsed

    def on_browse_source(self, sender, args):
        dialog = FolderBrowserDialog()
        dialog.Description = "Select Source Folder containing Local Models"
        if self.txt_source.Text and os.path.exists(self.txt_source.Text):
            dialog.SelectedPath = self.txt_source.Text
        if dialog.ShowDialog() == DialogResult.OK:
            self.txt_source.Text = dialog.SelectedPath

    def on_browse_dest_rvt(self, sender, args):
        dialog = FolderBrowserDialog()
        dialog.Description = "Select Destination Autodesk Docs Folder (RVT)"
        if self.txt_dest_rvt.Text and os.path.exists(self.txt_dest_rvt.Text):
            dialog.SelectedPath = self.txt_dest_rvt.Text
        if dialog.ShowDialog() == DialogResult.OK:
            self.txt_dest_rvt.Text = dialog.SelectedPath

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
        dest_rvt = self.txt_dest_rvt.Text.strip()
        dest_nwc = self.txt_dest_nwc.Text.strip()
        
        if not source or not os.path.exists(source):
            forms.alert("Please select a valid source local folder.", title="Input Error")
            return
            
        process_rvt = self.chk_process_rvt.IsChecked
        process_nwc = self.chk_process_nwc.IsChecked
        
        if not process_rvt and not process_nwc:
            forms.alert("Please select at least one action (Save RVT or Export NWC) to run.", title="Input Error")
            return
            
        if process_rvt:
            if self.chk_cloud.IsChecked:
                account_id = self.txt_account.Text.strip()
                acc_url = self.txt_url.Text.strip()
                
                if not account_id:
                    forms.alert("Please enter a valid Account ID GUID.", title="Input Error")
                    return
                try:
                    Guid(account_id)
                except Exception:
                    forms.alert("Account ID is not a valid GUID format.", title="Input Error")
                    return
                    
                if not acc_url:
                    forms.alert("Please enter a valid ACC URL.", title="Input Error")
                    return
            else:
                if not dest_rvt or not os.path.exists(dest_rvt):
                    forms.alert("Please select a valid destination RVT folder.", title="Input Error")
                    return
                    
        if process_nwc:
            if not dest_nwc or not os.path.exists(dest_nwc):
                forms.alert("Please select a valid destination NWC folder.", title="Input Error")
                return
                
        # Save settings
        self.settings["source_path"] = source
        self.settings["dest_rvt_path"] = dest_rvt
        self.settings["dest_nwc_path"] = dest_nwc
        self.settings["process_rvt"] = process_rvt
        self.settings["process_nwc"] = process_nwc
        self.settings["save_as_cloud"] = self.chk_cloud.IsChecked
        self.settings["account_id"] = self.txt_account.Text.strip()
        self.settings["acc_url"] = self.txt_url.Text.strip()
        
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

def parse_acc_url(url):
    """Extracts Project ID and Folder URN from ACC URL."""
    try:
        proj_match = re.search(r'projects/([a-f0-9\-]{36})', url)
        folder_match = re.search(r'folderUrn=([^&]+)', url)
        if not proj_match or not folder_match: return None, None
        
        project_id = proj_match.group(1)
        try:
            from urllib.parse import unquote
        except ImportError:
            from urllib import unquote
        folder_urn = unquote(folder_match.group(1))
        return project_id, folder_urn
    except Exception as e:
        logger.warning(f"Error parsing URL: {e}")
        return None, None

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
    # 1. Initialize and Display UI
    form = StartupWindow()
    form.window.ShowDialog()
    
    if not form.is_running:
        logger.info("Execution cancelled by user.")
        return

    # 2. Extract settings from form
    source_folder = form.txt_source.Text.strip()
    dest_rvt_folder = form.txt_dest_rvt.Text.strip()
    dest_nwc_folder = form.txt_dest_nwc.Text.strip()
    process_rvt = form.chk_process_rvt.IsChecked
    process_nwc = form.chk_process_nwc.IsChecked
    save_as_cloud = form.chk_cloud.IsChecked
    
    account_guid_str = form.txt_account.Text.strip()
    acc_url = form.txt_url.Text.strip()
    
    # Check if NWC Exporter is available if NWC export is requested
    if process_nwc and not DB.OptionalFunctionalityUtils.IsNavisworksExporterAvailable():
        forms.alert("Navisworks exporter utility is not installed on this machine. Cannot export NWC files.", title="Error")
        return

    # 3. Gather local files
    local_rvt_files = [f for f in os.listdir(source_folder) if f.lower().endswith('.rvt')]
    if not local_rvt_files:
        forms.alert("No Revit models (.rvt) found in the selected source folder.")
        return

    view_name = "PDI Coordination"
    
    # Build NWC options object
    nwc_options = None
    if process_nwc:
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

    output.print_md("# Processing Models")
    
    # Setup results container for summary reporting
    results = []
    
    # Subscribe to dialog box showing events to suppress popups
    uiapp = __revit__ # type: ignore
    app = uiapp.Application
    
    uiapp.DialogBoxShowing += dismiss_dialog
    app.FailuresProcessing += failures_processing_handler
    logger.info("Enabled automatic dialog box suppression and failures/warnings preprocessor.")
    
    try:
        total_files = len(local_rvt_files)
        with forms.ProgressBar(title="Processing PDI Coordination Models", cancellable=True) as pb:
            for i, file_name in enumerate(local_rvt_files):
                # Check for cancellation
                if pb.cancelled:
                    logger.info("Processing cancelled by user.")
                    output.print_md("### ⚠️ Processing Cancelled by User")
                    break
                    
                # Update progress bar
                pb.update_progress(i, total_files)
                
                full_path = os.path.join(source_folder, file_name)
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
                    m_path = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(full_path)
                    temp_doc = app.OpenDocumentFile(m_path, options)
                    
                    if temp_doc:
                        # Step A: Setup PDI Coordination View
                        coord_view = setup_coordination_view(temp_doc, view_name)
                        
                        if not coord_view:
                            logger.warning("Coordination view was not created.")
                            
                        # Step B: Export NWC
                        nwc_success = False
                        if process_nwc and coord_view:
                            nwc_success = export_nwc_file(temp_doc, coord_view, dest_nwc_folder, file_name, nwc_options)
                        
                        # Step C: Save RVT Background
                        rvt_success = False
                        if process_rvt:
                            if save_as_cloud:
                                logger.info("Uploading model to ACC cloud database...")
                                project_id_str, folder_id = parse_acc_url(acc_url)
                                if not project_id_str:
                                    raise Exception("Could not parse Project ID/Folder URN from ACC URL.")
                                
                                account_guid = Guid(account_guid_str)
                                project_guid = Guid(project_id_str)
                                
                                temp_doc.SaveAsCloudModel(account_guid, project_guid, folder_id, file_name)
                                logger.info("Uploaded successfully as cloud model.")
                                rvt_success = True
                            else:
                                dest_path = os.path.join(dest_rvt_folder, file_name)
                                logger.info("Saving model locally to Autodesk Docs path: {}".format(dest_path))
                                
                                save_opts = DB.SaveAsOptions()
                                save_opts.OverwriteExistingFile = True
                                
                                if temp_doc.IsWorkshared:
                                    ws_opts = DB.WorksharingSaveAsOptions()
                                    ws_opts.SaveAsCentral = True
                                    save_opts.SetWorksharingOptions(ws_opts)
                                    logger.info("Model is workshared; saving as new Central file.")
                                    
                                temp_doc.SaveAs(dest_path, save_opts)
                                logger.info("Saved successfully to destination folder.")
                                rvt_success = True
                                
                        status = "Success"
                        # Set details message based on actions
                        action_details = []
                        if process_rvt and rvt_success:
                            action_details.append("RVT Saved")
                        elif process_rvt:
                            status = "Failed"
                            action_details.append("RVT Save Failed")
                            
                        if process_nwc and nwc_success:
                            action_details.append("NWC Exported")
                        elif process_nwc:
                            status = "Failed"
                            action_details.append("NWC Export Failed")
                            
                        details = ", ".join(action_details)
                        temp_doc.Close(False)
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
    output.print_md("# Processing Summary")
    output.print_table(results, columns=["Model Name", "Status", "Details"])

if __name__ == "__main__":
    main()
