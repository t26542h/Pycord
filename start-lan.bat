@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
)

".venv\Scripts\python.exe" -m pip install -r requirements.txt

set PYCORD_HTTP_HOST=0.0.0.0
set PYCORD_WS_HOST=0.0.0.0
set PYCORD_HTTP_PORT=8000
set PYCORD_WS_PORT=8765

echo.
echo Starting PyCord Lite for your local network...
echo If Windows Firewall asks, allow Python on private networks.
echo.
".venv\Scripts\python.exe" app.py

pause
