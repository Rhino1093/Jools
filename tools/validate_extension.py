#! python3
"""Static validator for the Jools pyRevit extension.

Catches the failure modes that neither Pylance nor pylint can see, because they
are pyRevit-runtime concerns rather than Python ones: engine selection via
shebang, the CPython/IronPython split in ``pyrevit.forms``, Revit-version API
removals, and Dynamo code pasted into a pyRevit button.

Run from the repo root:

    python tools\\validate_extension.py            # human-readable report
    python tools\\validate_extension.py --quiet    # errors only
    python tools\\validate_extension.py --json     # machine-readable

Exit code is 1 if any ERROR was found, so it works as a pre-commit hook or CI gate.
Warnings alone do not fail the run.
"""

import argparse
import ast
import io
import json
import os
import re
import sys

# --- configuration ----------------------------------------------------------

# pyRevit matches this literally against line 1 (pyrevit/extensions/__init__.py:44).
CPYTHON_HASHBANG = "#! python3"

# A deliberate IronPython selection, as opposed to a botched CPython one.
IRONPYTHON_SHEBANG = re.compile(r"^\s*#\s*!\s*python\s*$")

# Shaped like a CPython shebang but not byte-identical, so pyRevit ignores it and
# silently falls back to IronPython 2.7. The costliest misconfiguration in this repo.
NEAR_MISS_SHEBANG = re.compile(r"^\s*#\s*!?\s*!?\s*python\s*3\b.*$")

SKIP_DIRS = {".venv", "typings", "__pycache__", ".git", "_deprecated", "tools"}

# Assemblies and globals that only exist inside Dynamo's Python node.
DYNAMO_MARKERS = (
    ("RevitServices", "Dynamo-only assembly"),
    ("DocumentManager", "Dynamo document accessor"),
    ("TransactionManager", "Dynamo transaction wrapper"),
    ("DSCore", "Dynamo core library"),
    ("Revit.Elements", "Dynamo element wrapper"),
    ("clr.ImportExtensions", "Dynamo import idiom"),
)

# Members removed from the Revit API. Verified against
# "C:/Program Files/Autodesk/Revit 2026/RevitAPI.xml".
REMOVED_API = {
    ".IntegerValue": (
        "ElementId.IntegerValue was removed in Revit 2026 (deprecated 2024). "
        "Use .Value, which returns Int64."
    ),
}

# Does not exist in either forms backend; the real name is ask_for_number_slider.
NONEXISTENT_CALLS = {
    "forms.ask_for_number": "No such function in any pyRevit build. Use ask_for_number_slider.",
}

ERROR, WARN = "ERROR", "WARN"


class Finding(object):
    """One problem found in one file."""

    def __init__(self, level, path, line, code, message, fix=None):
        self.level = level
        self.path = path
        self.line = line
        self.code = code
        self.message = message
        self.fix = fix

    def as_dict(self):
        return {
            "level": self.level,
            "path": self.path,
            "line": self.line,
            "code": self.code,
            "message": self.message,
            "fix": self.fix,
        }


# --- helpers ----------------------------------------------------------------


def read_text(path):
    """Read a source file tolerantly; scripts here vary in encoding and line endings."""
    with io.open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
        return handle.read()


