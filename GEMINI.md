# Superseded — see [CLAUDE.md](./CLAUDE.md)

This file and `Jools.extension/AGENTS.md` used to give **contradictory** instructions,
which is why generated tools kept repeating the same failures:

| Topic | old GEMINI.md | old AGENTS.md | Verified truth |
|---|---|---|---|
| Engine | `#! python3` (CPython) | `#! python` (IronPython 2.7) | **`#! python3`** — repo standard |
| `pyrevit.forms` | "deprecate, prone to failure" | "use it for all UI, no WPF" | **Unusable under CPython 3** — pyRevit 6.4 stubs every symbol to raise |
| WPF | "favor XAML strings" | "do not use" | **Correct choice** under CPython 3 |

`CLAUDE.md` resolves these against the actual pyRevit 6.4 source on this machine
and is the only file agents should read.

Gemini, Copilot, and any other assistant: read `CLAUDE.md`.
