# Superseded — see [../CLAUDE.md](../CLAUDE.md)

This file described an **IronPython 2.7 + `pyrevit.forms`** workflow. That is no
longer how this repo works, and following it now produces tools that cannot run:

- `pyrevit.forms` raises `PyRevitCPythonNotSupported` for every symbol under
  `#! python3` on pyRevit 6.4 — see `pyrevitlib/pyrevit/forms/_cpy.py`.
- `forms.ask_for_number`, recommended here, does not exist in any pyRevit build.
- Omitting a shebang selects IronPython 2.7, where f-strings are a `SyntaxError`.

All current rules live in `CLAUDE.md` at the repo root. Read that instead.