def iter_scripts(root):
    """Yield every .py file that pyRevit could actually execute."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def line_of(text, index):
    """1-based line number for a character offset."""
    return text.count("\n", 0, index) + 1


# --- individual checks ------------------------------------------------------


def check_shebang(path, text, findings):
    """Line 1 decides the engine. Everything else in this file depends on it."""
    first = text.split("\n", 1)[0].rstrip("\r\n").rstrip()

    if first == CPYTHON_HASHBANG:
        return "cpython"

    if first.startswith(CPYTHON_HASHBANG):
        # e.g. "#! python3 -*- coding -*-" — still matches pyRevit's `in` test.
        return "cpython"

    # Ordered most-specific first: a botched python3 line is a different bug from
    # a deliberate `#! python`, and deserves a different message.
    if NEAR_MISS_SHEBANG.match(first):
        findings.append(
            Finding(
                ERROR,
                path,
                1,
                "shebang-malformed",
                "Line 1 is {!r}, which pyRevit does not recognize (it matches the "
                "literal {!r}). The script runs on IronPython 2.7 instead, so any "
                "f-string is a SyntaxError at load time.".format(first, CPYTHON_HASHBANG),
                fix="Replace line 1 with exactly: {}".format(CPYTHON_HASHBANG),
            )
        )
        return "ironpython"

    if IRONPYTHON_SHEBANG.match(first) or first.startswith("#!"):
        # No exceptions remain: every script in this extension is CPython 3.
        findings.append(
            Finding(
                ERROR,
                path,
                1,
                "shebang-ironpython",
                "Line 1 is {!r}, selecting IronPython. Every script in this "
                "extension runs on CPython 3 (CLAUDE.md section 2.1) and there are no "
                "exceptions left.".format(first),
                fix="Replace line 1 with {}, then use joolslib for alert / "
                    "ask_for_string / OutputProgress (CLAUDE.md section 6a).".format(
                        CPYTHON_HASHBANG),
            )
        )
        return "ironpython"

    findings.append(
        Finding(
            ERROR,
            path,
            1,
            "shebang-missing",
            "No shebang. pyRevit defaults to IronPython 2.7.",
            fix="Add as line 1: {}".format(CPYTHON_HASHBANG),
        )
    )
    return "ironpython"


def check_syntax(path, text, engine, findings):
    """Parse with the running CPython. Only meaningful for CPython-targeted scripts."""
    if engine != "cpython":
        return
    try:
        ast.parse(text)
    except SyntaxError as exc:
        findings.append(
            Finding(
                ERROR,
                path,
                exc.lineno or 0,
                "syntax-error",
                "SyntaxError: {}".format(exc.msg),
            )
        )


def check_forms(path, text, engine, findings):
    """Under CPython 3, every pyrevit.forms symbol raises PyRevitCPythonNotSupported."""
    if engine != "cpython":
        return

    match = re.search(r"^\s*from\s+pyrevit\s+import\s+([^\n#]+)", text, re.M)
    imports_forms = bool(match and re.search(r"\bforms\b", match.group(1)))
    if not imports_forms:
        imports_forms = bool(re.search(r"^\s*from\s+pyrevit\.forms\s+import", text, re.M))
        imports_forms = imports_forms or bool(
            re.search(r"^\s*import\s+pyrevit\.forms", text, re.M)
        )

    if not imports_forms:
        return

    findings.append(
        Finding(
            ERROR,
            path,
            line_of(text, match.start()) if match else 1,
            "forms-under-cpython",
            "Imports pyrevit.forms in a `#! python3` script. pyRevit 6.4 loads the "
            "_cpy backend, whose stubs and module __getattr__ raise "
            "PyRevitCPythonNotSupported for every symbol, forms.alert included.",
            fix="Use TaskDialog for alerts, Microsoft.Win32 dialogs for files, "
                "output.update_progress() for progress (CLAUDE.md section 3).",
        )
    )

    # Point at each call site so the rewrite is mechanical.
    for call in sorted(set(re.findall(r"\bforms\.([A-Za-z_][A-Za-z0-9_]*)", text))):
        hit = re.search(r"\bforms\.{}\b".format(call), text)
        findings.append(
            Finding(
                ERROR,
                path,
                line_of(text, hit.start()),
                "forms-call",
                "forms.{} will raise PyRevitCPythonNotSupported.".format(call),
            )
        )


def check_events_shim(path, text, engine, findings):
    """The shim must precede the first pyrevit import, which pulls revit.events in."""
    if engine != "cpython":
        return

    first_import = re.search(r"^\s*(?:from|import)\s+pyrevit\b", text, re.M)
    if not first_import:
        return

    # Either the inline sys.modules block or the joolslib helper counts.
    shim = re.search(
        r"sys\.modules\[['\"]pyrevit\.revit\.events['\"]\]"
        r"|install_events_shim\s*\(",
        text)
    if not shim:
        findings.append(
            Finding(
                WARN,
                path,
                line_of(text, first_import.start()),
                "events-shim-missing",
                "Imports pyrevit without the pyrevit.revit.events shim. Under "
                "CPython this can fail with 'interface takes exactly one argument' "
                "before any of your code runs.",
                fix="Add the shim from CLAUDE.md section 2.3 above this import.",
            )
        )
    elif shim.start() > first_import.start():
        findings.append(
            Finding(
                ERROR,
                path,
                line_of(text, shim.start()),
                "events-shim-late",
                "The events shim appears after the first pyrevit import, so it has "
                "no effect — pyrevit.script imports pyrevit.revit transitively.",
                fix="Move the shim above every `from pyrevit import ...` line.",
            )
        )


def _compat_helper_span(text):
    """Character range of an eid_int() compat helper, if the file defines one.

    A guarded `getattr(eid, "Value", None)` fallback to IntegerValue is the
    sanctioned way to cover Revit 2022-2026 at once, because Value did not
    exist before 2024 and IntegerValue was removed in 2026. The IntegerValue
    inside that helper is correct, not a defect.
    """
    start = re.search(r"^def eid_int\s*\(", text, re.M)
    if not start:
        return None
    # Scan from the start of the NEXT line. Searching from start.end() would
    # match the remainder of the def line itself ("element_id):"), collapsing
    # the span to the keyword and exempting nothing.
    body = text.index("\n", start.end()) + 1
    end = re.search(r"^\S", text[body:], re.M)
    return (start.start(), body + (end.start() if end else len(text) - body))


def check_removed_api(path, text, findings):
    """Members Autodesk deleted; these fail only at runtime, and only on new Revit."""
    exempt = _compat_helper_span(text)
    for member, why in REMOVED_API.items():
        for hit in re.finditer(re.escape(member) + r"\b", text):
            if exempt and exempt[0] <= hit.start() < exempt[1]:
                continue
            findings.append(
                Finding(ERROR, path, line_of(text, hit.start()), "removed-api", why)
            )


def check_dynamo(path, text, findings):
    """Dynamo assemblies are not loaded in a pyRevit session."""
    for marker, why in DYNAMO_MARKERS:
        hit = re.search(r"\b{}\b".format(re.escape(marker).replace(r"\.", r"\.")), text)
        if hit:
            findings.append(
                Finding(
                    ERROR,
                    path,
                    line_of(text, hit.start()),
                    "dynamo-import",
                    "{} is a {}. It does not exist under pyRevit.".format(marker, why),
                    fix="Use `doc = revit.doc` (or `__revit__.ActiveUIDocument.Document`) "
                        "and `with revit.Transaction(...)`.",
                )
            )


def check_nonexistent_calls(path, text, findings):
    """Functions the docs and LLMs both hallucinate."""
    for call, why in NONEXISTENT_CALLS.items():
        hit = re.search(re.escape(call) + r"\s*\(", text)
        if hit:
            findings.append(
                Finding(ERROR, path, line_of(text, hit.start()), "no-such-function", why)
            )


# A Python sequence assigned straight to a .NET collection property. IronPython
# converts these silently; pythonnet raises
# "'list' value cannot be converted to System.Collections.IEnumerable".
DOTNET_COLLECTION_PROPS = ("ItemsSource", "DataContext")
PY_SEQUENCE_RHS = re.compile(
    r"\.(" + "|".join(DOTNET_COLLECTION_PROPS) + r")\s*=\s*"
    r"(\[|sorted\s*\(|list\s*\(|map\s*\(|filter\s*\(|reversed\s*\()"
)


def check_dotnet_collections(path, text, engine, findings):
    """Python lists handed to WPF properties fail only under CPython."""
    if engine != "cpython":
        return
    for hit in PY_SEQUENCE_RHS.finditer(text):
        findings.append(
            Finding(
                ERROR,
                path,
                line_of(text, hit.start()),
                "python-list-to-dotnet",
                "Assigns a Python sequence to .{}. pythonnet will not convert it "
                "to System.Collections.IEnumerable, unlike IronPython.".format(
                    hit.group(1)),
                fix="Build a .NET collection first:\n"
                    "          items = List[System.Object]()\n"
                    "          for x in values: items.Add(x)\n"
                    "          widget.{} = items".format(hit.group(1)),
            )
        )


def check_lib_import(path, text, root, findings):
    """A script importing joolslib is useless if the module is not where pyRevit looks."""
    if not re.search(r"^\s*import\s+joolslib", text, re.M):
        return
    if not os.path.isfile(os.path.join(root, "lib", "joolslib.py")):
        findings.append(
            Finding(
                ERROR,
                path,
                1,
                "missing-lib",
                "Imports joolslib, but <extension>/lib/joolslib.py does not exist. "
                "pyRevit only adds <extension>/lib to sys.path.",
                fix="Restore Jools.extension/lib/joolslib.py, then Reload pyRevit.",
            )
        )


# A Python class implementing a .NET interface needs __namespace__ so pythonnet can
# emit a proxy type. IronPython never required it; CPython fails at instantiation
# with "interface takes exactly one argument".
DOTNET_INTERFACE_BASE = re.compile(
    r"^class\s+(\w+)\s*\(\s*((?:[\w.]+\.)?I[A-Z]\w*)\s*\)\s*:", re.M)


def check_dotnet_interfaces(path, text, engine, findings):
    """Implementing a .NET interface without __namespace__ fails under CPython."""
    if engine != "cpython":
        return
    for match in DOTNET_INTERFACE_BASE.finditer(text):
        cls, base = match.group(1), match.group(2)
        # Scan the class body up to the next top-level statement.
        body_start = text.index("\n", match.end()) + 1
        nxt = re.search(r"^\S", text[body_start:], re.M)
        body = text[body_start:body_start + (nxt.start() if nxt else len(text))]
        # Must be a real assignment. A bare substring match would be satisfied by
        # the explanatory comment that usually sits above it.
        ns = re.search(r"^\s+__namespace__\s*=\s*(.+)$", body, re.M)
        if ns:
            # A string literal is per-session constant, so the emitted proxy type
            # collides with "Duplicate type name within an assembly" on the second
            # run. It must be derived per execution.
            if re.match(r"""^['"]""", ns.group(1).strip()):
                findings.append(
                    Finding(
                        ERROR,
                        path,
                        line_of(text, match.start()),
                        "namespace-not-unique",
                        "class {} sets a literal __namespace__. pythonnet emits the "
                        "proxy type into an assembly that lives for the whole Revit "
                        "session, so the second run raises \"Duplicate type name "
                        "within an assembly\".".format(cls),
                        fix="Derive it per execution: "
                            "_NS = joolslib.unique_namespace(\"ToolName\") at module "
                            "level, then __namespace__ = _NS.",
                    )
                )
            continue
        findings.append(
            Finding(
                ERROR,
                path,
                line_of(text, match.start()),
                "interface-no-namespace",
                "class {} implements the .NET interface {} but declares no "
                "__namespace__. pythonnet raises \"interface takes exactly one "
                "argument\" when it is instantiated.".format(cls, base),
                fix='Add as the first line of the class body: '
                    '__namespace__ = "Jools{}"'.format(cls),
            )
        )


# Subclassing a .NET type (Form, Window, Control) requires calling the base
# constructor explicitly. IronPython did it implicitly; pythonnet does not, and the
# first property assignment then raises NullReferenceException.
DOTNET_SUBCLASS = re.compile(
    r"^class\s+(\w+)\s*\(\s*([\w.]*(?:Form|Window|UserControl|Control))\s*\)\s*:", re.M)


def check_dotnet_base_init(path, text, engine, findings):
    """A .NET subclass whose __init__ never calls the base constructor."""
    if engine != "cpython":
        return
    for match in DOTNET_SUBCLASS.finditer(text):
        cls, base = match.group(1), match.group(2)
        body_start = text.index(chr(10), match.end()) + 1
        nxt = re.search(r"^\S", text[body_start:], re.M)
        body = text[body_start:body_start + (nxt.start() if nxt else len(text))]
        if not re.search(r"^\s+def __init__", body, re.M):
            continue
        if re.search(r"^\s+(super\(|[\w.]*(?:Form|Window|Control)\.__init__)", body, re.M):
            continue
        findings.append(
            Finding(
                ERROR,
                path,
                line_of(text, match.start()),
                "dotnet-base-init",
                "class {} subclasses .NET {} and defines __init__ without calling "
                "the base constructor. pythonnet leaves the control uninitialised "
                "and the first property assignment raises "
                "NullReferenceException.".format(cls, base),
                fix="Make super().__init__() the first statement of __init__.",
            )
        )


def check_transactions(path, text, findings):
    """Model writes outside a transaction throw at runtime; flag the obvious cases."""
    writes = re.search(
        r"\b(doc\.Create\.|doc\.Delete\s*\(|\.Duplicate\s*\(|"
        r"\bSet\s*\(|doc\.NewFamilyInstance)",
        text,
    )
    has_txn = re.search(r"Transaction\s*\(|revit\.Transaction", text)
    if writes and not has_txn:
        findings.append(
            Finding(
                WARN,
                path,
                line_of(text, writes.start()),
                "possible-write-no-transaction",
                "Looks like it modifies the model but no Transaction appears in the file.",
                fix="Wrap the change: `with revit.Transaction(\"Tool Name\"):`",
            )
        )


def check_bundles(root, findings):
    """A pushbutton with no script and no bundle.yaml silently vanishes from the ribbon."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if not dirpath.endswith(".pushbutton"):
            continue

        rel = os.path.relpath(dirpath, root)
        if "bundle.yaml" not in filenames:
            findings.append(
                Finding(WARN, rel, 0, "bundle-missing", "No bundle.yaml; the button "
                        "will fall back to its folder name for the title.")
            )
        runnable = {"script.py", "script.dyn", "script.cs", "script.rb"}
        if not runnable & set(filenames):
            findings.append(
                Finding(ERROR, rel, 0, "no-script",
                        "Pushbutton folder contains no script.py or script.dyn.")
            )


# --- reporting --------------------------------------------------------------


def run(root):
    """Run every check and return the findings sorted worst-first."""
    findings = []
    for path in iter_scripts(root):
        rel = os.path.relpath(path, root)
        text = read_text(path)

        # Placeholders and disabled-hook stubs carry no executable code; checking
        # their shebang is noise, not signal.
        code_lines = [
            ln for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        if not code_lines:
            continue

        # Modules under lib/ are imported by scripts, never launched by pyRevit,
        # so they select no engine and must not carry a shebang. They still get
        # every content check below; they just inherit the caller's engine, and
        # this repo's callers are CPython 3.
        is_library = ("lib" + os.sep) in rel or rel.startswith("lib" + os.sep)

        if is_library:
            engine = "cpython"
        else:
            engine = check_shebang(rel, text, findings)
            check_events_shim(rel, text, engine, findings)

        check_syntax(rel, text, engine, findings)
        check_forms(rel, text, engine, findings)
        check_dotnet_collections(rel, text, engine, findings)
        check_lib_import(rel, text, root, findings)
        check_dotnet_interfaces(rel, text, engine, findings)
        check_dotnet_base_init(rel, text, engine, findings)
        check_removed_api(rel, text, findings)
        check_dynamo(rel, text, findings)
        check_nonexistent_calls(rel, text, findings)
        check_transactions(rel, text, findings)

    check_bundles(root, findings)
    findings.sort(key=lambda f: (f.level != ERROR, f.path, f.line))
    return findings


def report(findings, quiet):
    """Print a grouped, human-readable report."""
    shown = [f for f in findings if not (quiet and f.level == WARN)]
    if not shown:
        # Don't claim a clean bill of health when --quiet is hiding warnings.
        hidden = sum(1 for f in findings if f.level == WARN)
        if hidden:
            print("OK - no errors ({} warning(s) hidden by --quiet).".format(hidden))
        else:
            print("OK - no problems found.")
        return

    current = None
    for finding in shown:
        if finding.path != current:
            current = finding.path
            print("\n{}".format(current))
        location = ":{}".format(finding.line) if finding.line else ""
        print("  {:<5} {}{} [{}]".format(finding.level, "line" if finding.line else "",
                                         location, finding.code))
        print("        {}".format(finding.message))
        if finding.fix:
            print("        fix: {}".format(finding.fix))

    errors = sum(1 for f in findings if f.level == ERROR)
    warns = len(findings) - errors
    print("\n{} error(s), {} warning(s).".format(errors, warns))


def report_compact(findings, root):
    """One line per finding, shaped for the VS Code problem matcher."""
    for finding in findings:
        print("{}:{}:1: {} [{}] {}".format(
            os.path.join(root, finding.path),
            finding.line or 1,
            "error" if finding.level == ERROR else "warning",
            finding.code,
            finding.message,
        ))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=None, help="extension root (default: auto)")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--compact", action="store_true",
                        help="one line per finding, for editor integration")
    parser.add_argument("--quiet", action="store_true", help="suppress warnings")
    args = parser.parse_args()

    root = args.root or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Jools.extension"
    )
    if not os.path.isdir(root):
        sys.stderr.write("Extension root not found: {}\n".format(root))
        return 2

    findings = run(root)

    if args.json:
        print(json.dumps([f.as_dict() for f in findings], indent=2))
    elif args.compact:
        report_compact(findings, root)
    else:
        report(findings, args.quiet)

    return 1 if any(f.level == ERROR for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
