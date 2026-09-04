@echo off
call "%~dp0commit_and_push.bat" %*
exit /b %errorlevel%
