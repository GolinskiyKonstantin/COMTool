@echo off
rem Creates .venv and installs dependencies. Skips everything if already done.
rem NOTE: no variables are set and used inside (...) blocks on purpose (cmd expands them too early).
setlocal EnableExtensions
cd /d "%~dp0"
set "VPY=%~dp0.venv\Scripts\python.exe"
set "REQ=%~dp0.venv\requirements.installed"

if not exist "%VPY%" goto mkvenv
if not exist "%REQ%" goto install
fc /b requirements.txt "%REQ%" >nul 2>&1
if not errorlevel 1 exit /b 0
goto install

:mkvenv
call :findpython
if defined PY goto havepy
call :installpython
call :findpython
:havepy
if defined PY goto createvenv
echo.
echo Python 3.8+ was not found and could not be installed automatically.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH", then run this file again.
exit /b 1

:createvenv
echo Creating virtual environment with: %PY%
if exist "%~dp0.venv" rmdir /s /q "%~dp0.venv"
%PY% -m venv "%~dp0.venv"
if errorlevel 1 goto venvfail
if not exist "%VPY%" goto venvfail
goto install

:venvfail
echo.
echo Failed to create virtual environment.
exit /b 1

:install
echo Installing required libraries, please wait...
"%VPY%" -m pip install --disable-pip-version-check --upgrade pip setuptools wheel
"%VPY%" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto installfail
copy /y requirements.txt "%REQ%" >nul
echo Done.
exit /b 0

:installfail
echo.
echo Library installation failed. Check the internet connection and run this file again.
exit /b 1

:findpython
set "PY="
for %%V in (3.12 3.13 3.11 3.10 3.9 3.8) do if not defined PY py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
if defined PY exit /b 0
python -c "import sys; sys.exit(0 if sys.version_info>=(3,8) else 1)" >nul 2>&1 && set "PY=python"
if defined PY exit /b 0
for %%P in ("%LocalAppData%\Programs\Python\Python312\python.exe" "%LocalAppData%\Programs\Python\Python313\python.exe" "%LocalAppData%\Programs\Python\Python311\python.exe") do if not defined PY if exist %%P set PY="%%~P"
exit /b 0

:installpython
echo Python was not found, installing Python 3.12 for the current user...
where winget >nul 2>&1
if errorlevel 1 goto downloadpython
winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
exit /b 0

:downloadpython
set "PYINST=%TEMP%\python-3.12.8-amd64.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe -OutFile $env:PYINST"
if not exist "%PYINST%" exit /b 0
"%PYINST%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=0 Include_test=0
exit /b 0
