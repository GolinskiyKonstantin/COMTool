@echo off
setlocal EnableExtensions
cd /d "%~dp0"
call "%~dp0_setup_env.bat"
if errorlevel 1 goto fail
call :shortcut
rem Isolate from other Qt installs on PATH and point Qt to its own plugins
set "PATH=%~dp0.venv\Scripts;%SystemRoot%\System32;%SystemRoot%"
set "QT_PLUGIN_PATH=%~dp0.venv\Lib\site-packages\PyQt5\Qt5\plugins"
set "QT_QPA_PLATFORM_PLUGIN_PATH=%~dp0.venv\Lib\site-packages\PyQt5\Qt5\plugins\platforms"
start "" "%~dp0.venv\Scripts\pythonw.exe" -m COMTool.Main
exit /b 0

:fail
echo.
pause
exit /b 1

:shortcut
rem Creates the "COMTool-BLE" desktop shortcut once (again if the folder was moved)
set "OLD="
if exist "%~dp0.venv\shortcut.path" set /p OLD=<"%~dp0.venv\shortcut.path"
if /i "%OLD%"=="%~dp0" exit /b 0
set "APPDIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'COMTool-BLE.lnk')); $s.TargetPath=Join-Path $env:APPDIR 'COMTool.bat'; $s.WorkingDirectory=$env:APPDIR; $s.IconLocation=(Join-Path $env:APPDIR 'COMTool\assets\logo_ble.ico'); $s.WindowStyle=7; $s.Description='COMTool-BLE'; $s.Save()"
if errorlevel 1 exit /b 0
>"%~dp0.venv\shortcut.path" echo %~dp0
echo Desktop shortcut "COMTool-BLE" created.
exit /b 0
