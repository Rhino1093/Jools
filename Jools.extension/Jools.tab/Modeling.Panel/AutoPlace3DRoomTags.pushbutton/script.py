#! python3
"""Place a 3D room tag family at every room point of a selected Revit link."""

import traceback

import clr  # type: ignore
clr.AddReference("RevitAPI")                # type: ignore
clr.AddReference("RevitAPIUI")              # type: ignore
clr.AddReference("PresentationFramework")   # type: ignore
clr.AddReference("PresentationCore")        # type: ignore
clr.AddReference("System.Xaml")             # type: ignore
clr.AddReference("WindowsBase")             # type: ignore

import System  # type: ignore
from System.Collections.Generic import List  # type: ignore
from System.Windows.Markup import XamlReader  # type: ignore

from Autodesk.Revit import DB  # type: ignore
from Autodesk.Revit.DB.Structure import StructuralType  # type: ignore
from Autodesk.Revit.UI import TaskDialog  # type: ignore

import joolslib          # Jools.extension/lib, see CLAUDE.md section 6a
joolslib.install_events_shim()   # must precede any pyrevit import

from pyrevit import revit, script

__author__ = "Ryan Johnston"

doc = revit.doc
output = script.get_output()
logger = script.get_logger()

TOOL_TITLE = "AutoPlace 3D Room Tags"

# Group naming. The delete-first option also removes group types carrying this
# prefix, so keep it distinctive enough that it can only match our own groups.
GROUP_PREFIX = "3D-Room-Tags_"

# Stand-ins for blank room values, matching the Dynamo graph this replaces.
NO_NAME = "Unnamed"
NO_NUMBER = "--"
NO_LEVEL = "zz_No Level"

DEFAULT_OFFSET_IN = 6.0

# Families Revit will place from a bare point. Anything else - curve based, view
# based (annotation), face hosted - cannot be created by NewFamilyInstance with
# only an XYZ, so it never belongs in the type list.
POINT_PLACEABLE = (
    DB.FamilyPlacementType.OneLevelBased,
    DB.FamilyPlacementType.WorkPlaneBased,
)

# Log what would happen instead of writing to the model. Flip to True when
# checking placement math against a real project.
DRY_RUN = False

# The only way to list a family's *instance* parameters is to place one and read
# it back (see writable_text_params). That is the one thing this tool does before
# the dialog appears, so it is also the first thing to rule out when the tool
# fails at launch. Flip to False and the parameter combos fall back to plain
# typed text - the tool still works, it just cannot offer you the list.
PROBE_FAMILY_PARAMS = True


class TagSetupError(Exception):
    """///Summary: The chosen family cannot accept the requested parameters.

    Raised after the first instance is placed, which is the earliest point the
    API can report what an *instance* of the family actually exposes. The caller
    rolls the whole run back and shows the message to the user.
    """


# ---------------------------------------------------------------------------
# Model reading
# ---------------------------------------------------------------------------

def _param_string(element, bip):
    """///Summary: Trimmed string value of a built-in parameter, or empty."""
    param = element.get_Parameter(bip)
    if param is None:
        return ""
    return (param.AsString() or "").strip()


class RoomInfo(object):
    """///Summary: Everything one tag needs, resolved into host coordinates."""

    def __init__(self, room, transform):
        # Room.Name returns name and number concatenated ("Office 101"), so read
        # the ROOM_NAME parameter instead - the same value Dynamo's
        # GetParameterValueByName("Name") returned.
        self.name = _param_string(room, DB.BuiltInParameter.ROOM_NAME) or NO_NAME
        self.number = _param_string(room, DB.BuiltInParameter.ROOM_NUMBER) or NO_NUMBER
        self.area = room.Area

        level = room.Level
        self.level = (level.Name if level else "") or NO_LEVEL

        # Room.Location is expressed in the LINK's coordinate system. Feeding it
        # straight to the host document - as the Dynamo graph did - only lands in
        # the right place while the link sits at origin-to-origin with no
        # rotation. GetTotalTransform() is the identity in exactly that case, so
        # applying it costs nothing there and fixes every other case.
        self.point = transform.OfPoint(room.Location.Point)


