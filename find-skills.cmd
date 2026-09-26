@echo off
REM Thin wrapper so agents can call the catalog search without knowing the
REM interpreter path. Keeps find_skills.py the single implementation.
setlocal
set "PY=%LIBRARY_CATALOG_PYTHON%"
if "%PY%"=="" (
  where python >nul 2>nul && set "PY=python"
)
if "%PY%"=="" (
  where python3 >nul 2>nul && set "PY=python3"
)
if "%PY%"=="" (
  echo find-skills: no python interpreter found on PATH 1>&2
  exit /b 127
)
"%PY%" "%~dp0find_skills.py" %*
