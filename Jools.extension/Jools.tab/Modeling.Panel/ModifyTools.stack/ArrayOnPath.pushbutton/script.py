#! python3
# coding: utf-8
from __future__ import division

import clr  # type: ignore

import joolslib          # Jools.extension/lib, see CLAUDE.md section 6a
joolslib.install_events_shim()

clr.AddReference("RevitAPI")  # type: ignore
clr.AddReference("RevitAPIUI")  # type: ignore
clr.AddReference('System.Windows.Forms')  # type: ignore
clr.AddReference('System.Drawing')  # type: ignore

from Autodesk.Revit.Exceptions import OperationCanceledException  # type: ignore
from collections import OrderedDict
from System.Collections.Generic import List  # type: ignore
from System.Windows.Forms import (Form, Label, TextBox, Button, CheckBox, ComboBox,
                                  ComboBoxStyle, DialogResult, FormStartPosition,
                                  FormBorderStyle)  # type: ignore
from System.Drawing import Size, Point  # type: ignore
from pyrevit import DB, UI, script


__author__ = "Ryan Johnston"
__date__ = "2024-12-02"
__purpose__ = "Array selected family instances along a picked path curve."


uidoc = __revit__.ActiveUIDocument # type: ignore
doc = uidoc.Document


def eid_int(element_id):
    """///Summary: An ElementId's integer value, valid in Revit 2022-2026.

    Autodesk added ElementId.Value in 2024 and removed ElementId.IntegerValue
    in 2026, so neither attribute alone covers every Revit this extension is
    attached to. Written for both CPython 3 and IronPython 2.7.
    """
    value = getattr(element_id, "Value", None)
    return int(value) if value is not None else element_id.IntegerValue


output = script.get_output()
logger = script.get_logger()

TOL = 1e-06

PATH_OPTIONS = OrderedDict(
    [("Detail / Model Line", "line"), ("Edge", "edge")]
)
MODE_OPTIONS = OrderedDict(
    [("By Count", "count"), ("By Spacing", "spacing")]
)
AXIS_OPTIONS = OrderedDict(
    [("Family X axis", "x"), ("Family Y axis", "y")]
)




def _show_error(message):
    joolslib.alert(message, title="Array On Path")


# pythonnet emits a proxy type <__namespace__>.<ClassName> into a dynamic assembly
# that survives for the whole Revit session, so a fixed name collides with
# "Duplicate type name within an assembly" on the second run.
_NS = joolslib.unique_namespace("ArrayOnPath")

class FamilyInstanceFilter(UI.Selection.ISelectionFilter):
    # Required by pythonnet to emit the interface proxy type; must be unique per
    # execution (see _NS above and CLAUDE.md section 2.7).
    __namespace__ = _NS

    def AllowElement(self, element):
        return isinstance(element, DB.FamilyInstance)

    def AllowReference(self, reference, position):
        return False


class CurveElementFilter(UI.Selection.ISelectionFilter):
    # Required by pythonnet to emit the interface proxy type; must be unique per
    # execution (see _NS above and CLAUDE.md section 2.7).
    __namespace__ = _NS

    def AllowElement(self, element):
        return isinstance(element, DB.CurveElement) and isinstance(
            element.Location, DB.LocationCurve
        )

    def AllowReference(self, reference, position):
        return False