def collect_rooms(link_doc):
    """///Summary: Placed room elements in a linked document.

    Unplaced rooms have no Location at all and are dropped here. Unenclosed rooms
    (Area == 0) do have one, and whether they deserve a tag is the user's call,
    so they survive to be filtered in main().
    """
    collector = (DB.FilteredElementCollector(link_doc)
                 .OfCategory(DB.BuiltInCategory.OST_Rooms)
                 .WhereElementIsNotElementType())
    rooms = []
    for room in collector:
        location = room.Location
        if location is None or getattr(location, "Point", None) is None:
            continue
        rooms.append(room)
    return rooms


class LinkChoice(object):
    """///Summary: One loaded Revit link instance and its room inventory."""

    def __init__(self, instance, link_doc):
        self.Instance = instance
        self.LinkDoc = link_doc
        self.Transform = instance.GetTotalTransform()
        self.Rooms = collect_rooms(link_doc)
        self.Display = link_doc.Title
        self.Enclosed = sum(1 for r in self.Rooms if r.Area > 0)

    @property
    def Summary(self):
        """One-line room census for the dialog status label."""
        unenclosed = len(self.Rooms) - self.Enclosed
        text = "{} placed rooms".format(len(self.Rooms))
        if unenclosed:
            text += "   ({} not enclosed)".format(unenclosed)
        return text


def gather_links(host_doc):
    """///Summary: Every loaded Revit link instance, room count included.

    Unloaded links return None from GetLinkDocument and are skipped. Nested links
    are invisible here by design - they belong to the linked document, not this
    one - so rooms inside a link-of-a-link cannot be reached.
    """
    choices = []
    for instance in (DB.FilteredElementCollector(host_doc)
                     .OfClass(DB.RevitLinkInstance)
                     .WhereElementIsNotElementType()):
        # One unreadable link must not cost the whole list, so every link is
        # inspected on its own. Unloaded links return None here.
        try:
            link_doc = instance.GetLinkDocument()
            if link_doc is None:
                continue
            choices.append(LinkChoice(instance, link_doc))
        except Exception as ex:
            logger.debug("skipped link %s: %s",
                         joolslib.eid_int(instance.Id), ex)
            continue

    # The same link placed twice shares a document title. Disambiguate by
    # instance name so the user can tell the two apart in the combo box.
    seen = {}
    for choice in choices:
        seen[choice.Display] = seen.get(choice.Display, 0) + 1
    for choice in choices:
        if seen[choice.Display] > 1:
            choice.Display = "{}   [{}]".format(choice.Display, choice.Instance.Name)

    choices.sort(key=lambda c: c.Display.lower())
    return choices


class SymbolChoice(object):
    """///Summary: One family type the tool is able to place from a point."""

    def __init__(self, symbol):
        self.Symbol = symbol
        self.Display = "{} : {}".format(symbol.Family.Name, symbol.Name)


def gather_symbols(host_doc):
    """///Summary: Point-placeable family types loaded in the host document."""
    choices = []
    for symbol in DB.FilteredElementCollector(host_doc).OfClass(DB.FamilySymbol):
        # A real project always holds a few symbols that answer these questions
        # badly - system-family and in-place leftovers above all - and one of
        # them raising must not empty the whole list.
        try:
            family = symbol.Family
            if family is None or family.IsInPlace:
                continue
            if symbol.Category is None:
                continue
            if family.FamilyPlacementType not in POINT_PLACEABLE:
                continue
            choices.append(SymbolChoice(symbol))
        except Exception as ex:
            logger.debug("skipped family symbol %s: %s",
                         joolslib.eid_int(symbol.Id), ex)
            continue
    choices.sort(key=lambda c: c.Display.lower())
    return choices


