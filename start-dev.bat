@echo off
REM PanKeeper local dev launcher: backend :8000 + frontend :5173
REM Double-click this (or run it from your own terminal). The services run in
REM their own detached windows, independent of WorkBuddy / any agent session.
set BE=D:\zcodeWork\pankeeper\pankeeper\pankeeper-backend
set FE=D:\zcodeWork\pankeeper\pankeeper\pankeeper-vue3
set LOG=D:\zcodeWork\pankeeper\pankeeper

start "pankeeper-backend" /min cmd /c "cd /d %BE% && .venv\Scripts\python.exe run.py --port 8000 > %LOG%\backend-dev.log 2>&1"
start "pankeeper-frontend" /min cmd /c "cd /d %FE% && node node_modules\vite\bin\vite.js > %LOG%\frontend-dev.log 2>&1"

echo.
echo   backend  : http://127.0.0.1:8000
echo   frontend : http://localhost:5173
echo   logs     : %LOG%\backend-dev.log  /  frontend-dev.log
echo.
echo   Two minimized windows were opened. Close them to stop the services,
echo   or run stop-dev.bat to kill both by port.
timeout /t 4 >nul
