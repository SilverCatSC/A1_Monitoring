@echo off
"%~dp0..\artifacts\hermes_agent\venv\Scripts\python.exe" "%~dp0hermes_maintenance_windows.py" %*
exit /b %ERRORLEVEL%