def writable_text_params(host_doc, symbol):
    """///Summary: Instance parameter names on a family type that accept a string.

    A FamilySymbol exposes only *type* parameters, and the API offers no
    read-only way to list a loaded family's instance parameters. So place one
    throwaway instance inside a transaction that is always rolled back, read what
    it exposes, and discard it. A rollback leaves no geometry and no undo entry,
    and the answer is exactly what the real placement will see - shared and type
    parameters reachable through LookupParameter included.
    """
    if not PROBE_FAMILY_PARAMS:
        return []

    names = set()
    transaction = DB.Transaction(host_doc, "Probe tag parameters")
    transaction.Start()
    try:
        if not symbol.IsActive:
            symbol.Activate()
            host_doc.Regenerate()
        probe = host_doc.Create.NewFamilyInstance(
            DB.XYZ.Zero, symbol, StructuralType.NonStructural)
        for param in probe.Parameters:
            if param.StorageType == DB.StorageType.String and not param.IsReadOnly:
                names.add(param.Definition.Name)
    except Exception as ex:
        logger.debug("parameter probe failed for symbol %s: %s",
                     joolslib.eid_int(symbol.Id), ex)
    finally:
        if transaction.GetStatus() == DB.TransactionStatus.Started:
            transaction.RollBack()
    return sorted(names)


# ---------------------------------------------------------------------------
# Options dialog
# ---------------------------------------------------------------------------

XAML = """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="AutoPlace 3D Room Tags" Width="520" SizeToContent="Height"
        ResizeMode="NoResize" WindowStartupLocation="CenterScreen"
        Topmost="True" Background="#F0F0F0">
    <StackPanel Margin="15">
        <TextBlock Text="1. Architectural link to read rooms from"
                   FontWeight="Bold" Margin="0,0,0,4"/>
        <ComboBox x:Name="LinkCombo" DisplayMemberPath="Display" Height="26"/>
        <TextBlock x:Name="StatusText" Margin="2,4,0,12" Foreground="#555555"/>

        <TextBlock Text="2. 3D room tag family type"
                   FontWeight="Bold" Margin="0,0,0,4"/>
        <ComboBox x:Name="TypeCombo" DisplayMemberPath="Display" Height="26"
                  Margin="0,0,0,12"/>

        <TextBlock Text="3. Tag parameters to write the room values into"
                   FontWeight="Bold" Margin="0,0,0,4"/>
        <Grid Margin="0,0,0,12">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="140"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>
            <Grid.RowDefinitions>
                <RowDefinition Height="Auto"/>
                <RowDefinition Height="Auto"/>
            </Grid.RowDefinitions>
            <TextBlock Grid.Row="0" Grid.Column="0" Text="Room Name into"
                       VerticalAlignment="Center" Margin="0,0,0,4"/>
            <ComboBox Grid.Row="0" Grid.Column="1" x:Name="NameParamCombo"
                      IsEditable="True" Height="26" Margin="0,0,0,4"/>
            <TextBlock Grid.Row="1" Grid.Column="0" Text="Room Number into"
                       VerticalAlignment="Center"/>
            <ComboBox Grid.Row="1" Grid.Column="1" x:Name="NumberParamCombo"
                      IsEditable="True" Height="26"/>
        </Grid>

        <TextBlock Text="4. Options" FontWeight="Bold" Margin="0,0,0,6"/>
        <StackPanel Orientation="Horizontal" Margin="0,0,0,8">
            <TextBlock Text="Height above room point (inches)"
                       VerticalAlignment="Center" Margin="0,0,8,0"/>
            <TextBox x:Name="OffsetBox" Width="70" Height="24"/>
        </StackPanel>
        <CheckBox x:Name="SkipUnenclosedChk" Margin="0,0,0,6"
                  Content="Skip rooms that are not enclosed (area = 0)"/>
        <CheckBox x:Name="GroupChk" Margin="0,0,0,6"
                  Content="Group the new tags by level"/>
        <CheckBox x:Name="DeleteChk" Margin="0,0,0,14"
                  Content="Delete existing tags of this type first, and any 3D-Room-Tags_ groups"/>

        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="BtnPlace" Content="Place Tags" Width="110" Height="30"
                    Margin="0,0,10,0" IsDefault="True"/>
            <Button x:Name="BtnCancel" Content="Cancel" Width="80" Height="30"
                    IsCancel="True"/>
        </StackPanel>
    </StackPanel>
</Window>
"""


