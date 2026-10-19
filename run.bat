@echo off
rem Runs Pomo from source without a console window.
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
    py -3 -m venv .venv
    .venv\Scripts\python.exe -m pip install -r requirements.txt
)
start "" .venv\Scripts\pythonw.exe main.py %*
