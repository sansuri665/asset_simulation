@echo off
setlocal

set "OPEN_PATH=%~1"
if "%OPEN_PATH%"=="" set "OPEN_PATH=/?seed=42&years=60"
set "BASE_URL=http://127.0.0.1:8783"
set "URL=%BASE_URL%%OPEN_PATH%"

echo Checking Asset Simulation UI...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='SilentlyContinue'; try { $health=Invoke-RestMethod -Uri '%BASE_URL%/api/health' -TimeoutSec 2; if ($health.serviceId -eq 'asset-simulation-macro-ui-v5.41') { exit 0 } } catch {}; $conn=Get-NetTCPConnection -LocalPort 8783 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if ($conn) { Write-Host ('Port 8783 is already used by PID ' + $conn.OwningProcess + '.'); exit 2 }; exit 1"
set "CHECK_RESULT=%ERRORLEVEL%"

if "%CHECK_RESULT%"=="0" (
  echo Asset Simulation UI is already running.
  start "" "%URL%"
  exit /b 0
)

if "%CHECK_RESULT%"=="2" (
  echo Please close the program using port 8783 or change the Asset Simulation port.
  pause
  exit /b 2
)

echo Starting Asset Simulation UI...
echo Open "%URL%" if the browser does not open automatically.
echo Press Ctrl+C in this window to stop the service.
cd /d "%~dp0..\.."
py -3 -m asset_simulation --host 127.0.0.1 --port 8783 --open --open-path "%OPEN_PATH%"
set "SERVER_RESULT=%ERRORLEVEL%"
if not "%SERVER_RESULT%"=="0" echo Asset Simulation UI exited with code %SERVER_RESULT%.
pause
exit /b %SERVER_RESULT%
