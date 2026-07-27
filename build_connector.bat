@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set "OUTPUT_ROOT=D:\AI\Agent_Data\Generated_Files\xianyu-local-connector"
if not exist "%OUTPUT_ROOT%" mkdir "%OUTPUT_ROOT%"
python -m PyInstaller --noconfirm --clean --onefile --windowed --name XianyuConnector --paths "%CD%" --distpath "%OUTPUT_ROOT%" --workpath "%OUTPUT_ROOT%\build" --specpath "%OUTPUT_ROOT%\spec" connector\main.py
if errorlevel 1 exit /b 1
certutil -hashfile "%OUTPUT_ROOT%\XianyuConnector.exe" SHA256 > "%OUTPUT_ROOT%\XianyuConnector.sha256.txt"
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo Inno Setup not found. EXE build completed without installer.
  exit /b 2
)
"%ISCC%" connector_installer.iss
if errorlevel 1 exit /b 1
certutil -hashfile "%OUTPUT_ROOT%\XianyuConnectorSetup-0.1.0.exe" SHA256 > "%OUTPUT_ROOT%\XianyuConnectorSetup-0.1.0.sha256.txt"
copy /Y "connector\README.md" "%OUTPUT_ROOT%\README.md" >nul
powershell -NoProfile -Command "$h=(Get-FileHash -Algorithm SHA256 '%OUTPUT_ROOT%\XianyuConnectorSetup-0.1.0.exe').Hash.ToLower(); [ordered]@{version='0.1.0';platform='windows-x64';filename='XianyuConnectorSetup-0.1.0.exe';sha256=$h;mandatory=$false;signed=$false}|ConvertTo-Json|Set-Content -Encoding UTF8 '%OUTPUT_ROOT%\version.json'"
echo Build complete: %OUTPUT_ROOT%\XianyuConnectorSetup-0.1.0.exe
