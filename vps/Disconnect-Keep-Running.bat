@echo off
:: Double-click this INSTEAD of closing Remote Desktop with the X.
:: Re-launches itself as Administrator, then moves the session to the console (stays unlocked).
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Disconnect-Keep-Running.ps1"
