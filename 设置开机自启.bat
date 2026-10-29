@echo off
rem Enable countdown auto-start (writes startup vbs).
setlocal
set "PY="
for /f "delims=" %%i in ('where pythonw 2^>nul') do if not defined PY set "PY=%%i"
if not defined PY for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY set "PY=%%i"
if not defined PY (
  echo Python was not found. Please install Python 3 first.
  pause
  exit /b 1
)
"%PY%" "%~dp0paydance.pyw" --install-autostart
echo Auto-start has been enabled.
pause
exit /b 0
