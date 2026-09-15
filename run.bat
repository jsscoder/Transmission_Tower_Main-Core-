@echo off
setlocal EnableExtensions

REM ============================================================
REM Transmission Tower DXF -> Shop Drawing Pipeline
REM ============================================================

if "%~1"=="" (
    echo.
    echo Usage:
    echo   run.bat assembly.dxf
    echo.
    echo Full generation:
    echo   set DESIGN_INPUT=connection_design.json
    echo   set RULES=design_rules.json
    echo   set REFERENCE_JSON_DIR=existing_shop_json
    echo   run.bat assembly.dxf pipeline_out
    echo.
    exit /b 2
)

set "DXF_PATH=%~1"
set "OUT_DIR=%~2"

if "%OUT_DIR%"=="" (
    set "OUT_DIR=pipeline_out"
)

if not exist "%DXF_PATH%" (
    echo.
    echo ERROR: DXF not found:
    echo %DXF_PATH%
    echo.
    exit /b 2
)

set "PYTHON_EXE=python"
where python >nul 2>&1
if errorlevel 1 (
    if exist "%~dp0my_venv\Scripts\python.exe" (
        set "PYTHON_EXE=%~dp0my_venv\Scripts\python.exe"
    ) else (
        echo.
        echo ERROR: Python not found in PATH.
        echo Install Python or activate your virtual environment.
        echo.
        exit /b 2
    )
)

echo.
echo ============================================================
echo  Transmission Tower Shop Drawing Pipeline
echo ============================================================
echo DXF    : %DXF_PATH%
echo Output : %OUT_DIR%
"%PYTHON_EXE%" --version
echo ============================================================
echo.

set "ARGS=inference.py --dxf "%DXF_PATH%" --out "%OUT_DIR%""

if defined DESIGN_INPUT (
    set "ARGS=%ARGS% --design-input "%DESIGN_INPUT%""
)

if defined RULES (
    set "ARGS=%ARGS% --rules "%RULES%""
)

if defined REFERENCE_JSON_DIR (
    set "ARGS=%ARGS% --reference-shop-json-dir "%REFERENCE_JSON_DIR%""
)

REM Generate only when design input is supplied.
if defined DESIGN_INPUT (
    set "ARGS=%ARGS% --generate"
)

echo [RUN]
echo "%PYTHON_EXE%" %ARGS%
echo.

"%PYTHON_EXE%" %ARGS%

set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo ============================================================
echo  Pipeline finished
echo  Output: %OUT_DIR%
echo  Exit code: %EXIT_CODE%
echo ============================================================
echo.

exit /b %EXIT_CODE%