class _ArrayOptionsDialog(Form):
    """///Summary: Options dialog for Array On Path.

    Replaces the rpw FlexForm this tool used under IronPython. rpw cannot load
    on pyRevit's CPython 3.12 engine at all: rpw/utils/sphinx_compat.py does
    `import imp`, and the imp module was removed in Python 3.12.

    Exposes .values as an rpw-compatible dict so the calling validation loop is
    unchanged. Combo boxes display the OrderedDict keys and return their values.
    """

    def __init__(self):

        # pythonnet does not run the .NET base constructor implicitly the way
        # IronPython did; without it the control is uninitialised and the first
        # `self.Text = ...` raises NullReferenceException.
        super().__init__()
        self.values = None
        self._combos = {}
        self._texts = {}
        self._checks = {}

        self.Text = "Array On Path"
        self.ClientSize = Size(400, 500)
        self.FormBorderStyle = FormBorderStyle.FixedDialog
        self.StartPosition = FormStartPosition.CenterScreen
        self.MinimizeBox = False
        self.MaximizeBox = False

        y = 12

        y = self._add_combo("path_type", "Path Type", PATH_OPTIONS,
                            "Detail / Model Line", y)
        y = self._add_combo("mode", "Placement Mode", MODE_OPTIONS, "By Count", y)
        y = self._add_text("count", "Total Count", "5", y)
        y = self._add_text("spacing", "Spacing (project units)", "5.0", y)
        y = self._add_check("fill_even", "Fill path evenly (spacing mode)", False, y)
        y = self._add_check("include_start", "Include path start", True, y)
        y = self._add_check("include_end", "Include path end", True, y)
        y = self._add_check("align_to_path", "Align instances to path direction", True, y)
        y = self._add_combo("axis_mode", "Alignment Axis", AXIS_OPTIONS,
                            "Family X axis", y)
        y = self._add_check("create_group", "Group resulting elements", True, y)

        ok = Button()
        ok.Text = "Place Instances"
        ok.Size = Size(140, 26)
        ok.Location = Point(120, y + 10)
        ok.DialogResult = DialogResult.OK
        ok.Click += self._on_ok
        self.Controls.Add(ok)

        cancel = Button()
        cancel.Text = "Cancel"
        cancel.Size = Size(90, 26)
        cancel.Location = Point(270, y + 10)
        cancel.DialogResult = DialogResult.Cancel
        self.Controls.Add(cancel)

        self.AcceptButton = ok
        self.CancelButton = cancel
        self.ClientSize = Size(400, y + 52)

    def _add_label(self, text, y):
        label = Label()
        label.Text = text
        label.Location = Point(12, y)
        label.AutoSize = True
        self.Controls.Add(label)
        return y + 20

    def _add_combo(self, key, caption, options, default_key, y):
        y = self._add_label(caption, y)
        combo = ComboBox()
        combo.DropDownStyle = ComboBoxStyle.DropDownList
        combo.Location = Point(12, y)
        combo.Size = Size(370, 24)
        for display in options.keys():
            combo.Items.Add(display)
        combo.SelectedItem = default_key
        self.Controls.Add(combo)
        self._combos[key] = (combo, options)
        return y + 32

    def _add_text(self, key, caption, default, y):
        y = self._add_label(caption, y)
        box = TextBox()
        box.Text = default
        box.Location = Point(12, y)
        box.Size = Size(370, 24)
        self.Controls.Add(box)
        self._texts[key] = box
        return y + 32

    def _add_check(self, key, caption, default, y):
        box = CheckBox()
        box.Text = caption
        box.Checked = default
        box.Location = Point(12, y)
        box.Size = Size(370, 22)
        self.Controls.Add(box)
        self._checks[key] = box
        return y + 26

    def _on_ok(self, sender, args):
        """Snapshot control state before the form closes."""
        values = {}
        for key, (combo, options) in self._combos.items():
            # Map the displayed label back to the OrderedDict's value.
            values[key] = options.get(combo.SelectedItem)
        for key, box in self._texts.items():
            values[key] = box.Text
        for key, box in self._checks.items():
            values[key] = box.Checked
        self.values = values


def _show_options_dialog():
    """///Summary: Show the options dialog. Returns an rpw-style dict, or None."""
    dialog = _ArrayOptionsDialog()
    if dialog.ShowDialog() != DialogResult.OK:
        return None
    return dialog.values


def _collect_user_options():
    while True:
        values = _show_options_dialog()
        if not values:
            return None

        path_choice = values.get("path_type") or "line"
        mode = values.get("mode") or "count"

        count_val = None
        spacing_val = None
        fill_even = bool(values.get("fill_even"))

        if mode == "count":
            raw_count = (values.get("count") or "").strip()
            try:
                count_val = int(raw_count)
            except ValueError:
                joolslib.alert(
                    "Enter an integer greater than zero for count.", title="Array On Path"
                )
                continue
            if count_val <= 0:
                joolslib.alert("Count must be greater than zero.", title="Array On Path")
                continue
        else:
            raw_spacing = (values.get("spacing") or "").strip()
            try:
                spacing_val = float(raw_spacing)
            except ValueError:
                joolslib.alert("Enter a numeric spacing value.", title="Array On Path")
                continue
            if spacing_val <= 0:
                joolslib.alert("Spacing must be greater than zero.", title="Array On Path")
                continue
            spacing_val = _to_internal_length(spacing_val)
            if spacing_val <= TOL:
                joolslib.alert("Spacing must be greater than zero.", title="Array On Path")
                continue

        include_start = bool(values.get("include_start"))
        include_end = bool(values.get("include_end"))
        align_to_path = bool(values.get("align_to_path"))

        axis_mode = values.get("axis_mode") or "x"
        create_group = bool(values.get("create_group"))

        result = {
            "path_type": path_choice,
            "mode": mode,
            "count": count_val,
            "spacing": spacing_val,
            "include_start": include_start,
            "include_end": include_end,
            "fill_even": fill_even if mode == "spacing" else False,
            "align_to_path": align_to_path,
            "axis_mode": axis_mode,
            "create_group": create_group,
        }
        logger.debug("Options collected: %s", result)
        return result


