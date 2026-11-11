@echo off
setlocal
cd /d "%~dp0"

where node >nul 2>nul
if errorlevel 1 (
  echo Node.js is not installed or is not on PATH.
  echo Install Node.js 22 or later, then reopen this window.
  pause
  exit /b 1
)

for /f "tokens=1 delims=v." %%A in ('node --version') do set NODE_MAJOR=%%A
if not defined NODE_MAJOR (
  echo Could not detect the installed Node.js version.
  pause
  exit /b 1
)
if %NODE_MAJOR% LSS 22 (
  echo INTRADAY AI requires Node.js 22 or later. Detected:
  node --version
  pause
  exit /b 1
)

echo Starting INTRADAY AI local research dashboard...
echo Keep this window open while using the app.
node server.mjs
if errorlevel 1 (
  echo.
  echo INTRADAY AI stopped with an error. Check the message above.
  pause
)
endlocal
