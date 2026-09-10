@echo off
"%~dp0..\artifacts\hermes_agent\venv\Scripts\python.exe" "%~dp0hermes_monitoring_oneshot_windows.py" %*
exit /b %ERRORLEVEL%
