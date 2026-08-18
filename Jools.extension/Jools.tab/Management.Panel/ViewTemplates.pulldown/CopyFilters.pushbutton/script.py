# -*- coding: utf-8 -*-
import clr

# .NET / Revit API / WPF Imports
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("System.Xaml")
clr.AddReference("WindowsBase")

from Autodesk.Revit import DB, UI # type: ignore
from System.Windows import Window, WindowStartupLocation # type: ignore
from System.Windows.Markup import XamlReader # type: ignore
from System.IO import StringReader # type: ignore
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
        Title="Copy Filters" Height="500" Width="400" 
        WindowStartupLocation="CenterScreen" Topmost="True" Background="#F0F0F0">
    <Grid Margin="15">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>
        
        <TextBlock Grid.Row="0" Text="1. Select Source Project:" FontWeight="Bold" Margin="0,0,0,5"/>
        <ComboBox Grid.Row="1" x:Name="SourceDocCombo" Margin="0,0,0,15" DisplayMemberPath="Title"/>
        
        <TextBlock Grid.Row="2" Text="2. Select Filters to Copy:" FontWeight="Bold" Margin="0,0,0,5"/>
        <ListBox Grid.Row="3" x:Name="FilterListBox" Margin="0,0,0,15">
            <ListBox.ItemTemplate>
                <DataTemplate>
                    <CheckBox Content="{Binding Name}" IsChecked="{Binding IsChecked}" VerticalContentAlignment="Center"/>
                </DataTemplate>
            </ListBox.ItemTemplate>
        </ListBox>
        
        <StackPanel Grid.Row="4" Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="BtnCopy" Content="Copy Selected" Width="100" Height="30" Margin="0,0,10,0" IsDefault="True"/>
            <Button x:Name="BtnCancel" Content="Cancel" Width="80" Height="30" IsCancel="True"/>
        </StackPanel>
    </Grid>
</Window>
"""

class FilterItem:
    def __init__(self, element):
        self.Element = element
        self.Name = element.Name
        self.IsChecked = False

class CopyFiltersWindow:
    def __init__(self, open_docs):
        self.open_docs = open_docs
        # Fix: XamlReader.Parse is much more reliable for strings in IronPython
        self.window = XamlReader.Parse(XAML)
        
        self.source_combo = self.window.FindName("SourceDocCombo")
        self.filter_list = self.window.FindName("FilterListBox")
        self.btn_copy = self.window.FindName("BtnCopy")
        self.btn_cancel = self.window.FindName("BtnCancel")
        
        # Populate documents
        self.source_combo.ItemsSource = open_docs
        self.source_combo.SelectionChanged += self.on_source_changed
        
        self.btn_copy.Click += self.on_copy_click
        self.btn_cancel.Click += self.on_cancel_click
        
        self.selected_source_doc = None
        self.selected_filters = []
        self.success = False

    def on_source_changed(self, sender, e):
        self.selected_source_doc = self.source_combo.SelectedItem
        if self.selected_source_doc:
            collector = DB.FilteredElementCollector(self.selected_source_doc).OfClass(DB.ParameterFilterElement)
            filters = [FilterItem(f) for f in collector]
            self.filter_list.ItemsSource = sorted(filters, key=lambda x: x.Name)

    def on_copy_click(self, sender, e):
        if not self.selected_source_doc:
            UI.TaskDialog.Show("Error", "Please select a source document.")
            return
            
        self.selected_filters = [item.Element for item in self.filter_list.ItemsSource if item.IsChecked]
        if not self.selected_filters:
            UI.TaskDialog.Show("Error", "Please select at least one filter.")
            return
            
        self.success = True
        self.window.Close()

    def on_cancel_click(self, sender, e):
        self.window.Close()

    def show(self):
        self.window.ShowDialog()
        return self.success

def main():
    # 1. Gather data
    open_docs = [d for d in app.Documents if not d.IsLinked and d.Title != doc.Title]
    if not open_docs:
        UI.TaskDialog.Show("Copy Filters", "No other open projects found. Please open the project you want to copy from.")
        return

    ui = CopyFiltersWindow(open_docs)
    if not ui.show():
        return

    source_doc = ui.selected_source_doc
    filters_to_copy = ui.selected_filters
    
    # 2. Check for conflicts and copy
    target_filter_names = [f.Name for f in DB.FilteredElementCollector(doc).OfClass(DB.ParameterFilterElement)]
    
    copied_count = 0
    conflict_report = []

    # Transaction in Source Doc to temporarily rename (rolled back)
    t_src = DB.Transaction(source_doc, "Temp Rename for Copy")
    t_src.Start()
    
    element_ids_to_copy = List[DB.ElementId]()
    
    for f in filters_to_copy:
        orig_name = f.Name
        if orig_name in target_filter_names:
            new_name = orig_name + "_Copy1"
            # Ensure new_name doesn't also exist (unlikely but possible)
            while new_name in target_filter_names:
                new_name += "_Copy1"
            
            try:
                f.Name = new_name
                conflict_report.append("- {} (renamed to {})".format(orig_name, new_name))
            except Exception as ex:
                logger.warning("Could not rename filter {}: {}".format(orig_name, str(ex)))
        
        element_ids_to_copy.Add(f.Id)

    # Transaction in Target Doc to receive elements
    t_target = DB.Transaction(doc, "Copy Filters")
    t_target.Start()
    try:
        options = DB.CopyPasteOptions()
        # Set handler for duplicate types if needed
        # options.SetDuplicateTypeNamesHandler(...) 
        
        copied_ids = DB.ElementTransformUtils.CopyElements(source_doc, element_ids_to_copy, doc, None, options)
        copied_count = len(copied_ids)
        t_target.Commit()
    except Exception as ex:
        UI.TaskDialog.Show("Error", "Failed to copy filters: " + str(ex))
        if t_target.GetStatus() == DB.TransactionStatus.Started:
            t_target.RollBack()
        if t_src.GetStatus() == DB.TransactionStatus.Started:
            t_src.RollBack()
        return

    # Roll back source transaction so we don't actually rename filters in the source file
    t_src.RollBack()

    # 3. Final Report
    msg = "Successfully copied {} filters.".format(copied_count)
    if conflict_report:
        msg += "\n\nThe following filters existed and were renamed:\n" + "\n".join(conflict_report)
    
    msg += "\n\nNote: If these filters use parameters not present in this project, they may need manual adjustment."
    
    UI.TaskDialog.Show("Copy Filters", msg)

if __name__ == "__main__":
    main()
