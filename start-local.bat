@echo off
REM PanKeeper (pankeeper-git repo) local dev launcher - single-port mode:
REM backend :8000 serves the built frontend from pankeeper-backend/web/
REM (no vite dev server, avoids the known vite proxy ECONNRESET jitter).
REM Python venv is borrowed from the old mainline repo checkout.
set BE=D:\zcodeWork\pankeeper\pankeeper-git\pankeeper-backend
set PY=D:\zcodeWork\pankeeper\pankeeper\pankeeper-backend\.venv\Scripts\python.exe

start "pankeeper-backend" /min cmd /c "cd /d %BE% && %PY% run.py --port 8000 > %BE%\dev-backend.log 2>&1"

echo.
echo   backend + web : http://127.0.0.1:8000
echo   login         : admin / admin#123  (fresh dev DB)
echo   log           : %BE%\dev-backend.log
echo   stop          : stop-dev.bat (kills by port 8000)
echo.
echo   A minimized window was opened. Close it to stop the backend.
ping -n 4 127.0.0.1 >nul
