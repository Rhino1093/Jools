# jools-revit MCP server

Lets Claude dimension Revit ceiling plans. Each MCP tool is one HTTP call to the
Jools pyRevit Routes API (`Jools.extension/startup.py`); the Revit work happens in
`Jools.extension/lib/rcpdims.py`.

## Setup (once per machine)

1. In `%APPDATA%\pyRevit\pyRevit_config.ini`, the `[routes]` section needs:
   ```ini
   enabled = true
   host = "127.0.0.1"
   ```
   Without `host`, Routes listens on every network interface.
2. Create the venv and install the SDK:
   ```powershell
   python -m venv mcp_server\.venv
   mcp_server\.venv\Scripts\python.exe -m pip install -r mcp_server\requirements.txt
   ```
3. `.mcp.json` at the repo root registers the server for Claude Code in this repo.
   Claude Code asks you to approve it the first time.

## Use

1. Open Revit with a project, open the ceiling plan.
2. After editing `startup.py` or anything in `lib/`, **save and restart Revit**.
   Do not use pyRevit Reload while Routes is on: on pyRevit 6.5.4 it crashes or
   hangs Revit (see CLAUDE.md section 7). Fixed in pyRevit 7.0.0, except that
   route changes still need a restart there.
3. Ask Claude to dimension the view.

The listing honours the view: view range, filters, hidden elements and worksets
(Revit 2023.1+). A fixture outside the view range is not offered.

## Several Revit sessions

pyRevit gives each Revit session its own port: the first takes 48884, the next
48885, and so on. The server talks to 48884 unless `JOOLS_ROUTES_URL` is set, e.g.
`http://127.0.0.1:48885/jools`, in the `env` block of `.mcp.json`.

## Tools

| Tool | Writes? | What it does |
|---|---|---|
| `revit_ping` | no | open document and active view |
| `rcp_audit` | no | which fixture families can be dimensioned to centre |
| `rcp_list_items` | no | fixtures + grids in the view, host and linked, with keys |
| `rcp_dimension` | yes | one dimension string; `dry_run` previews and rolls back |
| `rcp_delete_dimensions` | deletes | only dimensions it created since Revit started |
| `fab_info` | no | fabrication config, loaded services, parts placed per service |
| `fab_list_connectors` | no | open connectors on fab parts / selection, with keys |
| `fab_route` | yes | StretchAndFit between two connectors; `dry_run` defaults to true |
