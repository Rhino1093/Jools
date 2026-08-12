# Jools pyRevit Extension

A collection of "quick-and-dirty" automation tools for Revit, designed to fill gaps in standard Revit functionality with a focus on speed and reliability.

## Installation

For a guided setup, run the **`Setup-Instructions.bat`** file included in this folder.

### Quick Start:
1. Ensure [pyRevit](https://github.com/pyrevitlabs/pyRevit/releases) is installed.
2. Place this `Jools.extension` folder in a permanent location (e.g., `C:\pyRevitExtensions\Jools.extension`).
3. In Revit, go to **pyRevit > Settings > Extensions**.
4. Add the **parent folder** of this extension to the **Search Paths** (e.g., `C:\pyRevitExtensions`).
5. Click **Save Settings and Reload**.

## Features

The tools are organized into four consolidated panels:

*   **Project:** About information and useful project links.
*   **Management:** BIM Management tools (Bulk Export, Links, Health Checks) and View Template management.
*   **Modeling:** View creation tools and geometry modification utilities (Array on Path, Equal Distance, Lights to Ceiling).
*   **Documentation:** Schedule management and Fill Pattern utilities.

## Technical Details

*   **Host:** Revit (Multiple versions supported).
*   **Runtime:** CPython 3 preferred (`#! python3`).
*   **UI:** Robust WPF (XAML-based) implementations for complex dialogs.