def _guard(handler):
    """///Summary: Stop a Python exception from crossing back into WPF.

    A handler that raises inside a .NET event callback does not surface as a
    Python traceback: pythonnet has nowhere to put the error on the way out, and
    Revit reports a bare "Object reference not set to an instance of an object"
    naming nothing. Log the real stack and swallow it, so a bad handler degrades
    the dialog instead of killing the command.
    """
    def wrapped(sender, args):
        try:
            handler(sender, args)
        except Exception:
            logger.error("%s: handler %s failed\n%s", TOOL_TITLE,
                         getattr(handler, "__name__", handler),
                         traceback.format_exc())
    return wrapped


def _net_list(values):
    """///Summary: A Python sequence as a .NET list, fit for an ItemsSource.

    pythonnet will not convert a Python list to IEnumerable the way IronPython
    did; assigning one to ItemsSource raises at runtime (CLAUDE.md section 3).
    """
    items = List[System.Object]()
    for value in values:
        items.Add(value)
    return items


class TagOptions(object):
    """///Summary: The validated answers from the dialog."""

    def __init__(self):
        self.link = None
        self.symbol = None
        self.symbol_display = ""
        self.name_param = ""
        self.number_param = ""
        self.offset = 0.0          # internal units (feet)
        self.offset_inches = 0.0   # what the user typed, kept for the report
        self.skip_unenclosed = True
        self.group_by_level = True
        self.delete_existing = False


