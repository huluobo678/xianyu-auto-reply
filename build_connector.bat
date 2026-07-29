@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set "OUTPUT_ROOT=D:\AI\Agent_Data\Generated_Files\xianyu-local-connector"
if not exist "%OUTPUT_ROOT%" mkdir "%OUTPUT_ROOT%"
python -m pip install -r connector\build-requirements.txt
if errorlevel 1 exit /b 1
for /f %%V in ('python -c "from connector import __version__; print(__version__)"') do set "APP_VERSION=%%V"
python -m PyInstaller --noconfirm --clean --onedir --windowed --name XianyuConnector --version-file "%CD%\connector\windows_version_info.txt" --paths "%CD%" --exclude-module pytest --exclude-module _pytest --exclude-module pkg_resources --hidden-import aiohttp --hidden-import httpx --hidden-import loguru --hidden-import pystray --hidden-import PIL --hidden-import qrcode --hidden-import qrcode.constants --hidden-import websockets --add-data "%CD%\backend-web\app\services\qr_login\manager.py;backend-web\app\services\qr_login" --add-data "%CD%\backend-web\app\services\qr_login\face_verification.py;backend-web\app\services\qr_login" --add-data "%CD%\websocket\app\services\xianyu\connection_manager.py;websocket\app\services\xianyu" --add-data "%CD%\websocket\app\services\xianyu\message_handler.py;websocket\app\services\xianyu" --add-data "%CD%\websocket\app\services\xianyu\utils.py;websocket\app\services\xianyu" --add-data "%CD%\common\utils\xianyu_utils.py;common\utils" --add-data "%CD%\common\utils\xianyu_message_parser.py;common\utils" --add-data "%CD%\connector\xianyu_runtime.py;connector" --add-data "%CD%\connector\xianyu_token.py;connector" --distpath "%OUTPUT_ROOT%\dist" --workpath "%OUTPUT_ROOT%\build" --specpath "%OUTPUT_ROOT%\spec" connector\main.py
if errorlevel 1 exit /b 1
powershell -NoProfile -Command "$p=Start-Process -FilePath '%OUTPUT_ROOT%\dist\XianyuConnector\XianyuConnector.exe' -ArgumentList @('--self-test','--self-test-output','%OUTPUT_ROOT%\self-test.json') -WindowStyle Hidden -PassThru -Wait; exit $p.ExitCode"
if errorlevel 1 exit /b 1
python -m PyInstaller.utils.cliutils.archive_viewer -l "%OUTPUT_ROOT%\dist\XianyuConnector\XianyuConnector.exe" > "%OUTPUT_ROOT%\archive-contents.txt"
findstr /I /R "\.env$ tests\\ .*\.pfx$ .*\.pem$ .*\.key$" "%OUTPUT_ROOT%\archive-contents.txt" >nul
if not errorlevel 1 (
  echo Forbidden test, environment, or key file found in package.
  exit /b 1
)
powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 '%OUTPUT_ROOT%\dist\XianyuConnector\XianyuConnector.exe').Hash.ToLower() | Set-Content -Encoding ASCII '%OUTPUT_ROOT%\XianyuConnector.sha256.txt'"
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo Inno Setup not found. EXE build completed without installer.
  exit /b 2
)
"%ISCC%" /DAppVersion=%APP_VERSION% connector_installer.iss
if errorlevel 1 exit /b 1
set "INSTALLER=%OUTPUT_ROOT%\XianyuConnectorSetup-%APP_VERSION%.exe"
powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 '%INSTALLER%').Hash.ToLower() | Set-Content -Encoding ASCII '%OUTPUT_ROOT%\XianyuConnectorSetup-%APP_VERSION%.sha256.txt'"
copy /Y "connector\README.md" "%OUTPUT_ROOT%\README.md" >nul
powershell -NoProfile -Command "$f=Get-Item '%INSTALLER%'; $h=(Get-FileHash -Algorithm SHA256 $f.FullName).Hash.ToLower(); [ordered]@{version='%APP_VERSION%';platform='windows-x64';filename=$f.Name;sha256=$h;file_size_bytes=$f.Length;published_at=(Get-Date).ToUniversalTime().ToString('o');mandatory=$false;signed=$false}|ConvertTo-Json|Set-Content -Encoding UTF8 '%OUTPUT_ROOT%\version.json'"
echo Build complete: %INSTALLER%
