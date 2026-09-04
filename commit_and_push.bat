@echo off
setlocal EnableExtensions DisableDelayedExpansion

rem Stage only the maintained repository areas; local helpers and scratch files stay local.
cd /d "%~dp0"

for /f "delims=" %%R in ('git rev-parse --show-toplevel 2^>nul') do set "REPO=%%R"
if not defined REPO (
    echo Error: this script must be run from inside a Git repository.
    exit /b 1
)

for /f "delims=" %%U in ('git -C "%REPO%" remote get-url origin 2^>nul') do set "ORIGIN_URL=%%U"

if /I not "%ORIGIN_URL%"=="https://github.com/john22175/tv.git" (
    echo Error: origin must point to https://github.com/john22175/tv.git
    echo Current origin: %ORIGIN_URL%
    exit /b 1
)

git -C "%REPO%" add -A -- .gitignore README.md sources multihub tizen_receiver_app/app tizen_receiver_app/scripts tizen_receiver_app/tests tizen_receiver_app/README.md tizen_receiver_app/deploy.targets.example.json frontend .github ":(exclude,glob)**/*.bat"
git -C "%REPO%" diff --cached --quiet
if not errorlevel 1 goto :PUSH

set "MESSAGE=%~1"
if not defined MESSAGE set /p "MESSAGE=Commit message: "
if not defined MESSAGE set "MESSAGE=Update Git_source files"

git -C "%REPO%" commit -m "%MESSAGE%"
if errorlevel 1 exit /b 1

:PUSH
git -C "%REPO%" push origin HEAD
if errorlevel 1 exit /b 1

echo Commit and push completed.
exit /b 0