class TagOptionsWindow(object):
    """///Summary: The one dialog this tool shows.

    Wraps a Window rather than subclassing it, so pythonnet never has to build a
    proxy type for a .NET base class.
    """

    def __init__(self, links, symbols, prefs):
        self.window = XamlReader.Parse(XAML)
        self.links = links
        self.symbols = symbols
        self.options = TagOptions()
        self.accepted = False

        # Probing a family costs a rolled-back transaction, so remember the
        # answer per family type instead of repeating it on every combo change.
        self._param_cache = {}
        self._pref_name = prefs.get("name_param", "Name")
        self._pref_number = prefs.get("number_param", "Number")

        self.link_combo = self.window.FindName("LinkCombo")
        self.type_combo = self.window.FindName("TypeCombo")
        self.name_combo = self.window.FindName("NameParamCombo")
        self.number_combo = self.window.FindName("NumberParamCombo")
        self.offset_box = self.window.FindName("OffsetBox")
        self.status_text = self.window.FindName("StatusText")
        self.skip_chk = self.window.FindName("SkipUnenclosedChk")
        self.group_chk = self.window.FindName("GroupChk")
        self.delete_chk = self.window.FindName("DeleteChk")

        # Guarded copies are kept on the instance so the subscription holds a
        # live reference to them for as long as the window does.
        self._link_handler = _guard(self.on_link_changed)
        self._type_handler = _guard(self.on_type_changed)
        self._place_handler = _guard(self.on_place_click)
        self._cancel_handler = _guard(self.on_cancel_click)

        self.link_combo.ItemsSource = _net_list(links)
        self.link_combo.SelectionChanged += self._link_handler
        self.link_combo.SelectedIndex = _index_of(
            links, prefs.get("link_display", ""))

        self.type_combo.ItemsSource = _net_list(symbols)
        self.type_combo.SelectionChanged += self._type_handler
        self.type_combo.SelectedIndex = _preferred_symbol_index(
            symbols, prefs.get("symbol_display", ""))

        self.offset_box.Text = "{:g}".format(
            _as_float(prefs.get("offset_inches", DEFAULT_OFFSET_IN),
                      DEFAULT_OFFSET_IN))
        self.skip_chk.IsChecked = bool(prefs.get("skip_unenclosed", True))
        self.group_chk.IsChecked = bool(prefs.get("group_by_level", True))
        self.delete_chk.IsChecked = False

        self.window.FindName("BtnPlace").Click += self._place_handler
        self.window.FindName("BtnCancel").Click += self._cancel_handler

        # Both handlers normally run off the SelectedIndex assignments above.
        # Calling them once more guarantees the status label and the parameter
        # combos are populated no matter how WPF chose to raise those events.
        self._link_handler(None, None)
        self._type_handler(None, None)

    # -- events ------------------------------------------------------------

    def on_link_changed(self, sender, args):
        """Refresh the room census under the link combo."""
        choice = self.link_combo.SelectedItem
        self.status_text.Text = choice.Summary if choice else ""

    def on_type_changed(self, sender, args):
        """Repopulate the parameter combos for the selected family type."""
        choice = self.type_combo.SelectedItem
        if choice is None:
            return

        key = joolslib.eid_int(choice.Symbol.Id)
        if key not in self._param_cache:
            self._param_cache[key] = writable_text_params(doc, choice.Symbol)
        names = self._param_cache[key]

        self._fill_param_combo(self.name_combo, names, self._pref_name, "Name")
        self._fill_param_combo(self.number_combo, names, self._pref_number, "Number")

    def on_place_click(self, sender, args):
        """Validate every field before letting the dialog close."""
        link = self.link_combo.SelectedItem
        symbol_choice = self.type_combo.SelectedItem
        if link is None or symbol_choice is None:
            TaskDialog.Show(TOOL_TITLE, "Pick both a link and a tag family type.")
            return

        name_param = (self.name_combo.Text or "").strip()
        number_param = (self.number_combo.Text or "").strip()
        if not name_param or not number_param:
            TaskDialog.Show(
                TOOL_TITLE,
                "Name both tag parameters.\n\nIf the lists are empty, the family "
                "has no writable text parameters. Add them in the family editor, "
                "reload the family, then run this again.")
            return

        offset_inches = _as_float((self.offset_box.Text or "").strip(), None)
        if offset_inches is None:
            TaskDialog.Show(
                TOOL_TITLE,
                "Height above room point must be a number of inches, such as 6 "
                "or 42.\n\nCorrect it and click Place Tags again.")
            return

        self.options.link = link
        self.options.symbol = symbol_choice.Symbol
        self.options.symbol_display = symbol_choice.Display
        self.options.name_param = name_param
        self.options.number_param = number_param
        self.options.offset_inches = offset_inches
        self.options.offset = offset_inches / 12.0
        self.options.skip_unenclosed = bool(self.skip_chk.IsChecked)
        self.options.group_by_level = bool(self.group_chk.IsChecked)
        self.options.delete_existing = bool(self.delete_chk.IsChecked)

        self.accepted = True
        self.window.Close()

    def on_cancel_click(self, sender, args):
        self.window.Close()

    # -- helpers -----------------------------------------------------------

    def _fill_param_combo(self, combo, names, preferred, fallback):
        """Set a parameter combo list, keeping the user's last choice if valid.

        The combo is editable so a failed probe still leaves a way in: the list
        may be empty, but a typed name is read back from Text.
        """
        current = (combo.Text or "").strip()
        combo.ItemsSource = _net_list(names)
        for candidate in (current, preferred, fallback):
            if candidate and candidate in names:
                combo.SelectedItem = candidate
                # Text is what on_place_click reads back. Selecting an item
                # normally fills it, but setting it outright costs nothing and
                # means a marshalling miss cannot leave the box blank.
                combo.Text = candidate
                return
        combo.SelectedIndex = -1
        combo.Text = current or preferred or fallback

    def show(self):
        """Returns True if the user chose to place tags."""
        self.window.ShowDialog()
        return self.accepted


