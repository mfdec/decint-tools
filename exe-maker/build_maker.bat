@echo off
rem Build DECINT EXE Maker into dist\DECINT-EXE-Maker.exe (double-click or run from cmd).
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% -c "import sys; assert sys.version_info >= (3, 9), 'Python 3.9+ required'" || (
  echo Python 3.9 or newer is required. Install it from https://www.python.org/downloads/ ^(tick "Add to PATH"^).
  pause & exit /b 1
)
%PY% -m pip install --disable-pip-version-check -q pyinstaller pillow
%PY% build_maker.py --cli
if errorlevel 1 (echo. & echo BUILD FAILED & pause & exit /b 1)
echo.
echo Done: dist\DECINT-EXE-Maker.exe   (GUI)
echo       dist\decint-exe-maker-cli.exe   (command line)
pause