def _to_internal_length(value):
    units = doc.GetUnits()
    try:
        fo = units.GetFormatOptions(DB.SpecTypeId.Length)
        unit_id = fo.GetUnitTypeId()
        return DB.UnitUtils.ConvertToInternalUnits(value, unit_id)
    except AttributeError:
        fo = units.GetFormatOptions(DB.UnitType.UT_Length)
        dut = fo.DisplayUnits
        return DB.UnitUtils.ConvertToInternalUnits(value, dut)


def _from_internal_length(value):
    units = doc.GetUnits()
    try:
        fo = units.GetFormatOptions(DB.SpecTypeId.Length)
        unit_id = fo.GetUnitTypeId()
        return DB.UnitUtils.ConvertFromInternalUnits(value, unit_id)
    except AttributeError:
        fo = units.GetFormatOptions(DB.UnitType.UT_Length)
        dut = fo.DisplayUnits
        return DB.UnitUtils.ConvertFromInternalUnits(value, dut)


def _normalize_xy(vector):
    flat = DB.XYZ(vector.X, vector.Y, 0.0)
    length = flat.GetLength()
    if length < TOL:
        return None
    return DB.XYZ(flat.X / length, flat.Y / length, 0.0)


def _signed_planar_angle(src_vec, dst_vec):
    src = _normalize_xy(src_vec)
    dst = _normalize_xy(dst_vec)
    if not src or not dst:
        return None
    angle = src.AngleTo(dst)
    cross = src.CrossProduct(dst)
    if cross.Z < 0:
        angle = -angle
    if abs(angle) < TOL:
        return None
    return angle


def _linspace(start, end, count):
    if count <= 1:
        return [start]
    step = (end - start) / float(count - 1)
    return [start + i * step for i in range(count)]


def _params_by_count(count, include_start, include_end):
    if count <= 0:
        raise ValueError("Count must be greater than zero.")
    if count == 1:
        if include_start:
            return [0.0]
        if include_end:
            return [1.0]
        return [0.5]

    if include_start and include_end:
        return [i / float(count - 1) for i in range(count)]
    if include_start and not include_end:
        return [i / float(count) for i in range(count)]
    if not include_start and include_end:
        return [(i + 1) / float(count) for i in range(count)]
    step = 1.0 / float(count + 1)
    return [(i + 1) * step for i in range(count)]


def _distances_by_spacing(length, spacing, include_start, include_end, fill_evenly):
    if spacing <= TOL:
        raise ValueError("Spacing must be greater than zero.")
    if length <= TOL:
        raise ValueError("Path length is too short.")

    positions = []
    if include_start:
        positions.append(0.0)
        current = spacing
    else:
        current = spacing

    last_allowed = length if include_end else max(length - spacing, 0.0)
    last_allowed = max(0.0, last_allowed)

    while current <= last_allowed + TOL:
        if current > length + TOL:
            break
        positions.append(min(current, length))
        current += spacing

    if include_end:
        if not positions or abs(positions[-1] - length) > TOL:
            positions.append(length)
        else:
            positions[-1] = length

    if not positions:
        raise ValueError("Spacing does not fit any instances on the selected path.")

    if fill_evenly and len(positions) >= 2:
        start_val = positions[0]
        end_val = positions[-1]
        if abs(end_val - start_val) > TOL:
            positions = _linspace(start_val, end_val, len(positions))

    return [max(0.0, min(length, d)) for d in positions]


def _pick_family_instance():
    ref = uidoc.Selection.PickObject(
        UI.Selection.ObjectType.Element,
        FamilyInstanceFilter(),
        "Select the family instance to array.",
    )
    return doc.GetElement(ref.ElementId)


def _pick_path_curve(path_choice):
    if path_choice == "edge":
        edge_ref = uidoc.Selection.PickObject(
            UI.Selection.ObjectType.Edge, "Select an edge to use as the array path."
        )
        host = doc.GetElement(edge_ref.ElementId)
        geom_obj = host.GetGeometryObjectFromReference(edge_ref)
        edge = geom_obj if isinstance(geom_obj, DB.Edge) else None
        if not edge:
            raise RuntimeError("Unable to read edge geometry.")
        return edge.AsCurve()

    curve_ref = uidoc.Selection.PickObject(
        UI.Selection.ObjectType.Element,
        CurveElementFilter(),
        "Select a detail or model line to use as the array path.",
    )
    curve_elem = doc.GetElement(curve_ref.ElementId)
    loc = curve_elem.Location
    if not isinstance(loc, DB.LocationCurve):
        raise RuntimeError("Selected element is not curve-based.")
    return loc.Curve