def _as_float(value, default):
    """///Summary: float(value), or `default` when it will not convert."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _index_of(choices, wanted):
    """///Summary: Index of the choice whose Display matches, else 0."""
    for index, choice in enumerate(choices):
        if choice.Display == wanted:
            return index
    return 0


def _preferred_symbol_index(symbols, remembered):
    """///Summary: Last-used type, else the first that looks like a room tag."""
    for index, choice in enumerate(symbols):
        if choice.Display == remembered:
            return index
    for index, choice in enumerate(symbols):
        if "room tag" in choice.Display.lower():
            return index
    return 0


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

PREF_DEFAULTS = {
    "link_display": "",
    "symbol_display": "",
    "name_param": "Name",
    "number_param": "Number",
    "offset_inches": DEFAULT_OFFSET_IN,
    "skip_unenclosed": True,
    "group_by_level": True,
}


def load_prefs():
    """///Summary: Last run's answers, or the defaults. Never fatal."""
    try:
        config = script.get_config()
        return dict((key, config.get_option(key, value))
                    for key, value in PREF_DEFAULTS.items())
    except Exception as ex:
        logger.debug("could not read saved options: %s", ex)
        return dict(PREF_DEFAULTS)


def save_prefs(options):
    """///Summary: Remember the answers so the next run starts where this ended."""
    try:
        config = script.get_config()
        config.link_display = options.link.Display
        config.symbol_display = options.symbol_display
        config.name_param = options.name_param
        config.number_param = options.number_param
        config.offset_inches = options.offset_inches
        config.skip_unenclosed = options.skip_unenclosed
        config.group_by_level = options.group_by_level
        script.save_config()
    except Exception as ex:
        logger.debug("could not save options: %s", ex)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _set_text_param(instance, param_name, value):
    """///Summary: Write a string parameter by name. False if not writable."""
    param = instance.LookupParameter(param_name)
    if param is None or param.IsReadOnly:
        return False
    if param.StorageType != DB.StorageType.String:
        return False
    param.Set(value)
    return True


def _instance_text_params(instance):
    """///Summary: Writable string parameter names on a placed instance."""
    return sorted(set(
        p.Definition.Name for p in instance.Parameters
        if p.StorageType == DB.StorageType.String and not p.IsReadOnly))


def delete_previous_tags(host_doc, symbol):
    """///Summary: Remove the last run's output so a re-run starts clean.

    Group types go first: deleting a GroupType removes its instances and
    everything inside them, which is the only way to reach tags that are already
    grouped. Whatever is left loose and of the chosen type follows. Tags the user
    put in some other group are left alone and reported.
    """
    ids = List[DB.ElementId]()
    groups_removed = 0
    for group_type in DB.FilteredElementCollector(host_doc).OfClass(DB.GroupType):
        if group_type.Name.startswith(GROUP_PREFIX):
            ids.Add(group_type.Id)
            groups_removed += 1

    loose = 0
    foreign_group = 0
    # A quick filter on the symbol id beats walking every FamilyInstance in the
    # project, which on a large model is most of the elements in it.
    same_type = DB.FamilyInstanceFilter(host_doc, symbol.Id)
    for instance in (DB.FilteredElementCollector(host_doc)
                     .WhereElementIsNotElementType()
                     .WherePasses(same_type)):
        # -1 is InvalidElementId: the instance belongs to no group.
        if joolslib.eid_int(instance.GroupId) != -1:
            foreign_group += 1
            continue
        ids.Add(instance.Id)
        loose += 1

    if ids.Count:
        host_doc.Delete(ids)

    logger.debug("deleted %s group types and %s loose instances, skipped %s in "
                 "other groups", groups_removed, loose, foreign_group)
    return {"groups": groups_removed, "loose": loose, "foreign": foreign_group}


