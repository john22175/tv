@echo off
setlocal EnableExtensions DisableDelayedExpansion

rem Commit only untracked files below this script's folder, then push to origin.
cd /d "%~dp0"

for /f "delims=" %%R in ('git rev-parse --show-toplevel 2^>nul') do set "REPO=%%R"
if not defined REPO (
    echo Error: this script must be run from inside a Git working tree.
    exit /b 1
)

for /f "delims=" %%P in ('git rev-parse --show-prefix 2^>nul') do set "RELATIVE_DIR=%%P"

for /f "delims=" %%U in ('git -C "%REPO%" remote get-url origin 2^>nul') do set "ORIGIN_URL=%%U"
if not defined ORIGIN_URL (
    echo Error: no ^"origin^" remote is configured.
    echo Expected: https://github.com/john22175/tv.git
    exit /b 1
)
if /I not "%ORIGIN_URL%"=="https://github.com/john22175/tv.git" (
    echo Error: origin points to %ORIGIN_URL%
    echo Expected: https://github.com/john22175/tv.git
    exit /b 1
)

rem Reuse only staged additions that are entirely inside this script's folder.
rem Any staged edit, deletion, or file outside this folder is left for the user.
set "REUSING_STAGED_FILES="
git -C "%REPO%" diff --cached --quiet
if errorlevel 1 (
    git -C "%REPO%" diff --cached --quiet -- . ":(exclude)%RELATIVE_DIR%"
    if errorlevel 1 goto :UNSAFE_STAGED_CHANGES

    git -C "%REPO%" diff --cached --quiet --diff-filter=CDMRTUXB -- "%RELATIVE_DIR%"
    if errorlevel 1 goto :UNSAFE_STAGED_CHANGES

    rem Do not commit an old staged version of a file that later changed or vanished.
    git -C "%REPO%" diff-files --quiet -- "%RELATIVE_DIR%"
    if errorlevel 1 goto :UNSAFE_STAGED_CHANGES

    set "REUSING_STAGED_FILES=1"
    echo Resuming with already staged new files in %~dp0
)

set "FOUND_NEW_FILE="
for /f "delims=" %%F in ('git -C "%REPO%" ls-files --others --exclude-standard -- "%RELATIVE_DIR%"') do set "FOUND_NEW_FILE=1"

if not defined FOUND_NEW_FILE if not defined REUSING_STAGED_FILES (
    echo No new untracked files were found below %~dp0
    exit /b 0
)

if defined FOUND_NEW_FILE (
    rem NUL-delimited paths preserve spaces and non-ASCII filenames.
    git -C "%REPO%" ls-files -z --others --exclude-standard -- "%RELATIVE_DIR%" | git -C "%REPO%" add --pathspec-from-file=- --pathspec-file-nul
    if errorlevel 1 (
        echo Staging new files failed.
        exit /b 1
    )
)

git -C "%REPO%" diff --cached --name-status

set "MESSAGE=%~1"
if not defined MESSAGE set /p "MESSAGE=Commit message: "
if not defined MESSAGE set "MESSAGE=Add new files"

git -C "%REPO%" commit -m "%MESSAGE%"
if errorlevel 1 (
    echo Commit failed; nothing was pushed.
    exit /b 1
)

git -C "%REPO%" push origin HEAD
if errorlevel 1 (
    echo Push failed. The commit remains local.
    exit /b 1
)

echo Done.
exit /b 0

:UNSAFE_STAGED_CHANGES
echo Error: staged files include an edit, deletion, change after staging, or a file outside %~dp0
echo Commit or unstage those changes first.
exit /b 1
