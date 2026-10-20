# -*- coding: utf-8 -*-
"""///Summary: Route fabrication piping between two open connectors.

Proof of concept for the fab_* tools of the Jools Routes API. The caller
(Claude, through the MCP server) chooses *what* to connect: two connector keys.
Everything geometric stays here. An LLM is unreliable with coordinates, so it
never supplies any.

Routing uses FabricationPart.StretchAndFit, the API behind Revit's Route and
Fill: starting at an open end of a fabrication *fitting*, it stretches the run
to a target connector and fills in straights and fittings from the part's own
service configuration. It does not pathfind. Revit either finds a solution for
the two ends as they sit, or returns a FabricationPartFitResult saying why not
(MisalignedEnds, OffsetRequired, SizeMismatch ...). The API refuses to stretch
from a straight, tap or hanger, so the start connector must belong to a fitting.

Connector keys round-trip through JSON: "<elementId>:<connectorId>".

Engine note: Python 2/3 common subset. Routes handlers run on IronPython 2.7
(see startup.py). No f-strings.
"""

import clr  # type: ignore

try:
    clr.AddReference("RevitAPI")  # type: ignore
except Exception:
    # Already loaded by the host under both engines.
    pass

import System  # type: ignore
from Autodesk.Revit import DB  # type: ignore
from Autodesk.Revit.DB import Fabrication  # type: ignore
from System.Collections.Generic import List  # type: ignore

from joolslib import eid_int

TRANSACTION_NAME = "Jools: fab route"

# ElementId(Int32) was removed in Revit 2026 (CLAUDE.md section 2.4).
try:
    _to_int64 = long          # noqa: F821 - IronPython 2.7 only
except NameError:
    _to_int64 = int

DOMAINS = {
    "piping": DB.Domain.DomainPiping,
    "hvac": DB.Domain.DomainHvac,
}

# Parts this tool will start a route from: the API rejects the rest.
_NON_STRETCHABLE = ("straight", "tap", "hanger")

# Every part created through this API since Revit started, so a later tool
# can offer to remove them. Mirrors rcpdims._CREATED_IDS.
_CREATED_IDS = set()


def _feet(value):
    """///Summary: A length rounded for JSON; full precision is noise to an LLM."""
    return round(value, 4)


def _point(xyz):
    """///Summary: An XYZ as a [x, y, z] list in feet, or None."""
    if xyz is None:
        return None
    return [_feet(xyz.X), _feet(xyz.Y), _feet(xyz.Z)]


def _config(doc):
    """///Summary: The project's FabricationConfiguration, or ValueError telling the user.

    GetFabricationConfiguration returns None when the project has no fabrication
    configuration loaded, which is the usual reason every fab tool comes back empty.
    """
    config = DB.FabricationConfiguration.GetFabricationConfiguration(doc)
    if config is None:
        raise ValueError(
            "This project has no fabrication configuration. In Revit: "
            "Manage tab > Fabrication Settings > Change Configuration, load "
            "your fabrication config, then retry.")
    return config


def _button_domain(button):
    """///Summary: The connector domain of a service button as text, or '?'.

    Condition 0 is used because -1 returns Undefined for mixed-domain buttons.
    """
    try:
        count = button.ConditionCount
        return str(button.GetDomain(0 if count > 0 else -1))
    except Exception:
        return "?"


def fab_info(doc, service_id=None):
    """///Summary: What fabrication content the project has, so a service can be chosen.

    Without service_id: the project's fabrication config name, every loaded
    service (id, name, abbreviation, domain, palette and button counts) and how
    many fabrication parts of each service are already placed.
    With service_id: that service's palettes and buttons in full.
    Read-only.
    """
    config = _config(doc)

    placed = {}
    collector = DB.FilteredElementCollector(doc).OfClass(DB.FabricationPart)
    for part in collector:
        name = part.ServiceName
        placed[name] = placed.get(name, 0) + 1

    info = None
    try:
        info = config.GetFabricationConfigurationInfo()
    except Exception:
        pass

    services = []
    for service in config.GetAllLoadedServices():
        if service_id is not None and service.ServiceId != int(service_id):
            continue

        palettes = []
        domain = "?"
        for p in range(service.PaletteCount):
            buttons = []
            for b in range(service.GetButtonCount(p)):
                button = service.GetButton(p, b)
                if domain == "?" and button.IsStraight:
                    domain = _button_domain(button)
                if service_id is not None:
                    buttons.append({
                        "index": b,
                        "name": button.Name,
                        "code": button.Code,
                        "conditions": button.ConditionCount,
                        "straight": bool(button.IsStraight),
                        "hanger": bool(button.IsAHanger),
                    })
            entry = {"index": p, "name": service.GetPaletteName(p),
                     "buttons": service.GetButtonCount(p)}
            if service_id is not None:
                entry["button_list"] = buttons
            palettes.append(entry)

        services.append({
            "id": service.ServiceId,
            "name": service.Name,
            "abbreviation": service.Abbreviation,
            "domain": domain,
            "placed_parts": placed.get(service.Name, 0),
            "palettes": palettes if service_id is not None else len(palettes),
        })

    if service_id is not None and not services:
        raise ValueError(
            "No loaded service has id {0}. Call fab_info without service_id "
            "for the list.".format(service_id))

    return {
        "config": str(info.Name) if info is not None and hasattr(info, "Name") else None,
        "services": services,
        "placed_by_service": placed,
    }