def place_tags(host_doc, options, rooms):
    """///Summary: Create and populate one tag per room. Returns ids by level.

    Every placement lands in one transaction, which is where this beats the
    Dynamo graph on speed - Dynamo evaluated node by node, item by item.
    """
    symbol = options.symbol
    if not symbol.IsActive:
        symbol.Activate()
        host_doc.Regenerate()

    by_level = {}
    lift = DB.XYZ(0, 0, options.offset)
    checked = False

    with joolslib.OutputProgress(output, TOOL_TITLE, len(rooms)) as progress:
        for index, info in enumerate(rooms):
            if progress.cancelled:
                raise TagSetupError(
                    "Cancelled after {} of {} tags, so nothing was placed."
                    .format(index, len(rooms)))

            instance = host_doc.Create.NewFamilyInstance(
                info.point + lift, symbol, StructuralType.NonStructural)

            # An instance is the first thing that can answer "does this family
            # really have these parameters?", so validate on the first one and
            # let the caller roll the whole run back if not.
            if not checked:
                checked = True
                missing = [n for n in (options.name_param, options.number_param)
                           if instance.LookupParameter(n) is None]
                if missing:
                    raise TagSetupError(
                        "{} has no parameter named {}.\n\nWritable text "
                        "parameters on this family: {}\n\nPick one of those, or "
                        "add the parameter in the family editor and reload the "
                        "family.".format(
                            options.symbol_display,
                            " or ".join(repr(name) for name in missing),
                            ", ".join(_instance_text_params(instance)) or "none"))

            _set_text_param(instance, options.name_param, info.name)
            _set_text_param(instance, options.number_param, info.number)

            by_level.setdefault(info.level, []).append(instance.Id)

            # Repainting the output window per element costs more than the
            # placement itself on a large model.
            if index % 25 == 0:
                progress.update_progress(index + 1)

        progress.update_progress(len(rooms))

    return by_level


def group_tags_by_level(host_doc, by_level):
    """///Summary: One model group per level, named 3D-Room-Tags_<level>.

    GroupType names must be unique in a document, so an existing name gets a
    numeric suffix instead of throwing the way the Dynamo graph did on a re-run.
    """
    taken = set(gt.Name for gt in
                DB.FilteredElementCollector(host_doc).OfClass(DB.GroupType))
    results = []

    for level in sorted(by_level):
        ids = List[DB.ElementId]()
        for element_id in by_level[level]:
            ids.Add(element_id)

        group = host_doc.Create.NewGroup(ids)
        name = _unique_name(GROUP_PREFIX + level, taken)
        taken.add(name)
        try:
            group.GroupType.Name = name
        except Exception as ex:
            # A group that could not be renamed is still a valid group. Say so
            # rather than losing the whole run over a name.
            logger.debug("could not rename group for %s: %s", level, ex)
            name = "{}   (rename failed)".format(group.GroupType.Name)
        results.append((level, ids.Count, name))

    return results


