#! python3
import clr
import csv
import math

# Load Revit API and Windows Base
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")
clr.AddReference("System.Windows.Forms")

from Autodesk.Revit import DB # type: ignore
from Autodesk.Revit import UI # type: ignore
import System # type: ignore
from System.Windows import Window, WindowStartupLocation, Thickness, VerticalAlignment # type: ignore
from System.Windows.Controls import Grid, RowDefinition, TextBlock, ComboBox, Button # type: ignore
from System.Windows.Media import Brushes # type: ignore
from System.Windows.Forms import SaveFileDialog # type: ignore
from System.Collections.Generic import List # type: ignore

# Accessing Revit globals directly
uidoc = __revit__.ActiveUIDocument # type: ignore
doc = uidoc.Document

class ModelInfo:
    def __init__(self, name, x, y, z, angle, is_host=False):
        self.name = name
        self.x = x
        self.y = y
        self.z = z
        self.angle = angle
        self.is_host = is_host
        self.is_base = False
        self.dx = 0.0
        self.dy = 0.0
        self.dz = 0.0
        self.da = 0.0

def get_pbp_data(target_doc, name, is_host=False):
    """Extracts Project Base Point data using SharedPosition property."""
    print("DEBUG: Checking model: {}".format(name))
    try:
        collector = DB.FilteredElementCollector(target_doc).OfClass(DB.BasePoint)
        pbp = None
        for bp in collector:
            if not bp.IsShared: # Project Base Point
                pbp = bp
                break
        
        if not pbp:
            print("DEBUG: No PBP found in: {}".format(name))
            return None
            
        pos = pbp.SharedPosition
        x = pos.X
        y = pos.Y
        z = pos.Z
        
        # Angle to True North using direct ID
        angle_param = pbp.get_Parameter(System.Enum.ToObject(DB.BuiltInParameter, -1001504))
        angle = angle_param.AsDouble() if angle_param else 0.0
        
        print("DEBUG: Found PBP: E/W={:.4f}, N/S={:.4f}, Elev={:.4f}, Angle={:.4f}".format(x, y, z, math.degrees(angle)))
        return ModelInfo(name, x, y, z, angle, is_host)
    except Exception as ex:
        print("DEBUG: ERROR in {}: {}".format(name, str(ex)))
        return None

