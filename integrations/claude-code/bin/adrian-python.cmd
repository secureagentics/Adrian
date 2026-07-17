@echo off
rem Adrian launcher (Windows). Sibling of bin/adrian-python (POSIX sh); Claude
rem Code resolves the extensionless "adrian-python" in the hook command to this
rem .cmd via PATHEXT under both cmd.exe and PowerShell.
setlocal
rem Plugin root = parent of this bin\ directory (%~dp0 ends with a backslash).
set "PLUGIN_DIR=%~dp0.."

rem adrian_cc lives under the plugin dir; protobuf + websockets are vendored
rem under vendor\ (pure-Python) so no pip install is needed. Put both on
rem PYTHONPATH, vendor first, and force pure-Python protobuf (no compiled _upb).
set "PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python"
if defined PYTHONPATH (
  set "PYTHONPATH=%PLUGIN_DIR%\vendor;%PLUGIN_DIR%;%PYTHONPATH%"
) else (
  set "PYTHONPATH=%PLUGIN_DIR%\vendor;%PLUGIN_DIR%"
)

rem Prefer a plugin-local venv, then the py launcher, then python.
if exist "%PLUGIN_DIR%\.venv\Scripts\python.exe" (
  "%PLUGIN_DIR%\.venv\Scripts\python.exe" %*
  exit /b %errorlevel%
)
where py >nul 2>&1
if %errorlevel%==0 (
  py -3 %*
  exit /b %errorlevel%
)
where python >nul 2>&1
if %errorlevel%==0 (
  python %*
  exit /b %errorlevel%
)
echo adrian-cc: no Python found (need the py launcher or python on PATH) 1>&2
exit /b 1
