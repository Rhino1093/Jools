@echo off
setlocal
title Jools Extension Setup Guide
color 0B

echo ======================================================================
echo                JOOLS PYREVIT EXTENSION - INSTALLATION
echo ======================================================================
echo.
echo This script will guide you through setting up the Jools extension.
echo.
echo ----------------------------------------------------------------------
echo STEP 1: INSTALL PYREVIT
echo ----------------------------------------------------------------------
echo Ensure you have pyRevit installed. If not, download the latest 
echo "pyRevit_Setup_x.x.x.exe" from:
echo.
echo https://github.com/pyrevitlabs/pyRevit/releases
echo.
set /p "choice=Would you like to open the pyRevit download page now? (y/n): "
if /i "%choice%"=="y" start https://github.com/pyrevitlabs/pyRevit/releases
echo.
echo ----------------------------------------------------------------------
echo STEP 2: POSITION THE EXTENSION
echo ----------------------------------------------------------------------
echo 1. Move this entire folder (the one containing this script) to 
echo    a permanent location on your drive.
echo.
echo 2. IMPORTANT: The folder MUST be named exactly "Jools.extension" 
echo    for pyRevit to recognize it. If you are updating, overwrite 
echo    the old folder.
echo.
echo    Recommended Path: C:\pyRevitExtensions\Jools.extension
echo.
echo ----------------------------------------------------------------------
echo STEP 3: LINK TO PYREVIT
echo ----------------------------------------------------------------------
echo 1. Open Autodesk Revit.
echo 2. Locate the 'pyRevit' tab on the ribbon.
echo 3. Click the 'pyRevit' dropdown (far left) and select 'Settings'.
echo 4. In the Settings window, click on 'Extensions' in the left menu.
echo 5. In the 'Search Paths' section, click the [+] button.
echo 6. Browse and select the PARENT folder of Jools.extension.
echo    (Example: If using the recommended path, select "C:\pyRevitExtensions")
echo 7. Click 'Save Settings and Reload'.
echo.
echo ----------------------------------------------------------------------
echo STEP 4: REFRESH JOOLS TAB
echo ----------------------------------------------------------------------
echo The 'Jools' tab should now appear in your Revit ribbon.
echo If it doesn't appear immediately, please restart Revit.
echo.
echo ======================================================================
echo Setup guide complete. Press any key to close this window.
echo ======================================================================
pause > nul
