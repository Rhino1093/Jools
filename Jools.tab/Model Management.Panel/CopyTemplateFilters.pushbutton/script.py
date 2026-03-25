# -*- coding: utf-8 -*-
import clr
import sys

# .NET / Revit API / WPF Imports
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("System.Xaml")
clr.AddReference("WindowsBase")

from Autodesk.Revit import DB, UI # type: ignore
from System.Windows.Markup import XamlReader # type: ignore
from System.Collections.Generic import List # type: ignore
from pyrevit import script

# Use __revit__ directly for IronPython stability
uidoc = __revit__.ActiveUIDocument # type: ignore
doc = uidoc.Document
app = doc.Application
output = script.get_output()
logger = script.get_logger()

XAML = """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Copy Template Filters" Height="600" Width="500" 
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
                    <CheckBox Content="{Binding Name}" IsChecked="{Binding IsChecked}" VerticalContentAlignment="Center"/>
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
        self.source_combo.ItemsSource = open_docs
        self.source_combo.SelectionChanged += self.on_source_changed
        
        target_items = [TemplateItem(t) for t in current_templates]
        self.target_list.ItemsSource = sorted(target_items, key=lambda x: x.Name)
        
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
            self.source_list.ItemsSource = sorted(templates, key=lambda x: x.Name)

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
        return target_doc.GetElement(copied_ids[0])
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
    
    # 1. Line Patterns
    for prop in ["ProjectionLinePatternId", "CutLinePatternId"]:
        src_id = getattr(source_overrides, prop)
        if src_id != DB.ElementId.InvalidElementId:
            src_pat = source_doc.GetElement(src_id)
            if src_pat:
                tar_pat = get_element_by_name(target_doc, DB.LinePatternElement, src_pat.Name)
                if tar_pat:
                    setter = getattr(new_overrides, "Set" + prop)
                    setter(tar_pat.Id)

    # 2. Fill Patterns (Handles Foreground/Background for newer Revit versions)
    fill_props = [
        "ProjectionFillPatternId", "CutFillPatternId",
        "ProjectionBackgroundFillPatternId", "CutBackgroundFillPatternId"
    ]
    for prop in fill_props:
        if hasattr(source_overrides, prop): # Background props only exist in Revit 2019+
            src_id = getattr(source_overrides, prop)
            if src_id != DB.ElementId.InvalidElementId:
                src_pat = source_doc.GetElement(src_id)
                if src_pat:
                    tar_pat = get_element_by_name(target_doc, DB.FillPatternElement, src_pat.Name)
                    if tar_pat:
                        setter = getattr(new_overrides, "Set" + prop)
                        setter(tar_pat.Id)

    # 3. Colors
    color_props = [
        "ProjectionLineColor", "CutLineColor", 
        "ProjectionFillColor", "CutFillColor",
        "ProjectionBackgroundFillColor", "CutBackgroundFillColor"
    ]
    for prop in color_props:
        if hasattr(source_overrides, prop):
            color = getattr(source_overrides, prop)
            if color.IsValid:
                setter = getattr(new_overrides, "Set" + prop)
                setter(color)

    # 4. Numeric Values / Booleans
    simple_props = [
        "ProjectionLineWeight", "CutLineWeight", "Transparency", 
        "Halftone", "DetailLevel", 
        "ProjectionFillPatternVisible", "CutFillPatternVisible",
        "ProjectionBackgroundFillPatternVisible", "CutBackgroundFillPatternVisible"
    ]
    for prop in simple_props:
        if hasattr(source_overrides, prop):
            val = getattr(source_overrides, prop)
            setter = getattr(new_overrides, "Set" + prop)
            setter(val)

    return new_overrides

def main():
    # ... (rest of main logic)
    open_docs = [d for d in app.Documents if not d.IsLinked and d.Title != doc.Title]
    if not open_docs:
        UI.TaskDialog.Show("Copy Template Filters", "No other open projects found.")
        return

    current_templates = [v for v in DB.FilteredElementCollector(doc).OfClass(DB.View) if v.IsTemplate]
    
    ui = CopyTemplateFiltersWindow(open_docs, current_templates)
    if not ui.show():
        return

    src_doc = ui.selected_source_doc
    src_template = ui.selected_source_template
    target_templates = ui.selected_target_templates
    
    # 2. Extract filter data from source template
    src_filter_ids = src_template.GetFilters()
    filter_data = [] # List of tuples: (TargetFilter, Overrides, Visibility)
    
    for sfid in src_filter_ids:
        sf = src_doc.GetElement(sfid)
        overrides = src_template.GetFilterOverrides(sfid)
        visibility = src_template.GetFilterVisibility(sfid)
        
        # Ensure filter exists in target
        target_filter = get_or_copy_filter(sf, doc)
        if target_filter:
            filter_data.append((target_filter, overrides, visibility))
        else:
            logger.warning("Could not copy/find filter: {}".format(sf.Name))

    # 3. Apply to target templates
    t = DB.Transaction(doc, "Copy View Template Filters")
    t.Start()
    try:
        for target_template in target_templates:
            # Wipe existing filters (Replace mode)
            existing_filters = target_template.GetFilters()
            for efid in existing_filters:
                target_template.RemoveFilter(efid)
            
            # Apply new filters and overrides
            for target_filter, overrides, visibility in filter_data:
                target_template.AddFilter(target_filter.Id)
                target_template.SetFilterOverrides(target_filter.Id, overrides)
                target_template.SetFilterVisibility(target_filter.Id, visibility)
                
        t.Commit()
        UI.TaskDialog.Show("Copy Template Filters", "Successfully updated {} templates with {} filters each.".format(len(target_templates), len(filter_data)))
    except Exception as ex:
        if t.GetStatus() == DB.TransactionStatus.Started:
            t.RollBack()
        UI.TaskDialog.Show("Error", "Failed to copy template filters: " + str(ex))

if __name__ == "__main__":
    main()
