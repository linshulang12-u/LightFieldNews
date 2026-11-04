@echo off
rem LightFieldNews: download the latest LightFieldNews.html from the GitHub Pages site (the crawl itself runs on GitHub).
rem Windows Task Scheduler runs this every day. Needs only curl.exe, built into Windows 10/11.
setlocal
cd /d "%~dp0"
if not exist logs mkdir logs
set URL=https://linshulang12-u.github.io/LightFieldNews/LightFieldNews.html
echo ==== %date% %time% ==== >> logs\sync.log
curl.exe -fsSL --retry 3 --max-time 120 -o LightFieldNews.download "%URL%" >> logs\sync.log 2>&1
if errorlevel 1 (
  echo download failed, kept the previous LightFieldNews.html >> logs\sync.log
  if exist LightFieldNews.download del LightFieldNews.download
  exit /b 1
)
move /Y LightFieldNews.download LightFieldNews.html >nul
echo updated LightFieldNews.html >> logs\sync.log
exit /b 0