def main():
    print("DEBUG: Starting Alignment Tool...")
    # 1. Collect Data
    models = []
    host_info = get_pbp_data(doc, "Host: " + doc.Title, is_host=True)
    if host_info:
        models.append(host_info)
    
    links = DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkInstance)
    for link in links:
        try:
            link_doc = link.GetLinkDocument()
            if link_doc:
                link_info = get_pbp_data(link_doc, "Link: " + link_doc.Title)
                if link_info:
                    models.append(link_info)
        except Exception as ex:
            print("DEBUG: Error accessing link {}: {}".format(link.Name, str(ex)))
    
    if not models:
        UI.TaskDialog.Show("Alignment Tool", "No models with Project Base Points found.")
        return

    # 2. UI Selection (Pure Python instantiation)
    try:
        win = Window()
        win.Title = "Model Alignment Report"
        win.Height = 220
        win.Width = 450
        win.WindowStartupLocation = WindowStartupLocation.CenterScreen
        win.Background = Brushes.WhiteSmoke
        win.Topmost = True

        grid = Grid()
        grid.Margin = Thickness(20)
        
        # Row Definitions
        r1 = RowDefinition()
        r1.Height = System.Windows.GridLength.Auto
        r2 = RowDefinition()
        r2.Height = System.Windows.GridLength.Auto
        r3 = RowDefinition()
        
        grid.RowDefinitions.Add(r1)
        grid.RowDefinitions.Add(r2)
        grid.RowDefinitions.Add(r3)

        # Label
        lbl = TextBlock()
        lbl.Text = "Select the BASE model for alignment:"
        lbl.FontSize = 14
        lbl.FontWeight = System.Windows.FontWeights.Bold
        lbl.Margin = Thickness(0,0,0,10)
        Grid.SetRow(lbl, 0)
        grid.Children.Add(lbl)

        # Combo
        combo = ComboBox()
        combo.Height = 30
        combo.Margin = Thickness(0,0,0,20)
        combo.VerticalContentAlignment = VerticalAlignment.Center
        
        # Explicitly convert to .NET List[str] for CPython compatibility
        names_list = List[str]()
        for m in models:
            names_list.Add(m.name)
        combo.ItemsSource = names_list
        combo.SelectedIndex = 0
        
        Grid.SetRow(combo, 1)
        grid.Children.Add(combo)

        # Button
        btn = Button()
        btn.Content = "Generate Report & CSV"
        btn.Height = 40
        btn.FontSize = 14
        btn.FontWeight = System.Windows.FontWeights.Bold
        btn.Background = Brushes.SteelBlue
        btn.Foreground = Brushes.White
        Grid.SetRow(btn, 2)
        grid.Children.Add(btn)

        selected_base_name = [None]
        def on_click(sender, e):
            selected_base_name[0] = combo.SelectedItem
            win.DialogResult = True
            win.Close()
        btn.Click += on_click

        win.Content = grid
        if not win.ShowDialog():
            return
            
        base = next((m for m in models if m.name == selected_base_name[0]), None)
    except Exception as ui_ex:
        print("DEBUG: UI ERROR: {}".format(str(ui_ex)))
        UI.TaskDialog.Show("Alignment Tool", "UI failed to load. Check console.")
        return
        
    if not base: return
    
    # 3. Calculations
    for m in models:
        m.is_base = (m.name == base.name)
        m.dx = base.x - m.x
        m.dy = base.y - m.y
        m.dz = base.z - m.z
        m.da = base.angle - m.angle
        
    # 4. Output: Console Table
    print("\n" + "="*120)
    print("MODEL ALIGNMENT REPORT")
    print("Base Model: {}".format(base.name))
    print("="*120)
    header = "{:<40} | {:<5} | {:<12} | {:<12} | {:<12} | {:<10} | {:<12} | {:<12}".format(
        "Model Name", "Base", "E/W", "N/S", "Elev", "Angle", "Delta X", "Delta Y"
    )
    print(header)
    print("-" * 120)
    
    csv_rows = [["Model Name", "Is Base", "PBP E/W", "PBP N/S", "PBP Elev", "Angle", "Delta X", "Delta Y", "Delta Z", "Delta Angle"]]
    
    for m in models:
        line = "{:<40} | {:<5} | {:<12.4f} | {:<12.4f} | {:<12.4f} | {:<10.4f} | {:<12.4f} | {:<12.4f}".format(
            m.name[:38], "YES" if m.is_base else "no", m.x, m.y, m.z, math.degrees(m.angle), m.dx, m.dy
        )
        print(line)
        csv_rows.append([
            m.name, "YES" if m.is_base else "no", m.x, m.y, m.z, math.degrees(m.angle), m.dx, m.dy, m.dz, math.degrees(m.da)
        ])
    print("-" * 120)
    
    # 5. Output: CSV
    sfd = SaveFileDialog()
    sfd.Filter = "CSV Files (*.csv)|*.csv"
    sfd.FileName = "ModelAlignmentReport.csv"
    
    if sfd.ShowDialog() == System.Windows.Forms.DialogResult.OK:
        try:
            with open(sfd.FileName, mode='w', newline='') as f:
                writer = csv.writer(f)
                writer.writerows(csv_rows)
            UI.TaskDialog.Show("Alignment Tool", "CSV report saved to:\n" + sfd.FileName)
        except Exception as ex:
            UI.TaskDialog.Show("Alignment Tool", "Failed to save CSV: " + str(ex))

if __name__ == "__main__":
    main()
