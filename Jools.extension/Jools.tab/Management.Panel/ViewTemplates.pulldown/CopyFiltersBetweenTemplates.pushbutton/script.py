#! python3
import clr
import sys
from types import ModuleType

# --- CPYTHON WORKAROUND ---
# Bypasses "interface takes exactly one argument" error in pyrevit.revit.events
try:
    mock_events = ModuleType('pyrevit.revit.events')
    mock_events._HANDLER = None
    sys.modules['pyrevit.revit.events'] = mock_events
except Exception:
    pass
# --------------------------

# .NET / Revit API / WPF Imports
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("System.Xaml")
clr.AddReference("WindowsBase")

from Autodesk.Revit import DB, UI # type: ignore
import System # type: ignore
from System.Windows.Markup import XamlReader # type: ignore
from System.Collections.Generic import List # type: ignore
from pyrevit import revit, script

# Standard pyRevit variables
doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
logger = script.get_logger()

XAML = """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Copy Filters Between Templates" Height="600" Width="500" 
        WindowStartupLocation="CenterScreen" Topmost="True" Background="#F0F0F0">
    <Grid Margin="15">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>
        
        <TextBlock Grid.Row="0" Text="1. Select Source View Template:" FontWeight="Bold" Margin="0,0,0,5"/>
        <ListBox Grid.Row="1" x:Name="SourceTemplateList" Margin="0,0,0,15" DisplayMemberPath="Name"/>
        
        <TextBlock Grid.Row="2" Text="2. Select Target View Template(s):" FontWeight="Bold" Margin="0,0,0,5"/>
        <ListBox Grid.Row="3" x:Name="TargetTemplateList" Margin="0,0,0,15">
            <ListBox.ItemTemplate>
                <DataTemplate>
                    <CheckBox Content="{Binding Name}" IsChecked="{Binding IsChecked, Mode=TwoWay}" VerticalContentAlignment="Center"/>
                </DataTemplate>
            </ListBox.ItemTemplate>
        </ListBox>
        
        <StackPanel Grid.Row="4" Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="BtnCopy" Content="Copy Filters" Width="120" Height="30" Margin="0,0,10,0" IsDefault="True"/>
            <Button x:Name="BtnCancel" Content="Cancel" Width="80" Height="30" IsCancel="True"/>
        </StackPanel>
    </Grid>
</Window>
"""

class TemplateItem:
    def __init__(self, element):
        self.Element = element
        self.Name = element.Name
        self.IsChecked = False

class CopyFiltersWindow:
    def __init__(self, templates):
        self.templates = templates
        self.window = XamlReader.Parse(XAML)
        
        self.source_list = self.window.FindName("SourceTemplateList")
        self.target_list = self.window.FindName("TargetTemplateList")
        self.btn_copy = self.window.FindName("BtnCopy")
        self.btn_cancel = self.window.FindName("BtnCancel")
        
        # Populate template lists
        sorted_templates = sorted(templates, key=lambda x: x.Name)
        source_net_list = List[System.Object]()
        for t in sorted_templates: source_net_list.Add(t)
        self.source_list.ItemsSource = source_net_list
        
        self.target_items = [TemplateItem(t) for t in sorted_templates]
        target_net_list = List[System.Object]()
        for i in self.target_items: target_net_list.Add(i)
        self.target_list.ItemsSource = target_net_list
        
        self.btn_copy.Click += self.on_copy_click
        self.btn_cancel.Click += self.on_cancel_click
        
        self.selected_source = None
        self.selected_targets = []
        self.success = False

    def on_copy_click(self, sender, e):
        self.selected_source = self.source_list.SelectedItem
        if not self.selected_source:
            UI.TaskDialog.Show("Error", "Please select a source template.")
            return
            
        self.selected_targets = [item.Element for item in self.target_items if item.IsChecked]
        if not self.selected_targets:
            UI.TaskDialog.Show("Error", "Please select at least one target template.")
            return
            
        # Prevent copying to self
        if self.selected_source in self.selected_targets:
            self.selected_targets.remove(self.selected_source)
            if not self.selected_targets:
                UI.TaskDialog.Show("Error", "Source and Target cannot be the same template.")
                return

        self.success = True
        self.window.Close()

    def on_cancel_click(self, sender, e):
        self.window.Close()

    def show(self):
        self.window.ShowDialog()
        return self.success

def main():
    templates = [v for v in DB.FilteredElementCollector(doc).OfClass(DB.View) if v.IsTemplate]
    if not templates:
        UI.TaskDialog.Show("Copy Filters", "No view templates found in this project.")
        return

    ui = CopyFiltersWindow(templates)
    if not ui.show():
        return

    src_template = ui.selected_source
    target_templates = ui.selected_targets
    
    with revit.Transaction("Copy Filters Between Templates"):
        try:
            # 1. Capture Source Data
            src_filter_ids = src_template.GetFilters()
            filter_configs = []
            for fid in src_filter_ids:
                overrides = src_template.GetFilterOverrides(fid)
                visibility = src_template.GetFilterVisibility(fid)
                filter_configs.append((fid, overrides, visibility))

            # 2. Apply to Targets
            for target in target_templates:
                # Remove existing filters
                existing_fids = target.GetFilters()
                for efid in existing_fids:
                    target.RemoveFilter(efid)
                
                # Add source filters
                for fid, overrides, visibility in filter_configs:
                    target.AddFilter(fid)
                    target.SetFilterOverrides(fid, overrides)
                    target.SetFilterVisibility(fid, visibility)
            
            UI.TaskDialog.Show("Success", f"Successfully copied filters from '{src_template.Name}' to {len(target_templates)} templates.")
        except Exception as ex:
            UI.TaskDialog.Show("Error", f"Failed to copy filters: {str(ex)}")

if __name__ == "__main__":
    main()
