#! python3
import clr
import sys

# Load Revit API before pyrevit to avoid interface instantiation errors in CPython
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from pyrevit import revit, script

import math

# .NET / WPF Imports
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")
clr.AddReference("System.Xaml")

from Autodesk.Revit import DB # type: ignore
from Autodesk.Revit import UI # type: ignore
from Autodesk.Revit.Exceptions import OperationCanceledException # type: ignore
from System.Windows import Window, WindowStartupLocation, Thickness, WindowStyle # type: ignore
from System.Windows.Controls import StackPanel, Label, TextBox, Button # type: ignore

__author__ = "Ryan Johnston"
__date__ = "2026-02-16"
__purpose__ = "Equally distribute detail lines (CPython Compatible)."

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
logger = script.get_logger()

def get_input(title, label_text, default_val):
    """Simple WPF input window without inheritance to avoid CPython init issues."""
    win = Window()
    win.Title = str(title)
    win.Width = 300
    win.Height = 150
    win.WindowStartupLocation = WindowStartupLocation.CenterScreen
    win.Topmost = True
    win.WindowStyle = WindowStyle.ToolWindow
    
    panel = StackPanel()
    panel.Margin = Thickness(20)

    lbl = Label()
    lbl.Content = str(label_text)
    panel.Children.Add(lbl)

    txt = TextBox()
    txt.Text = str(default_val)
    txt.Margin = Thickness(0, 0, 0, 10)
    panel.Children.Add(txt)

    btn = Button()
    btn.Content = "Go"
    btn.IsDefault = True
    
    # Using a list to store the result to pass back from the event
    result = [None]
    
    def on_click(sender, e):
        result[0] = txt.Text
        win.Close()
    
    btn.Click += on_click
    panel.Children.Add(btn)

    win.Content = panel
    win.ShowDialog()
    return result[0]

def alert(msg, title="Equal Dist"):
    UI.TaskDialog.Show(title, msg)

def get_hidden_line_style():
    categories = doc.Settings.Categories
    line_cat = categories.get_Item(DB.BuiltInCategory.OST_Lines)
    for sub_cat in line_cat.SubCategories:
        if "<Hidden>" in sub_cat.Name:
            return sub_cat.GetGraphicsStyle(DB.GraphicsStyleType.Projection)
    return None

def are_parallel(v1, v2):
    return v1.CrossProduct(v2).IsZeroLength()

def get_line_direction(line):
    curve = line.GeometryCurve
    if isinstance(curve, DB.Line):
        return curve.Direction.Normalize()
    return None

def sort_parallel_lines(lines, direction):
    perp = DB.XYZ(direction.Y, -direction.X, 0).Normalize()
    def get_sort_val(l):
        pt = l.GeometryCurve.GetEndPoint(0)
        return pt.DotProduct(perp)
    return sorted(lines, key=get_sort_val)

