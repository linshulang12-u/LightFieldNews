@echo off
rem LightFieldNews diagnostics: writes logs\check.txt for Claude to read.
setlocal
cd /d "%~dp0"
if not exist logs mkdir logs
set OUT=logs\check.txt
echo ==== %date% %time% ==== > %OUT%
echo [where python] >> %OUT%
where python >> %OUT% 2>&1
echo [where py] >> %OUT%
where py >> %OUT% 2>&1
echo [python --version] >> %OUT%
python --version >> %OUT% 2>&1
echo errorlevel=%errorlevel% >> %OUT%
echo [py -0p] >> %OUT%
py -0p >> %OUT% 2>&1
echo errorlevel=%errorlevel% >> %OUT%
echo [user Python folders] >> %OUT%
dir /b "%LOCALAPPDATA%\Programs\Python" >> %OUT% 2>&1
dir /b "C:\Program Files\Python*" >> %OUT% 2>&1
dir /b "%USERPROFILE%\anaconda3\python.exe" "%USERPROFILE%\miniconda3\python.exe" "C:\ProgramData\anaconda3\python.exe" "C:\ProgramData\miniconda3\python.exe" >> %OUT% 2>&1
echo [winget] >> %OUT%
winget --version >> %OUT% 2>&1
echo [network] >> %OUT%
curl -s -o NUL -w "openalex %%{http_code}\n" --max-time 20 "https://api.openalex.org/works?per-page=1" >> %OUT% 2>&1
curl -s -o NUL -w "crossref %%{http_code}\n" --max-time 20 "https://api.crossref.org/works?rows=1" >> %OUT% 2>&1
curl -s -o NUL -w "arxiv %%{http_code}\n" --max-time 20 "https://export.arxiv.org/api/query?search_query=all:laser&max_results=1" >> %OUT% 2>&1
curl -s -o NUL -w "europepmc %%{http_code}\n" --max-time 20 "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=laser&format=json&pageSize=1" >> %OUT% 2>&1
curl -s -o NUL -w "pypi %%{http_code}\n" --max-time 20 "https://pypi.org/simple/requests/" >> %OUT% 2>&1
echo [PATH] >> %OUT%
echo %PATH% >> %OUT%
echo ==== done ==== >> %OUT%
echo Check finished. You can close this window.
timeout /t 5 >nul
