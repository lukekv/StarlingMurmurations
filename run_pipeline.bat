@echo off
:: ============================================================
:: run_pipeline.bat
:: Texture Library Pipeline launcher
::
:: HOW TO USE
:: ----------
:: 1. Edit INPUT_DIR and OUTPUT_DIR below to match your paths.
:: 2. Save the file.
:: 3. Open a terminal in this folder and type:  run_pipeline
::    OR double-click this file in Explorer.
::
:: To resume a cancelled run against the same output folder,
:: just run again without changing anything.  The pipeline
:: picks up from where it left off.
:: ============================================================

:: --- Edit these two lines before each run -------------------

set INPUT_DIR=D:\_AI\Texture Library Image Sorter\_Shared Asset Library
set OUTPUT_DIR=D:\_AI\Texture Library Image Sorter\_Shared Asset Library\_output

:: --- Do not edit below this line ----------------------------

set PYTHON=C:\Python314\python.exe
set PIPELINE_DIR=D:\_AI\Texture Library Image Sorter\Texture Library Image Sorter\texture_pipeline

echo.
echo ============================================================
echo  Texture Library Pipeline
echo  Input  : %INPUT_DIR%
echo  Output : %OUTPUT_DIR%
echo ============================================================
echo.

cd /d "%PIPELINE_DIR%"
"%PYTHON%" main.py --input "%INPUT_DIR%" --output "%OUTPUT_DIR%"

echo.
echo Pipeline finished. Press any key to close.
pause > nul