def _unique_name(base, taken):
    """///Summary: `base`, or base_2, base_3 ... until it is unused."""
    if base not in taken:
        return base
    suffix = 2
    while "{}_{}".format(base, suffix) in taken:
        suffix += 1
    return "{}_{}".format(base, suffix)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if doc is None:
        TaskDialog.Show(TOOL_TITLE, "Open a project first, then run this again.")
        return

    links = gather_links(doc)
    if not links:
        TaskDialog.Show(
            TOOL_TITLE,
            "This project has no loaded Revit links.\n\nLink the architectural "
            "model - or reload it if it shows as unloaded - then run this again.")
        return

    symbols = gather_symbols(doc)
    if not symbols:
        TaskDialog.Show(
            TOOL_TITLE,
            "No point-placeable family types are loaded.\n\nLoad your 3D room "
            "tag family, then run this again. It has to be a level-based or "
            "work-plane-based family, not an annotation family.")
        return

    dialog = TagOptionsWindow(links, symbols, load_prefs())
    if not dialog.show():
        return
    options = dialog.options

    candidates = options.link.Rooms
    if options.skip_unenclosed:
        skipped = [r for r in candidates if r.Area <= 0]
        candidates = [r for r in candidates if r.Area > 0]
    else:
        skipped = []

    if not candidates:
        TaskDialog.Show(
            TOOL_TITLE,
            "{} has no rooms to tag.\n\nEither the model has no placed rooms, or "
            "every room is unenclosed and the skip option is on."
            .format(options.link.Display))
        return

    rooms = [RoomInfo(room, options.link.Transform) for room in candidates]

    if DRY_RUN:
        logger.info("DRY_RUN: would place %s tags of %s",
                    len(rooms), options.symbol_display)
        for info in rooms:
            logger.info("  %s / %s at %.2f, %.2f, %.2f on %s", info.number,
                        info.name, info.point.X, info.point.Y,
                        info.point.Z + options.offset, info.level)
        return

    save_prefs(options)

    # Placement and grouping are separate transactions because NewGroup needs its
    # members to exist as committed elements. The TransactionGroup keeps both in
    # a single undo step.
    txn_group = DB.TransactionGroup(doc, TOOL_TITLE)
    txn_group.Start()
    placement = DB.Transaction(doc, "Place 3D room tags")
    grouping = None
    by_level = {}
    groups = []
    removed = None
    try:
        placement.Start()
        removed = (delete_previous_tags(doc, options.symbol)
                   if options.delete_existing else None)
        by_level = place_tags(doc, options, rooms)
        doc.Regenerate()
        placement.Commit()

        if options.group_by_level:
            grouping = DB.Transaction(doc, "Group 3D room tags by level")
            grouping.Start()
            groups = group_tags_by_level(doc, by_level)
            grouping.Commit()

        txn_group.Assimilate()
    except Exception as ex:
        for transaction in (placement, grouping):
            if transaction and transaction.GetStatus() == DB.TransactionStatus.Started:
                transaction.RollBack()
        if txn_group.GetStatus() == DB.TransactionStatus.Started:
            txn_group.RollBack()
        logger.error("placement failed: %s", ex)
        TaskDialog.Show(TOOL_TITLE, "Nothing was changed.\n\n{}".format(ex))
        return

    _report(options, by_level, groups, skipped, removed)


def _report(options, by_level, groups, skipped, removed):
    """///Summary: What happened, in the pyRevit output window."""
    total = sum(len(ids) for ids in by_level.values())
    output.print_md("## {}".format(TOOL_TITLE))
    output.print_md("- Source link: **{}**".format(options.link.Display))
    output.print_md("- Tag type: **{}**".format(options.symbol_display))
    output.print_md("- Placed **{}** tags, {:g} in. above each room point."
                    .format(total, options.offset_inches))

    if removed:
        output.print_md("- Cleared first: {} group type(s), {} loose tag(s)."
                        .format(removed["groups"], removed["loose"]))
        if removed["foreign"]:
            output.print_md(
                "- Left alone: {} existing tag(s) sitting inside groups this "
                "tool did not create.".format(removed["foreign"]))

    if skipped:
        output.print_md("- Skipped **{}** unenclosed room(s), area = 0."
                        .format(len(skipped)))

    if groups:
        output.print_table(
            table_data=[[level, count, name] for level, count, name in groups],
            columns=["Level", "Tags", "Group"])
    else:
        output.print_table(
            table_data=[[level, len(ids)]
                        for level, ids in sorted(by_level.items())],
            columns=["Level", "Tags"])


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Without this, a .NET exception on its way back out of the engine
        # reaches the user as a bare "Object reference not set to an instance of
        # an object" dialog that names no file and no line. Print the real stack
        # to the output window first, then let pyRevit report it as usual.
        output.print_md("### {} failed".format(TOOL_TITLE))
        output.print_code(traceback.format_exc())
        logger.error("%s failed\n%s", TOOL_TITLE, traceback.format_exc())
        raise
