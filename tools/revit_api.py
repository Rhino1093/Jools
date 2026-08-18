#! python3
"""Look up Revit API members in Autodesk's shipped XML documentation.

The point of this tool is to stop guessing. Revit's API changes every release and
the local Pylance stubs only go up to RVT 25, so they happily autocomplete members
that Autodesk deleted. ``RevitAPI.xml`` ships with each Revit install and is the
authority: if a member is not in it, it does not exist in that version.

    python tools\\revit_api.py ElementId                    # the type + its members
    python tools\\revit_api.py ElementId.Value              # one member, full docs
    python tools\\revit_api.py "FilteredElementCollector.OfCategory"
    python tools\\revit_api.py --search "reference intersector"
    python tools\\revit_api.py --exists ElementId.IntegerValue
    python tools\\revit_api.py --diff ElementId 2024 2026   # what changed between versions

``--exists`` exits 0 if found, 1 if not, so it can gate a script.
"""

import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET

REVIT_ROOT = r"C:\Program Files\Autodesk"
ASSEMBLIES = ("RevitAPI", "RevitAPIUI")

# Prefixes Autodesk uses on member names in the XML.
KIND = {
    "T": "type",
    "M": "method",
    "P": "property",
    "F": "field",
    "E": "event",
}


def installed_versions():
    """Return every Revit version present on this machine, newest first."""
    if not os.path.isdir(REVIT_ROOT):
        return []
    found = []
    for name in os.listdir(REVIT_ROOT):
        match = re.match(r"^Revit (\d{4})$", name)
        if match and os.path.isdir(os.path.join(REVIT_ROOT, name)):
            found.append(match.group(1))
    return sorted(found, reverse=True)


def doc_paths(version):
    """XML documentation files for one Revit version."""
    base = os.path.join(REVIT_ROOT, "Revit {}".format(version))
    return [
        os.path.join(base, "{}.xml".format(asm))
        for asm in ASSEMBLIES
        if os.path.isfile(os.path.join(base, "{}.xml".format(asm)))
    ]


def load_members(version):
    """Map 'K:Full.Dotted.Name' -> the <member> element, across both assemblies."""
    members = {}
    for path in doc_paths(version):
        for _, elem in ET.iterparse(path):
            if elem.tag == "member" and elem.get("name"):
                members[elem.get("name")] = elem
    return members


def clean(text):
    """Collapse Autodesk's ragged whitespace into one readable line."""
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def signature(name):
    """Turn 'M:A.B.C(X,Y)' into 'C(X, Y)' for display."""
    body = name.split(":", 1)[1]
    args = ""
    if "(" in body:
        body, args = body.split("(", 1)
        args = "(" + ", ".join(a.split(".")[-1] for a in args.rstrip(")").split(","))+ ")"
    short = body.split(".")[-1]
    return short + args


def _rank(name):
    """Types first, then the DB/UI namespaces, then shortest — most-wanted first.

    Searching "ElementId" must surface the ElementId *type*, not the dozens of
    unrelated properties that happen to be named ElementId.
    """
    body = name.split(":", 1)[1]
    is_type = 0 if name.startswith("T:") else 1
    core = 0 if body.startswith(("Autodesk.Revit.DB.", "Autodesk.Revit.UI.")) else 1
    return (is_type, core, body.count("."), body)


def matches(members, query):
    """Find members whose dotted name ends with the query, case-insensitively."""
    want = query.lower().lstrip(".")
    exact, partial = [], []
    for name in members:
        body = name.split(":", 1)[1]
        stem = body.split("(", 1)[0].lower()
        if stem == want or stem.endswith("." + want):
            exact.append(name)
        elif want in stem:
            partial.append(name)
    return sorted(exact, key=_rank) or sorted(partial, key=_rank)


def describe(name, elem, verbose=True):
    """Render one member the way you'd want to read it before writing a call."""
    lines = []
    kind = KIND.get(name.split(":", 1)[0], "?")
    lines.append("  {:<9} {}".format(kind, signature(name)))
    lines.append("            {}".format(name.split(":", 1)[1]))

    summary = clean(elem.findtext("summary"))
    if summary:
        lines.append("            {}".format(summary))

    if not verbose:
        return lines

    since = clean(elem.findtext("since"))
    if since:
        lines.append("            since: Revit {}".format(since))

    for param in elem.findall("param"):
        lines.append("            param {}: {}".format(param.get("name"), clean(param.text)))

    returns = clean(elem.findtext("returns"))
    if returns:
        lines.append("            returns: {}".format(returns))

    for exc in elem.findall("exception"):
        ref = (exc.get("cref") or "").split(".")[-1]
        lines.append("            raises {}: {}".format(ref, clean(exc.text)))

    return lines


