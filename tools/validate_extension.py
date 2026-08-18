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
        findings.append(
            Finding(
                ERROR,
                path,
                1,
                "shebang-ironpython",
                "Line 1 is {!r}, selecting IronPython. This repo standardized on "
                "CPython 3 (CLAUDE.md \u00a72.1); pyrevit.forms calls must be "
                "replaced when converting.".format(first),
                fix="Replace line 1 with exactly: {}, then port forms.* per "
                    "CLAUDE.md \u00a73.".format(CPYTHON_HASHBANG),
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
                "output.update_progress() for progress (CLAUDE.md \u00a73).",
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

    shim = re.search(r"sys\.modules\[['\"]pyrevit\.revit\.events['\"]\]", text)
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
                fix="Add the shim from CLAUDE.md \u00a72.3 above this import.",
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


def check_removed_api(path, text, findings):
    """Members Autodesk deleted; these fail only at runtime, and only on new Revit."""
    for member, why in REMOVED_API.items():
        for hit in re.finditer(re.escape(member) + r"\b", text):
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

        engine = check_shebang(rel, text, findings)
        check_syntax(rel, text, engine, findings)
        check_forms(rel, text, engine, findings)
        check_events_shim(rel, text, engine, findings)
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
