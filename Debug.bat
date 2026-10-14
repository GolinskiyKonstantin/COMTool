@echo off
setlocal EnableExtensions
cd /d "%~dp0"
call "%~dp0_setup_env.bat"
set "PATH=%~dp0.venv\Scripts;%SystemRoot%\System32;%SystemRoot%"
set "QT_PLUGIN_PATH=%~dp0.venv\Lib\site-packages\PyQt5\Qt5\plugins"
set "QT_QPA_PLATFORM_PLUGIN_PATH=%~dp0.venv\Lib\site-packages\PyQt5\Qt5\plugins\platforms"
set QT_DEBUG_PLUGINS=1
echo ===== diagnostics =====
"%~dp0.venv\Scripts\python.exe" -c "import sys,struct,platform;print(sys.version);print('bits:',struct.calcsize('P')*8, platform.platform())"
"%~dp0.venv\Scripts\python.exe" -m pip show PyQt5 PyQt5-Qt5
dir "%~dp0.venv\Lib\site-packages\PyQt5\Qt5\plugins\platforms"
echo Folder: %~dp0
echo ===== running app (console) =====
"%~dp0.venv\Scripts\python.exe" -m COMTool.Main
echo.
echo ===== exit code: %errorlevel% =====
pause
