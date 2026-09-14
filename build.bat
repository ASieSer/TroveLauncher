@echo off
REM Builds TroveAccountsHub.exe. Double-click it, or run it from a terminal.
REM
REM It installs what is missing (PyInstaller, and the app's own dependencies,
REM which have to be importable for PyInstaller to trace them) and leaves the
REM finished executable in dist\.
setlocal

REM Work from the repository root whatever directory this was launched from.
cd /d "%~dp0"

echo.
echo  Trove Accounts Hub - build
echo  ============================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo  Python was not found on PATH.
    echo  Install Python 3.10 or newer from https://python.org and tick
    echo  "Add python.exe to PATH" while doing it.
    goto :fail
)

for /f "delims=" %%v in ('python -c "import sys;print(sys.version.split()[0])"') do set PYVER=%%v
echo  Python %PYVER%

python -c "import sys;raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo  Python 3.10 or newer is required.
    goto :fail
)

REM The spec puts this icon inside the executable. It is versioned, so it should
REM be here; tools\make_icon.py regenerates it if it ever goes missing.
if not exist "web\img\app.ico" (
    echo  web\img\app.ico is missing. Regenerating it...
    python -m pip install --quiet pillow || goto :fail
    python tools\make_icon.py || goto :fail
)

python -c "import webview, requests" >nul 2>&1
if errorlevel 1 (
    echo  Installing the application dependencies...
    python -m pip install --quiet -r requirements.txt
    if errorlevel 1 goto :fail
)

python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo  Installing PyInstaller...
    python -m pip install --quiet pyinstaller
    if errorlevel 1 goto :fail
)

echo  Packaging. This takes a minute...
echo.
python -m PyInstaller TroveAccountsHub.spec --noconfirm --log-level WARN
if errorlevel 1 goto :fail

if not exist "dist\TroveAccountsHub.exe" (
    echo  PyInstaller finished but dist\TroveAccountsHub.exe is not there.
    goto :fail
)

echo.
echo  ============================================
for %%f in ("dist\TroveAccountsHub.exe") do echo  Done: %%~ff  ^(%%~zf bytes^)
echo.
echo  That one file is the whole application. It needs no Python and no
echo  dependencies on the machine it runs on.
echo.
pause
exit /b 0

:fail
echo.
echo  ============================================
echo  Build failed. The error is above.
echo.
pause
exit /b 1