def _connector_key(element, connector):
    """///Summary: The JSON-safe key for one connector."""
    return "{0}:{1}".format(eid_int(element.Id), connector.Id)


def _connector_row(element, connector, is_fab):
    """///Summary: One open connector as a compact dict an LLM can reason about."""
    try:
        origin = _point(connector.Origin)
    except Exception:
        origin = None
    try:
        direction = _point(connector.CoordinateSystem.BasisZ)
    except Exception:
        direction = None

    # Round connectors expose Radius; rectangular ones Width x Height.
    size = None
    try:
        if connector.Shape == DB.ConnectorProfileType.Round:
            size = {"dia_in": round(connector.Radius * 2.0 * 12.0, 3)}
        else:
            size = {"w_in": round(connector.Width * 12.0, 3),
                    "h_in": round(connector.Height * 12.0, 3)}
    except Exception:
        pass

    row = {
        "key": _connector_key(element, connector),
        "element": eid_int(element.Id),
        "fab": is_fab,
        "domain": str(connector.Domain),
        "at": origin,
        "dir": direction,
        "size": size,
    }
    if is_fab:
        row["part"] = element.Alias or element.ProductName
        row["service"] = element.ServiceName
        row["stretchable"] = _is_stretchable(element)
    else:
        row["part"] = element.Name
    return row


def _part_kind(part):
    """///Summary: 'straight', 'hanger' or 'fitting' for a fabrication part.

    A straight is a two-connector part whose connectors face opposite ways, so
    their directions have a dot product near -1. An elbow has two connectors
    too, but at an angle. Taps are not told apart from fittings: if
    StretchAndFit rejects a start connector, this heuristic is the first thing
    to check.
    """
    try:
        if part.IsAHanger():
            return "hanger"
    except Exception:
        pass
    try:
        connectors = [c for c in part.ConnectorManager.Connectors]
        if len(connectors) == 2:
            dot = connectors[0].CoordinateSystem.BasisZ.DotProduct(
                connectors[1].CoordinateSystem.BasisZ)
            if dot < -0.999:
                return "straight"
    except Exception:
        pass
    return "fitting"


def _is_stretchable(part):
    """///Summary: True if StretchAndFit may start from this fabrication part."""
    return _part_kind(part) not in _NON_STRETCHABLE


def _elements_for(doc, uidoc, view, element_ids, selected):
    """///Summary: The elements whose connectors are offered.

    Priority: explicit ids, then the current Revit selection, then every
    fabrication part visible in the view. Selection is the practical way to
    pick two ends: the user clicks them, Claude lists them.
    """
    if element_ids:
        found = []
        for raw in element_ids:
            try:
                element = doc.GetElement(DB.ElementId(_to_int64(int(raw))))
            except (TypeError, ValueError):
                raise ValueError(
                    "element_ids must be whole numbers; got '{0}'.".format(raw))
            if element is None:
                raise ValueError("No element in this model has id {0}.".format(raw))
            found.append(element)
        return found

    if selected:
        ids = uidoc.Selection.GetElementIds()
        if ids.Count == 0:
            raise ValueError(
                "Nothing is selected in Revit. Select the fittings or equipment "
                "whose open ends you want to connect, then retry.")
        return [doc.GetElement(i) for i in ids]

    return list(DB.FilteredElementCollector(doc, view.Id).OfClass(DB.FabricationPart))


def _connectors_of(element):
    """///Summary: (connector set, is_fab) for an element, or (None, False)."""
    if isinstance(element, DB.FabricationPart):
        return element.ConnectorManager.Connectors, True
    try:
        return element.MEPModel.ConnectorManager.Connectors, False
    except Exception:
        return None, False


def list_connectors(doc, uidoc, view, element_ids=None, selected=False,
                    domain=None, include_connected=False, limit=200):
    """///Summary: Open connectors a route could start or end at.

    Read-only. `domain` is "piping" or "hvac" (default: both). Connected
    connectors are hidden unless include_connected is true. The row's
    `stretchable` flag says whether a route may *start* there; any open
    connector may be an *end*.
    """
    wanted = None
    if domain:
        wanted = DOMAINS.get(str(domain).lower())
        if wanted is None:
            raise ValueError(
                "domain must be 'piping' or 'hvac'; got '{0}'.".format(domain))

    rows = []
    truncated = False
    for element in _elements_for(doc, uidoc, view, element_ids, selected):
        connectors, is_fab = _connectors_of(element)
        if connectors is None:
            continue
        for connector in connectors:
            if connector.ConnectorType == DB.ConnectorType.Logical:
                continue
            if connector.IsConnected and not include_connected:
                continue
            if wanted is not None and connector.Domain != wanted:
                continue
            if len(rows) >= int(limit):
                truncated = True
                break
            rows.append(_connector_row(element, connector, is_fab))
        if truncated:
            break

    return {"count": len(rows), "truncated": truncated, "connectors": rows}


