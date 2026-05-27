@echo off
:: ============================================================
:: run_override_pass.bat
:: Tileability AI Override Pass launcher
::
:: HOW TO USE
:: ----------
:: 1. Set INPUT_DIR and OUTPUT_DIR to match your last pipeline run.
:: 2. Run AFTER the main pipeline (run_pipeline.bat) has completed.
::    The override pass reads the existing database -- the pipeline
::    does not need to re-run.
::
:: What this does:
::   Finds all groups that failed the Stage 3 tileability test and
::   runs AI classification on each one.  Groups whose category is
::   Art, Sky, Utility, or Water are rescued and written to the
::   library.  All others remain in _needs_review/tileability_failed/.
::
:: To add more rescuable categories, edit tileability_override_categories
:: in config.py.
:: ============================================================

:: --- Edit these two lines to match your last pipeline run ----

set INPUT_DIR=D:\_AI\Texture Library Image Sorter\_Shared Asset Library
set OUTPUT_DIR=D:\_AI\Texture Library Image Sorter\_Shared Asset Library\_output

:: --- Do not edit below this line ----------------------------

set PYTHON=C:\Python314\python.exe
set PIPELINE_DIR=D:\_AI\Texture Library Image Sorter\Texture Library Image Sorter\texture_pipeline

echo.
echo ============================================================
echo  Tileability AI Override Pass
echo  Input  : %INPUT_DIR%
echo  Output : %OUTPUT_DIR%
echo ============================================================
echo.

cd /d "%PIPELINE_DIR%"
"%PYTHON%" main.py --input "%INPUT_DIR%" --output "%OUTPUT_DIR%" --override-pass

echo.
echo Override pass finished. Press any key to close.
pause > nul