def main():
    selection = [doc.GetElement(id) for id in uidoc.Selection.GetElementIds()]
    detail_lines = [el for el in selection if isinstance(el, DB.DetailLine)]
    
    active_view = doc.ActiveView
    if not isinstance(active_view, (DB.ViewPlan, DB.ViewDrafting, DB.ViewSection)):
        alert("This tool must be used in a plan, drafting, or section view.")
        return

    # GATHER DATA FIRST (Outside Transaction)
    mode = None
    params = {}

    if len(detail_lines) >= 3:
        direction = get_line_direction(detail_lines[0])
        if not direction:
            alert("Selected elements must be straight lines.")
            return
        for l in detail_lines[1:]:
            curr_dir = get_line_direction(l)
            if not curr_dir or not are_parallel(direction, curr_dir):
                alert("All selected lines must be parallel.")
                return
        mode = "DISTRIBUTE"

    elif len(detail_lines) == 2:
        direction = get_line_direction(detail_lines[0])
        curr_dir = get_line_direction(detail_lines[1])
        if not direction or not curr_dir or not are_parallel(direction, curr_dir):
            alert("Selected lines must be parallel straight lines.")
            return
        
        val = get_input("Equal Dist", "Number of lines to add between:", "1")
        if val is None: return
        try:
            params['num_to_add'] = int(val)
            mode = "ADD_BETWEEN"
        except ValueError:
            alert("Invalid number.")
            return

    else:
        try:
            params['pt_start'] = uidoc.Selection.PickPoint("Pick start point for distribution")
            params['pt_end'] = uidoc.Selection.PickPoint("Pick end point for distribution")
        except OperationCanceledException:
            return

        val = get_input("Equal Dist", "Total number of lines:", "3")
        if val is None: return
        try:
            params['total_count'] = int(val)
            if params['total_count'] < 2:
                alert("Total lines must be at least 2.")
                return
            mode = "CREATE_NEW"
        except ValueError:
            alert("Invalid number.")
            return

    # EXECUTE (Inside Transaction)
    try:
        with revit.Transaction("Equal Dist"):
            if mode == "DISTRIBUTE":
                direction = get_line_direction(detail_lines[0])
                sorted_lines = sort_parallel_lines(detail_lines, direction)
                first_pt = sorted_lines[0].GeometryCurve.GetEndPoint(0)
                last_pt = sorted_lines[-1].GeometryCurve.GetEndPoint(0)
                perp = DB.XYZ(direction.Y, -direction.X, 0).Normalize()
                total_dist = (last_pt - first_pt).DotProduct(perp)
                spacing = total_dist / (len(sorted_lines) - 1)
                
                for i, line in enumerate(sorted_lines[1:-1], 1):
                    curr_pt = line.GeometryCurve.GetEndPoint(0)
                    target_dist = i * spacing
                    current_dist = (curr_pt - first_pt).DotProduct(perp)
                    move_vec = perp * (target_dist - current_dist)
                    DB.ElementTransformUtils.MoveElement(doc, line.Id, move_vec)

            elif mode == "ADD_BETWEEN":
                num_to_add = params['num_to_add']
                direction = get_line_direction(detail_lines[0])
                sorted_lines = sort_parallel_lines(detail_lines, direction)
                first_line, last_line = sorted_lines[0], sorted_lines[1]
                perp = DB.XYZ(direction.Y, -direction.X, 0).Normalize()
                dist_vec = last_line.GeometryCurve.GetEndPoint(0) - first_line.GeometryCurve.GetEndPoint(0)
                total_perp_dist = dist_vec.DotProduct(perp)
                line_style = first_line.LineStyle
                spacing = total_perp_dist / (num_to_add + 1)
                
                for i in range(1, num_to_add + 1):
                    move_vec = perp * (i * spacing)
                    new_curve = first_line.GeometryCurve.CreateTransformed(DB.Transform.CreateTranslation(move_vec))
                    new_line = doc.Create.NewDetailCurve(active_view, new_curve)
                    new_line.LineStyle = line_style

            elif mode == "CREATE_NEW":
                pt_start, pt_end = params['pt_start'], params['pt_end']
                total_count = params['total_count']
                vec = pt_end - pt_start
                dist = vec.GetLength()
                direction = vec.Normalize()
                line_dir = DB.XYZ(-direction.Y, direction.X, 0).Normalize()
                
                line_style = None
                if detail_lines:
                    line_style = detail_lines[0].LineStyle
                else:
                    line_style = get_hidden_line_style()
                
                spacing = dist / (total_count - 1)
                half_len = 2.0
                
                for i in range(total_count):
                    base_pt = pt_start + (direction * (i * spacing))
                    p1, p2 = base_pt + (line_dir * half_len), base_pt - (line_dir * half_len)
                    new_line = doc.Create.NewDetailCurve(active_view, DB.Line.CreateBound(p1, p2))
                    if line_style: new_line.LineStyle = line_style

    except Exception as e:
        alert("An error occurred: {}".format(str(e)))

if __name__ == "__main__":
    main()
