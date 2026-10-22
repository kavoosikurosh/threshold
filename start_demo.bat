@echo off
rem [phone] One-click demo: starts the Threshold server and a free Cloudflare
rem quick tunnel, so any phone can open the app over https (no Wi-Fi setup).
rem Close both windows to stop. The https link changes every time.
setlocal
cd /d "%~dp0"

rem --- find Python: venv inside the repo (teammates), or Kurosh's threshold_env next to it
set "PY="
if exist "venv\Scripts\python.exe" set "PY=%~dp0venv\Scripts\python.exe"
if not defined PY if exist "..\threshold_env\Scripts\python.exe" set "PY=%~dp0..\threshold_env\Scripts\python.exe"
if not defined PY (
  echo Could not find Python. Make the venv first, see README.md.
  pause
  exit /b 1
)

where cloudflared >nul 2>nul
if errorlevel 1 (
  echo cloudflared is not installed. Run:  winget install --id Cloudflare.cloudflared -e
  echo Then close and reopen this window.
  pause
  exit /b 1
)

rem [final] An old server still running on port 8000 would keep serving OLD code
rem (the new one can't start). Stop here and say so.
netstat -ano | findstr /r /c:":8000 .*LISTENING" >nul
if not errorlevel 1 (
  echo.
  echo Port 8000 is already in use, probably by an old Threshold server window.
  echo Close that window ^(or restart the PC^), then run start_demo.bat again.
  echo To see what is using it:  netstat -ano ^| findstr :8000
  pause
  exit /b 1
)

echo Starting the Threshold server (models take about 10 seconds to load)...
start "Threshold server" /d "%~dp0backend" "%PY%" -m uvicorn main:app --host 0.0.0.0 --port 8000

rem give the server a moment before the tunnel connects to it
timeout /t 5 /nobreak >nul

echo.
echo ============================================================
echo  Same Wi-Fi as this PC (photo mode only; live Scan needs https):
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do for /f "tokens=*" %%b in ("%%a") do echo     http://%%b:8000
echo ============================================================

rem [voice] the tunnel writes its log to a file so we can find the new https link
set "TUNNEL_LOG=%TEMP%\threshold_tunnel.log"
if exist "%TUNNEL_LOG%" del "%TUNNEL_LOG%"
start "Threshold tunnel" cloudflared tunnel --url http://localhost:8000 --logfile "%TUNNEL_LOG%"

rem [voice] wait for the link, print the phone/Siri link, and copy it to the clipboard
echo.
echo Waiting for the https link...
powershell -NoProfile -Command "$f = $env:TUNNEL_LOG; for ($i = 0; $i -lt 60; $i++) { if (Test-Path $f) { $m = Select-String -Path $f -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1; if ($m) { $u = $m.Matches[0].Value + '/?mode=scan'; Set-Clipboard $u; Write-Host ''; Write-Host ' ANY PHONE, ANYWHERE (and the link for the Siri Shortcut):' -ForegroundColor Green; Write-Host ('    ' + $u) -ForegroundColor Green; Write-Host ''; Write-Host ' Copied to the clipboard. Text or email it to the phone.'; Write-Host ' This link changes every time you run start_demo.bat (see SIRI_SHORTCUT.md).'; exit } }; Start-Sleep 1 }; Write-Host ' Could not find the link yet. Look in the Threshold tunnel window for https://....trycloudflare.com'"
echo.
echo Both windows are running. Close them to stop the demo.
pause
