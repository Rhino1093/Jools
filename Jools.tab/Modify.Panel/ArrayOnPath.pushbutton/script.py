#! python
# coding: utf-8
from __future__ import division

import clr  # type: ignore

clr.AddReference("RevitAPI")  # type: ignore
clr.AddReference("RevitAPIUI")  # type: ignore

from Autodesk.Revit.Exceptions import OperationCanceledException  # type: ignore
from collections import OrderedDict
from System.Collections.Generic import List  # type: ignore
from pyrevit import DB, UI, forms, revit, script
from rpw.ui.forms import FlexForm, Label, TextBox, Button, ComboBox, CheckBox, Separator


__author__ = "Ryan Johnston"
__date__ = "2024-12-02"
__purpose__ = "Array selected family instances along a picked path curve."


doc = revit.doc
uidoc = revit.uidoc
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


if not hasattr(forms, "ask_for_number"):
    def _fallback_ask_for_number(prompt, default=None, title=None):
        default_text = "" if default is None else str(default)
        value = forms.ask_for_string(prompt=prompt, default=default_text, title=title)
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            forms.alert("Enter a numeric value.", title=title or "Input Error")
            return None

    forms.ask_for_number = _fallback_ask_for_number  # type: ignore


def _show_error(message):
    forms.alert(message, title="Array On Path")


class FamilyInstanceFilter(UI.Selection.ISelectionFilter):
    def AllowElement(self, element):
        return isinstance(element, DB.FamilyInstance)

    def AllowReference(self, reference, position):
        return False


class CurveElementFilter(UI.Selection.ISelectionFilter):
    def AllowElement(self, element):
        return isinstance(element, DB.CurveElement) and isinstance(
            element.Location, DB.LocationCurve
        )

    def AllowReference(self, reference, position):
        return False


def _collect_user_options():
    while True:
        components = [
            Label("Path Type"),
            ComboBox("path_type", PATH_OPTIONS, default="Detail / Model Line"),
            Separator(),
            Label("Placement Mode"),
            ComboBox("mode", MODE_OPTIONS, default="By Count"),
            Label("Total Count"),
            TextBox("count", Text="5"),
            Label("Spacing (project units)"),
            TextBox("spacing", Text="5.0"),
            CheckBox("fill_even", "Fill path evenly (spacing mode)", default=False),
            Separator(),
            CheckBox("include_start", "Include path start", default=True),
            CheckBox("include_end", "Include path end", default=True),
            CheckBox("align_to_path", "Align instances to path direction", default=True),
            Label("Alignment Axis"),
            ComboBox("axis_mode", AXIS_OPTIONS, default="Family X axis"),
            CheckBox("create_group", "Group resulting elements", default=True),
            Separator(),
            Button("Place Instances"),
        ]

        dialog = FlexForm("Array On Path", components)
        dialog.show()
        values = dialog.values
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
                forms.alert(
                    "Enter an integer greater than zero for count.", title="Array On Path"
                )
                continue
            if count_val <= 0:
                forms.alert("Count must be greater than zero.", title="Array On Path")
                continue
        else:
            raw_spacing = (values.get("spacing") or "").strip()
            try:
                spacing_val = float(raw_spacing)
            except ValueError:
                forms.alert("Enter a numeric spacing value.", title="Array On Path")
                continue
            if spacing_val <= 0:
                forms.alert("Spacing must be greater than zero.", title="Array On Path")
                continue
            spacing_val = _to_internal_length(spacing_val)
            if spacing_val <= TOL:
                forms.alert("Spacing must be greater than zero.", title="Array On Path")
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
        seed.Id.IntegerValue,
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

    with revit.Transaction("Array Elements On Path"):
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
