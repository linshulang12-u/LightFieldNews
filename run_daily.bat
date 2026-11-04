@echo off
rem LightFieldNews daily update (Windows Task Scheduler runs this every day): crawl, then rebuild LightFieldNews.html.
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
call :findpy
if not defined PY (
  echo %date% %time% Python 3 not found >> logs\daily.log
  exit /b 1
)
echo ==== %date% %time% ==== >> logs\daily.log
%PY% scraper\news_scraper.py --daily >> logs\daily.log 2>&1
%PY% scraper\build_standalone.py >> logs\daily.log 2>&1
exit /b %errorlevel%

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
