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
app = doc.Application
output = script.get_output()
logger = script.get_logger()

XAML = """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Copy View Templates From Other Projects" Height="600" Width="500" 
        WindowStartupLocation="CenterScreen" Topmost="True" Background="#F0F0F0">
    <Grid Margin="15">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>
        
        <TextBlock Grid.Row="0" Text="1. Select Source Project:" FontWeight="Bold" Margin="0,0,0,5"/>
        <ComboBox Grid.Row="1" x:Name="SourceDocCombo" Margin="0,0,0,15" DisplayMemberPath="Title"/>
        
        <TextBlock Grid.Row="2" Text="2. Select Source View Template:" FontWeight="Bold" Margin="0,0,0,5"/>
        <ListBox Grid.Row="3" x:Name="SourceTemplateList" Margin="0,0,0,15" DisplayMemberPath="Name"/>
        
        <TextBlock Grid.Row="4" Text="3. Select Target View Template(s) in Current Project:" FontWeight="Bold" Margin="0,0,0,5"/>
        <ListBox Grid.Row="5" x:Name="TargetTemplateList" Margin="0,0,0,15">
            <ListBox.ItemTemplate>
                <DataTemplate>
                    <CheckBox Content="{Binding Name}" IsChecked="{Binding IsChecked, Mode=TwoWay}" VerticalContentAlignment="Center"/>
                </DataTemplate>
            </ListBox.ItemTemplate>
        </ListBox>
        
        <StackPanel Grid.Row="6" Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="BtnCopy" Content="Copy Overrides" Width="120" Height="30" Margin="0,0,10,0" IsDefault="True"/>
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

class CopyTemplateFiltersWindow:
    def __init__(self, open_docs, current_templates):
        self.open_docs = open_docs
        self.window = XamlReader.Parse(XAML)
        
        self.source_combo = self.window.FindName("SourceDocCombo")
        self.source_list = self.window.FindName("SourceTemplateList")
        self.target_list = self.window.FindName("TargetTemplateList")
        self.btn_copy = self.window.FindName("BtnCopy")
        self.btn_cancel = self.window.FindName("BtnCancel")
        
        # Populate documents and current templates
        source_net_docs = List[System.Object]()
        for d in open_docs: source_net_docs.Add(d)
        self.source_combo.ItemsSource = source_net_docs
        self.source_combo.SelectionChanged += self.on_source_changed
        
        target_items = [TemplateItem(t) for t in current_templates]
        sorted_targets = sorted(target_items, key=lambda x: x.Name)
        target_net_list = List[System.Object]()
        for i in sorted_targets: target_net_list.Add(i)
        self.target_list.ItemsSource = target_net_list
        
        self.btn_copy.Click += self.on_copy_click
        self.btn_cancel.Click += self.on_cancel_click
        
        self.selected_source_doc = None
        self.selected_source_template = None
        self.selected_target_templates = []
        self.success = False

    def on_source_changed(self, sender, e):
        self.selected_source_doc = self.source_combo.SelectedItem
        if self.selected_source_doc:
            templates = [v for v in DB.FilteredElementCollector(self.selected_source_doc).OfClass(DB.View) if v.IsTemplate]
            sorted_templates = sorted(templates, key=lambda x: x.Name)
            template_net_list = List[System.Object]()
            for t in sorted_templates: template_net_list.Add(t)
            self.source_list.ItemsSource = template_net_list

    def on_copy_click(self, sender, e):
        self.selected_source_template = self.source_list.SelectedItem
        if not self.selected_source_doc or not self.selected_source_template:
            UI.TaskDialog.Show("Error", "Please select a source project and template.")
            return
            
        self.selected_target_templates = [item.Element for item in self.target_list.ItemsSource if item.IsChecked]
        if not self.selected_target_templates:
            UI.TaskDialog.Show("Error", "Please select at least one target template.")
            return
            
        self.success = True
        self.window.Close()

    def on_cancel_click(self, sender, e):
        self.window.Close()

    def show(self):
        self.window.ShowDialog()
        return self.success

def get_or_copy_filter(source_filter, target_doc):
    """Finds a matching filter in the target doc or copies it if missing."""
    target_filters = DB.FilteredElementCollector(target_doc).OfClass(DB.ParameterFilterElement)
    for tf in target_filters:
        if tf.Name == source_filter.Name:
            return tf
            
    # Copy missing filter
    ids = List[DB.ElementId]()
    ids.Add(source_filter.Id)
    copied_ids = DB.ElementTransformUtils.CopyElements(source_filter.Document, ids, target_doc, None, DB.CopyPasteOptions())
    if copied_ids:
        copied_list = list(copied_ids)
        if copied_list:
            return target_doc.GetElement(copied_list[0])
    return None

def get_element_by_name(doc, class_type, name):
    """Helper to find an element by name and class."""
    collector = DB.FilteredElementCollector(doc).OfClass(class_type)
    for el in collector:
        if el.Name == name:
            return el
    return None

def clone_overrides(source_overrides, source_doc, target_doc):
    """Deep clones OverrideGraphicSettings by mapping referenced patterns by name."""
    new_overrides = DB.OverrideGraphicSettings()
    
    # Mapping table: (Property Name, Setter Method, Element Class or None)
    mapping = [
        ("Halftone", "SetHalftone", None),
        ("DetailLevel", "SetDetailLevel", None),
        ("Transparency", "SetSurfaceTransparency", None),
        ("ProjectionLineWeight", "SetProjectionLineWeight", None),
        ("ProjectionLineColor", "SetProjectionLineColor", None),
        ("ProjectionLinePatternId", "SetProjectionLinePatternId", DB.LinePatternElement),
        ("CutLineWeight", "SetCutLineWeight", None),
        ("CutLineColor", "SetCutLineColor", None),
        ("CutLinePatternId", "SetCutLinePatternId", DB.LinePatternElement),
        ("SurfaceForegroundPatternId", "SetSurfaceForegroundPatternId", DB.FillPatternElement),
        ("SurfaceForegroundPatternColor", "SetSurfaceForegroundPatternColor", None),
        ("IsSurfaceForegroundPatternVisible", "SetSurfaceForegroundPatternVisible", None),
        ("SurfaceBackgroundPatternId", "SetSurfaceBackgroundPatternId", DB.FillPatternElement),
        ("SurfaceBackgroundPatternColor", "SetSurfaceBackgroundPatternColor", None),
        ("IsSurfaceBackgroundPatternVisible", "SetSurfaceBackgroundPatternVisible", None),
        ("CutForegroundPatternId", "SetCutForegroundPatternId", DB.FillPatternElement),
        ("CutForegroundPatternColor", "SetCutForegroundPatternColor", None),
        ("IsCutForegroundPatternVisible", "SetCutForegroundPatternVisible", None),
        ("CutBackgroundPatternId", "SetCutBackgroundPatternId", DB.FillPatternElement),
        ("CutBackgroundPatternColor", "SetCutBackgroundPatternColor", None),
        ("IsCutBackgroundPatternVisible", "SetCutBackgroundPatternVisible", None),
        # Legacy Support
        ("ProjectionFillPatternId", "SetProjectionFillPatternId", DB.FillPatternElement),
        ("ProjectionFillColor", "SetProjectionFillColor", None),
        ("ProjectionFillPatternVisible", "SetProjectionFillPatternVisible", None),
        ("CutFillPatternId", "SetCutFillPatternId", DB.FillPatternElement),
        ("CutFillColor", "SetCutFillColor", None),
        ("CutFillPatternVisible", "SetCutFillPatternVisible", None),
    ]

    for prop_name, setter_name, element_class in mapping:
        if hasattr(source_overrides, prop_name) and hasattr(new_overrides, setter_name):
            val = getattr(source_overrides, prop_name)
            setter = getattr(new_overrides, setter_name)
            
            if element_class and isinstance(val, DB.ElementId):
                if val != DB.ElementId.InvalidElementId:
                    src_el = source_doc.GetElement(val)
                    if src_el:
                        tar_el = get_element_by_name(target_doc, element_class, src_el.Name)
                        if tar_el:
                            setter(tar_el.Id)
            elif isinstance(val, DB.Color):
                if val.IsValid:
                    setter(val)
            else:
                if prop_name in ["ProjectionLineWeight", "CutLineWeight"]:
                    if val > 0: setter(val)
                elif prop_name == "DetailLevel":
                    if val != DB.ViewDetailLevel.Undefined: setter(val)
                else:
                    setter(val)

    return new_overrides

def main():
    open_docs = [d for d in app.Documents if not d.IsLinked and d.Title != doc.Title]
    if not open_docs:
        UI.TaskDialog.Show("Copy View Templates From Other Projects"
, "No other open projects found.")
        return

    current_templates = [v for v in DB.FilteredElementCollector(doc).OfClass(DB.View) if v.IsTemplate]
    
    ui = CopyTemplateFiltersWindow(open_docs, current_templates)
    if not ui.show():
        return

    src_doc = ui.selected_source_doc
    src_template = ui.selected_source_template
    target_templates = ui.selected_target_templates
    
    with revit.Transaction("Copy View Template Filters"):
        try:
            src_filter_ids = src_template.GetFilters()
            filter_data = []
            
            for sfid in src_filter_ids:
                sf = src_doc.GetElement(sfid)
                overrides = src_template.GetFilterOverrides(sfid)
                visibility = src_template.GetFilterVisibility(sfid)
                
                target_filter = get_or_copy_filter(sf, doc)
                if target_filter:
                    tar_overrides = clone_overrides(overrides, src_doc, doc)
                    filter_data.append((target_filter, tar_overrides, visibility))
                else:
                    logger.warning(f"Could not copy/find filter: {sf.Name}")

            for target_template in target_templates:
                existing_filters = target_template.GetFilters()
                for efid in existing_filters:
                    target_template.RemoveFilter(efid)
                
                for target_filter, overrides, visibility in filter_data:
                    target_template.AddFilter(target_filter.Id)
                    target_template.SetFilterOverrides(target_filter.Id, overrides)
                    target_template.SetFilterVisibility(target_filter.Id, visibility)
                    
            UI.TaskDialog.Show("Copy View Templates From Other Projects"
, f"Successfully updated {len(target_templates)} templates with {len(filter_data)} filters each.")
        except Exception as ex:
            UI.TaskDialog.Show("Error", f"Failed to copy template filters: {str(ex)}")

if __name__ == "__main__":
    main()
