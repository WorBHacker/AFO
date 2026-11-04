@echo off
rem AFO: the chat window without a console. It starts the hub itself when the hub is not running.
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -m app