def _build_placements(seed, curve, options):
    length = curve.Length
    logger.info(
        "Building placements (seed id %s, mode %s, length %s)",
        eid_int(seed.Id),
        options["mode"],
        length,
    )
    if length <= TOL:
        raise ValueError("Selected path has zero length.")

    include_start = options["include_start"]
    include_end = options["include_end"]

    if (
        not include_start
        and not include_end
        and options["mode"] == "count"
        and options["count"] == 1
    ):
        raise ValueError("Cannot exclude both endpoints with a single instance.")

    if options["mode"] == "count":
        params = _params_by_count(options["count"], include_start, include_end)
    else:
        distances = _distances_by_spacing(
            length,
            options["spacing"],
            include_start,
            include_end,
            options["fill_even"],
        )
        params = [max(0.0, min(1.0, d / length)) for d in distances]

    placements = []
    seen = set()

    for param in params:
        clamped = max(0.0, min(1.0, param))
        key = round(clamped, 6)
        if key in seen:
            continue
        seen.add(key)
        point = curve.Evaluate(clamped, True)
        derivatives = curve.ComputeDerivatives(clamped, True)
        tangent = derivatives.BasisX
        placements.append({"point": point, "tangent": tangent, "param": clamped})

    if not placements:
        raise ValueError("No placements could be created with the provided options.")

    base_loc = seed.Location
    if not isinstance(base_loc, DB.LocationPoint):
        raise ValueError("Selected family instance must be point-based.")
    base_point = base_loc.Point

    if options["align_to_path"]:
        axis_vec = seed.HandOrientation if options["axis_mode"] == "x" else seed.FacingOrientation
    else:
        axis_vec = None

    base_z = base_point.Z

    for data in placements:
        target_point = data["point"]
        flattened_target = DB.XYZ(target_point.X, target_point.Y, base_z)
        translation = flattened_target - base_point
        data["translation"] = translation
        if options["align_to_path"] and axis_vec:
            angle = _signed_planar_angle(axis_vec, data["tangent"])
            data["rotation"] = angle
        else:
            data["rotation"] = None

    logger.info("Placements generated: %s entries", len(placements))
    return placements


def _apply_transform(element_id, translation, rotation, rotation_point):
    if translation and translation.GetLength() > TOL:
        DB.ElementTransformUtils.MoveElement(doc, element_id, translation)
    if rotation and abs(rotation) > TOL and rotation_point:
        axis = DB.Line.CreateBound(
            rotation_point - DB.XYZ.BasisZ * 10.0,
            rotation_point + DB.XYZ.BasisZ * 10.0,
        )
        DB.ElementTransformUtils.RotateElement(doc, element_id, axis, rotation)


def main():
    logger.debug("Starting Array On Path execution")
    try:
        seed = _pick_family_instance()
        options = _collect_user_options()
    except OperationCanceledException:
        script.exit()

    if options is None:
        script.exit()

    path_choice = options.pop("path_type")

    try:
        curve = _pick_path_curve(path_choice)
    except OperationCanceledException:
        script.exit()

    if curve is None:
        script.exit()

    try:
        placements = _build_placements(seed, curve, options)
    except Exception as ex:
        logger.error("Failed to build placements: %s", ex)
        _show_error(str(ex))
        return

    logger.debug("Computed placements: %s entries", len(placements))

    if not placements:
        _show_error("No valid placements were generated.")
        return

    created_ids = []
    seed_id = seed.Id

    t = DB.Transaction(doc, "Array Elements On Path")
    t.Start()
    try:
        for placement in placements[1:]:
            copies = DB.ElementTransformUtils.CopyElement(doc, seed_id, DB.XYZ.Zero)
            new_id = list(copies)[0]
            _apply_transform(
                new_id, placement["translation"], placement["rotation"], placement["point"]
            )
            created_ids.append(new_id)

        first = placements[0]
        _apply_transform(seed_id, first["translation"], first["rotation"], first["point"])

        if options["create_group"]:
            group_ids = List[DB.ElementId]()
            for eid in [seed_id] + created_ids:
                group_ids.Add(eid)
            if group_ids.Count > 0:
                doc.Create.NewGroup(group_ids)
        t.Commit()
    except Exception as ex:
        if t.GetStatus() == DB.TransactionStatus.Started:
            t.RollBack()
        logger.error("Transaction failed: %s", ex)
        _show_error(str(ex))
        return

    total = len(created_ids) + 1
    logger.debug(
        "Placement complete: total=%s, grouped=%s", total, options["create_group"]
    )
    output.print_md("**Array On Path**")
    output.print_md("- Placed {} instances along selected path.".format(total))
    if options["mode"] == "count":
        output.print_md("- Mode: by count ({})".format(options["count"]))
    else:
        spacing_display = _from_internal_length(options["spacing"])
        output.print_md("- Mode: by spacing ({:.2f})".format(spacing_display))
    output.print_md("- Grouped: {}".format("Yes" if options["create_group"] else "No"))


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        logger.error("Array On Path failed unexpectedly: %s", err)
        _show_error("Array On Path failed:\n{}".format(err))
