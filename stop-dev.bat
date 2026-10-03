@echo off
REM Stop PanKeeper dev services by port (8000 backend / 5173 frontend)
echo Stopping PanKeeper dev services...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000" ^| findstr LISTENING') do taskkill /PID %%p /T /F >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5173" ^| findstr LISTENING') do taskkill /PID %%p /T /F >nul 2>&1
echo Done (processes that were running were killed).
timeout /t 3 >nul
