@echo off
rem LightFieldNews one-time setup. Every step is also written to logs\setup.log.
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
set LOG=logs\setup.log
echo ==== setup %date% %time% ==== > %LOG%

echo Opening the site (LightFieldNews.html) ...
start "" "%~dp0LightFieldNews.html"

echo.
echo [1/5] Looking for Python 3 ...
call :findpy
if defined PY goto :havepy
echo Python 3 not found >> %LOG%
echo Python 3 is not installed on this computer. It is needed for the daily update.
choice /C YN /M "Install Python 3.12 now with winget (from Microsoft's package source)"
if errorlevel 2 goto :nopython
echo Installing Python 3.12 ... >> %LOG%
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements >> %LOG% 2>&1
call :findpy
if not defined PY goto :nopython

:havepy
echo Using %PY% >> %LOG%
%PY% --version >> %LOG% 2>&1
%PY% --version

echo.
echo [2/5] Installing packages (requests, PyYAML, beautifulsoup4, Pillow) ...
%PY% -m pip install --user --disable-pip-version-check -r scraper\requirements.txt >> %LOG% 2>&1
if errorlevel 1 goto :fail

echo.
echo [3/5] Registering the Windows task "LightFieldNews daily update" (every day 06:33) ...
schtasks /Create /F /SC DAILY /ST 06:33 /TN "LightFieldNews daily update" /TR "\"%~dp0run_daily.bat\"" >> %LOG% 2>&1
if errorlevel 1 goto :fail

echo.
echo [4/5] Fetching new papers. The first run can take 10-30 minutes; progress goes to logs\setup.log ...
%PY% scraper\news_scraper.py --daily >> %LOG% 2>&1
if errorlevel 1 goto :fail

echo.
echo [5/5] Rebuilding LightFieldNews.html ...
%PY% scraper\build_standalone.py >> %LOG% 2>&1
if errorlevel 1 goto :fail
echo ==== setup finished %date% %time% ==== >> %LOG%
start "" "%~dp0LightFieldNews.html"
echo.
echo All done. The site refreshes every day at 06:33; open LightFieldNews.html to read it.
pause
exit /b 0

:nopython
echo setup stopped: no Python >> %LOG%
echo.
echo Python is needed for the daily update. Install it from https://www.python.org/downloads/
echo (tick "Add python.exe to PATH"), then double-click setup_windows.bat again.
echo The site itself already works: double-click LightFieldNews.html.
pause
exit /b 1

:fail
echo setup FAILED at the step above >> %LOG%
echo.
echo A step failed. The details are in logs\setup.log - tell Claude and it can read the log.
pause
exit /b 1

:findpy
set "PY="
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PY="%%D\python.exe""
if defined PY exit /b 0
py -3 --version >nul 2>nul
if not errorlevel 1 (
  set "PY=py -3"
  exit /b 0
)
python --version >nul 2>nul
if not errorlevel 1 (
  set "PY=python"
  exit /b 0
)
exit /b 1
