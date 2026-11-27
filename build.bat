@echo off
rem Builds the Pomo installer:
rem   dist\Pomo\                  the app (PyInstaller one-folder build)
rem   dist\Pomo-portable.zip      zipped copy for people who prefer no installer
rem   dist\Pomo-Setup.exe         the installer (needs Inno Setup 6)
rem File names carry no version so the README's "latest release" download links stay valid.
setlocal
cd /d "%~dp0"

if defined CI (
    set "PY=python"
) else (
    if not exist .venv\Scripts\python.exe (
        echo Creating virtual environment...
        py -3 -m venv .venv || goto :error
    )
    set "PY=.venv\Scripts\python.exe"
)

echo [1/6] Installing dependencies...
%PY% -m pip install --quiet --disable-pip-version-check -r requirements.txt pyinstaller || goto :error

echo [2/6] Running tests...
%PY% -m unittest discover -s tests -t . || goto :error

echo [3/6] Preparing icon, wizard images and version info...
set "APP_VERSION="
for /f "delims=" %%v in ('%PY% tools\prepare_build.py') do set "APP_VERSION=%%v"
if "%APP_VERSION%"=="" goto :error

rem Intel's PresentMon counts a game's frames for gaming mode: fetched once and checked against its hash
%PY% tools\fetch_presentmon.py || goto :error

rem --noupx: UPX-packed files lose the signatures Qt, Python and Microsoft put on theirs
echo [4/6] Building Pomo %APP_VERSION%...
%PY% -m PyInstaller --noconfirm --clean --onedir --windowed --noupx --name Pomo ^
    --icon assets\pomo.ico --version-file build\version_info.txt ^
    --add-data "pomodoro\sounds;pomodoro\sounds" --add-data "pomodoro\bin;pomodoro\bin" main.py || goto :error

rem Smart App Control blocks unsigned binaries it doesn't know, so sign every one that isn't signed yet
rem (see tools\sign.py), then have Inno Setup sign the installer and its uninstaller with the same tool.
echo [5/6] Signing...
set "SIGN_ARGS="
%PY% tools\sign.py --configured
if errorlevel 2 goto :signed
if errorlevel 1 goto :error
%PY% tools\sign.py dist\Pomo || goto :error
for /f "delims=" %%p in ('%PY% -c "import sys; print(sys.executable)"') do set "PY_EXE=%%p"
set SIGN_ARGS="/Spomosign=$q%PY_EXE%$q $q%~dp0tools\sign.py$q $f" /DSignBuild
:signed
%PY% -c "import shutil, sys; shutil.make_archive(sys.argv[1], 'zip', 'dist', 'Pomo')" "dist\Pomo-portable" || goto :error

echo [6/6] Building installer...
set "ISCC="
for /f "delims=" %%i in ('where iscc 2^>nul') do if not defined ISCC set "ISCC=%%i"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    echo.
    echo Inno Setup 6 was not found, so only the portable build was made.
    echo Install it with:  winget install JRSoftware.InnoSetup
    echo then run build.bat again.
    exit /b 1
)
set "URL_ARG="
if defined GITHUB_REPOSITORY set "URL_ARG=/DAppURL=https://github.com/%GITHUB_REPOSITORY%"
"%ISCC%" /Q /DAppVersion=%APP_VERSION% %URL_ARG% %SIGN_ARGS% installer\Pomo.iss || goto :error

echo.
echo Done (version %APP_VERSION%):
echo   dist\Pomo-Setup.exe
echo   dist\Pomo-portable.zip
exit /b 0

:error
echo.
echo Build failed.
exit /b 1