def _resolve_connector(doc, key):
    """///Summary: (element, connector) for a "<elementId>:<connectorId>" key."""
    try:
        element_text, connector_text = str(key).split(":")
        element_id = int(element_text)
        connector_id = int(connector_text)
    except ValueError:
        raise ValueError(
            "'{0}' is not a connector key. Keys look like '123456:2' and come "
            "from fab_list_connectors.".format(key))

    element = doc.GetElement(DB.ElementId(_to_int64(element_id)))
    if element is None:
        raise ValueError("No element in this model has id {0}.".format(element_id))

    connectors, _ = _connectors_of(element)
    if connectors is not None:
        for connector in connectors:
            if connector.Id == connector_id:
                return element, connector
    raise ValueError(
        "Element {0} has no connector {1}. Re-run fab_list_connectors; "
        "connector ids can change after an edit.".format(element_id, connector_id))


def route_between(doc, from_key, to_key, dry_run=True):
    """///Summary: Stretch a run from one open connector to another.

    Validates both ends and reports every reason it cannot proceed, then calls
    StretchAndFit inside one transaction. dry_run (the default) rolls the
    transaction back so nothing is kept, but the result and the would-be new
    part ids are still returned.
    """
    start_el, start = _resolve_connector(doc, from_key)
    end_el, end = _resolve_connector(doc, to_key)

    # Collect every blocker at once so the user fixes them in one pass.
    problems = []
    if start.IsConnected:
        problems.append("The start connector is already connected. Pick an open end.")
    if end.IsConnected:
        problems.append("The end connector is already connected. Pick an open end.")
    if not isinstance(start_el, DB.FabricationPart):
        problems.append(
            "The start must be on a fabrication part; element {0} is "
            "'{1}'.".format(eid_int(start_el.Id), start_el.Name))
    elif not _is_stretchable(start_el):
        problems.append(
            "The API cannot start a route from a straight, tap or hanger. "
            "Start from the open end of a fitting (an elbow, reducer, cap or "
            "pipe stub fitting), or place one first.")
    if start.Domain != end.Domain:
        problems.append(
            "The ends are in different domains ({0} vs {1}).".format(
                start.Domain, end.Domain))
    if problems:
        raise ValueError(" ".join(problems))

    transaction = DB.Transaction(doc, TRANSACTION_NAME)
    transaction.Start()
    try:
        target = Fabrication.FabricationPartRouteEnd.CreateFromConnector(end)
        # StretchAndFit has an out ISet<ElementId>; IronPython returns it as a
        # second tuple item when the argument is omitted.
        result, new_ids = DB.FabricationPart.StretchAndFit(doc, start, target)
    except Exception as error:
        transaction.RollBack()
        raise ValueError(
            "Revit refused the route: {0}".format(str(error).split("\n")[0]))

    outcome = str(result)
    created = sorted(eid_int(i) for i in new_ids) if new_ids is not None else []
    succeeded = result == Fabrication.FabricationPartFitResult.Success

    if not succeeded or dry_run:
        transaction.RollBack()
        kept = False
    else:
        transaction.Commit()
        kept = True
        for number in created:
            _CREATED_IDS.add(number)

    reply = {
        "result": outcome,
        "kept": kept,
        "dry_run": bool(dry_run),
        "new_part_ids": created,
    }
    if not succeeded:
        reply["hint"] = _FAILURE_HINTS.get(outcome, "")
    return reply


# What the user can do about each way StretchAndFit fails. Text is shown as-is.
_FAILURE_HINTS = {
    "MisalignedEnds": "The two ends are not on a common axis or at a right "
                      "angle. Move or rotate one end so a normal elbow run can join them.",
    "OffsetRequired": "The ends need an offset the service has no fitting for. "
                      "Line the ends up in plan or elevation first.",
    "SizeMismatch": "The two ends are different sizes. Add a reducer or "
                    "resize one end first.",
    "ShapeMismatch": "The ends have different shapes (round vs rectangular).",
    "IncompatibleConnection": "The two connector types cannot be joined "
                              "(for example grooved to welded).",
    "IncompatibleGeometry": "No fitting in the service fits this geometry.",
    "BadDimensions": "A required dimension is not valid to fabricate.",
    "DimensionLocked": "A dimension on the start part is locked or product-listed.",
    "Unsupported": "Revit does not support this combination of ends.",
}
