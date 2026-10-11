@echo off
:: Opens the Voice-over Studio on the latest demo recording.
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" tools\voiceover_studio.py %*
