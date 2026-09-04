@echo off
setlocal EnableExtensions DisableDelayedExpansion

rem Stage, commit, and push every change below this script's folder.
cd /d "%~dp0"

for /f "delims=" %%R in ('git rev-parse --show-toplevel 2^>nul') do set "REPO=%%R"
if not defined REPO (
    echo Error: this script must be run from inside a Git repository.
    exit /b 1
)

for /f "delims=" %%P in ('git rev-parse --show-prefix 2^>nul') do set "RELATIVE_DIR=%%P"
for /f "delims=" %%U in ('git -C "%REPO%" remote get-url origin 2^>nul') do set "ORIGIN_URL=%%U"

if /I not "%ORIGIN_URL%"=="https://github.com/john22175/tv.git" (
    echo Error: origin must point to https://github.com/john22175/tv.git
    echo Current origin: %ORIGIN_URL%
    exit /b 1
)

rem Do not accidentally include anything previously staged outside this folder.
git -C "%REPO%" diff --cached --quiet -- . ":(exclude)%RELATIVE_DIR%"
if errorlevel 1 (
    echo Error: changes outside this folder are already staged.
    echo Commit or unstage them before running this script.
    exit /b 1
)

git -C "%REPO%" add -A -- "%RELATIVE_DIR%"
git -C "%REPO%" diff --cached --quiet
if not errorlevel 1 (
    echo No changes to commit in %~dp0
    exit /b 0
)

set "MESSAGE=%~1"
if not defined MESSAGE set /p "MESSAGE=Commit message: "
if not defined MESSAGE set "MESSAGE=Update Git_source files"

git -C "%REPO%" commit -m "%MESSAGE%"
if errorlevel 1 exit /b 1

git -C "%REPO%" push origin HEAD
if errorlevel 1 exit /b 1

echo Commit and push completed.
exit /b 0
