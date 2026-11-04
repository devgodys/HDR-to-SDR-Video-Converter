@echo off
setlocal
rem Usage: Compilation.bat [stable|latest]     (default: stable)
rem   stable = pinned versions with SHA-256 hashes (requirements-build-stable.txt) - use this for releases
rem   latest = newest PyPI versions of everything (requirements-build-latest.txt) - to try a build early
rem Everything is written to build_log.txt (send that file if something fails); the window stays open at the end.
set CHANNEL=%1
if "%CHANNEL%"=="" set CHANNEL=stable
set LOG=%~dp0build_log.txt
cd /d "%~dp0"

echo Build log - %DATE% %TIME% > "%LOG%"
echo Channel: %CHANNEL% >> "%LOG%"
echo Folder : %CD% >> "%LOG%"
echo. >> "%LOG%"

echo ===== [1/5] SYSTEM ===== >> "%LOG%"
ver >> "%LOG%" 2>&1
python --version >> "%LOG%" 2>&1
python -c "import sys,struct;print(sys.executable, struct.calcsize('P')*8, 'bit')" >> "%LOG%" 2>&1
echo Smart App Control state (0=off, 1=on, 2=evaluation): >> "%LOG%"
reg query "HKLM\SYSTEM\CurrentControlSet\Control\CI\Policy" /v VerifiedAndReputablePolicyState >> "%LOG%" 2>&1
echo. >> "%LOG%"

echo ===== [2/5] INSTALLING REQUIREMENTS (%CHANNEL%) =====
echo ===== [2/5] INSTALLING REQUIREMENTS (%CHANNEL%) ===== >> "%LOG%"
if /I "%CHANNEL%"=="stable" (
  python -c "import sys; sys.exit(0 if sys.version_info[:2] == (3,15) else 1)"
  if errorlevel 1 (
    set RC=1
    echo *** STEP FAILED: stable builds require Python 3.15 >> "%LOG%"
    echo Stable builds require Python 3.15. Select that interpreter and retry. >> "%LOG%"
    goto :finish
  )

  python -m pip install --require-hashes -r requirements-build-stable.txt >> "%LOG%" 2>&1
) else (
  python -m pip install --upgrade -r requirements-build-latest.txt >> "%LOG%" 2>&1
)
set RC=%errorlevel%
echo pip exit code: %RC% >> "%LOG%"
if not "%RC%"=="0" (
  echo *** STEP FAILED: pip install ^(code %RC%^) >> "%LOG%"
  goto :finish
)
python -m pip list >> "%LOG%" 2>&1
echo. >> "%LOG%"

echo ===== [3/5] BUILDING WITH PYINSTALLER =====
echo ===== [3/5] BUILDING WITH PYINSTALLER ===== >> "%LOG%"
echo started %TIME% >> "%LOG%"
rem --noupx: never UPX-pack the files (packed executables are far more likely to be flagged by antivirus)
python -m PyInstaller --noconfirm --clean --noupx --windowed --onefile ^
  --name "HDR-to-SDR-Converter" ^
  --icon icon.ico ^
  --paths . ^
  --collect-submodules hdr2sdr ^
  --add-data "i18n;i18n" ^
  --add-data "icons;icons" ^
  --add-data "icon.ico;." ^
  hdr2sdr\__main__.py >> "%LOG%" 2>&1
set RC=%errorlevel%
echo finished %TIME% >> "%LOG%"
echo PyInstaller exit code: %RC% >> "%LOG%"
if not "%RC%"=="0" (
  echo *** STEP FAILED: PyInstaller ^(code %RC%^) >> "%LOG%"
  goto :finish
)

echo ===== [4/5] CHECKING THE RESULT ===== >> "%LOG%"
if exist "dist\HDR-to-SDR-Converter.exe" (
  for %%F in ("dist\HDR-to-SDR-Converter.exe") do echo OK: %%~fF, %%~zF bytes >> "%LOG%"
) else (
  echo *** STEP FAILED: dist\HDR-to-SDR-Converter.exe is missing - it was probably removed by antivirus / Smart App Control >> "%LOG%"
  set RC=2
)

:finish
echo. >> "%LOG%"
echo ===== [5/5] WINDOWS SECURITY EVENTS (last 20 minutes) ===== >> "%LOG%"
echo -- Smart App Control / Code Integrity blocks: >> "%LOG%"
powershell -NoProfile -Command "try { Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-CodeIntegrity/Operational'; StartTime=(Get-Date).AddMinutes(-20)} -MaxEvents 25 -ErrorAction Stop | ForEach-Object { '[' + $_.TimeCreated + '] id ' + $_.Id + ': ' + ($_.Message -replace '\s+',' ') } } catch { 'none / not readable: ' + $_.Exception.Message }" >> "%LOG%" 2>&1
echo -- Microsoft Defender detections: >> "%LOG%"
powershell -NoProfile -Command "try { Get-MpThreatDetection | Where-Object { $_.InitialDetectionTime -gt (Get-Date).AddMinutes(-20) } | ForEach-Object { '[' + $_.InitialDetectionTime + '] ' + (($_.Resources) -join '; ') } } catch { 'not readable: ' + $_.Exception.Message }" >> "%LOG%" 2>&1
echo. >> "%LOG%"
echo End of log. RC=%RC% >> "%LOG%"

echo.
echo ---------------- last lines of the log ----------------
powershell -NoProfile -Command "Get-Content -LiteralPath '%LOG%' -Tail 40"
echo --------------------------------------------------------
echo Full log: %LOG%
if "%RC%"=="0" (echo RESULT: build OK - dist\HDR-to-SDR-Converter.exe) else (echo RESULT: FAILED - please send build_log.txt)
echo.
pause
endlocal
