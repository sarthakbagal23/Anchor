@echo off
rem Anchor dev server with watchdog: refuses a second instance on
rem port 8765, restarts the server if it exits unexpectedly, and timestamps
rem every (re)start into server-out.log so a silent death is visible.
cd /d "C:\Users\sarth\Desktop\opennotebook"
set LOG=C:\Users\sarth\Desktop\opennotebook\server-out.log
set ERRLOG=C:\Users\sarth\Desktop\opennotebook\server-err.log
echo [%date% %time%] run-server: watchdog starting >> "%LOG%"

:checkport
powershell -NoProfile -Command "$c=New-Object Net.Sockets.TcpClient; try { $c.Connect('127.0.0.1',8765); $c.Close(); exit 0 } catch { exit 1 }" >nul 2>nul
if %errorlevel%==0 (
  echo [%date% %time%] run-server: port 8765 already in use - not starting a second server. Close the other server window first (or kill a stray hidden python.exe in Task Manager) and re-run. >> "%LOG%"
  echo Port 8765 is already in use - another server is running.
  echo Close the other server window first, then re-run this file.
  echo If you see no server window, kill the stray python.exe in Task Manager.
  pause
  exit /b 1
)

:loop
echo [%date% %time%] run-server: launching server >> "%LOG%"
".venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8765 >> "%LOG%" 2>> "%ERRLOG%"
echo [%date% %time%] run-server: server exited with code %errorlevel% - restarting in 3s (Ctrl+C here to stop) >> "%LOG%"
timeout /t 3 /nobreak >nul
goto loop