def cmd_lookup(members, query, verbose):
    """Print everything matching a member or type name."""
    hits = matches(members, query)
    if not hits:
        print("Not found: {}".format(query))
        return 1

    # A type match answers the question on its own; don't bury it under the
    # unrelated properties that share its name.
    if hits[0].startswith("T:"):
        hits = [h for h in hits if h.startswith("T:")][:3]
    else:
        hits = hits[:10]

    for name in hits:
        if name.startswith("T:"):
            print("\n{}".format(name.split(":", 1)[1]))
            for line in describe(name, members[name], verbose):
                print(line)
            prefix = name.split(":", 1)[1] + "."
            children = sorted(
                n for n in members
                if not n.startswith("T:")
                and n.split(":", 1)[1].startswith(prefix)
                and "." not in n.split(":", 1)[1][len(prefix):].split("(")[0]
            )
            if children:
                print("\n  members ({}):".format(len(children)))
                for child in children:
                    print("    {:<10} {}".format(
                        KIND.get(child.split(":", 1)[0], "?"), signature(child)))
        else:
            print("")
            for line in describe(name, members[name], verbose):
                print(line)

    return 0


def cmd_search(members, text):
    """Full-text search across summaries — for when you don't know the name."""
    needle = text.lower()
    hits = []
    for name, elem in members.items():
        summary = clean(elem.findtext("summary")).lower()
        if needle in summary or needle in name.lower():
            hits.append((name, elem))
    if not hits:
        print("No matches for {!r}".format(text))
        return 1
    for name, elem in sorted(hits)[:40]:
        print("")
        for line in describe(name, elem, verbose=False):
            print(line)
    if len(hits) > 40:
        print("\n... {} more matches; narrow the search.".format(len(hits) - 40))
    return 0


def cmd_diff(query, old, new):
    """Show what a type gained or lost between two Revit versions."""
    def surface(version):
        members = load_members(version)
        prefix = None
        for name in members:
            if name.startswith("T:") and name.split(":", 1)[1].lower().endswith(
                    "." + query.lower()):
                prefix = name.split(":", 1)[1] + "."
                break
        if prefix is None:
            return None
        return {signature(n) for n in members
                if not n.startswith("T:") and n.split(":", 1)[1].startswith(prefix)}

    before, after = surface(old), surface(new)
    if before is None or after is None:
        print("Type {!r} not found in Revit {}".format(
            query, old if before is None else new))
        return 1

    removed = sorted(before - after)
    added = sorted(after - before)
    print("{}: Revit {} -> {}".format(query, old, new))
    for item in removed:
        print("  REMOVED  {}".format(item))
    for item in added:
        print("  added    {}".format(item))
    if not removed and not added:
        print("  no change")
    return 0


def main():
    versions = installed_versions()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query", nargs="?", help="Type, Type.Member, or search text")
    parser.add_argument("--version", default=versions[0] if versions else None,
                        help="Revit version (default: newest installed)")
    parser.add_argument("--search", metavar="TEXT", help="full-text search of summaries")
    parser.add_argument("--exists", metavar="MEMBER",
                        help="exit 0 if the member exists, 1 if not")
    parser.add_argument("--diff", nargs=3, metavar=("TYPE", "OLD", "NEW"),
                        help="compare a type's members between two Revit versions")
    parser.add_argument("--brief", action="store_true", help="summaries only")
    parser.add_argument("--list-versions", action="store_true")
    args = parser.parse_args()

    if args.list_versions:
        for version in versions:
            flag = "" if doc_paths(version) else "   (no API xml)"
            print("Revit {}{}".format(version, flag))
        return 0

    if args.diff:
        return cmd_diff(args.diff[0], args.diff[1], args.diff[2])

    if not versions:
        sys.stderr.write("No Revit installation found under {}\n".format(REVIT_ROOT))
        return 2
    if not doc_paths(args.version):
        sys.stderr.write("No API xml for Revit {}\n".format(args.version))
        return 2

    members = load_members(args.version)

    if args.exists:
        found = bool(matches(members, args.exists))
        print("{}: {} in Revit {}".format(
            args.exists, "EXISTS" if found else "NOT FOUND", args.version))
        return 0 if found else 1

    if args.search:
        return cmd_search(members, args.search)

    if not args.query:
        parser.print_help()
        return 2

    return cmd_lookup(members, args.query, not args.brief)


if __name__ == "__main__":
    sys.exit(main())
