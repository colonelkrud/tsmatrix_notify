@echo off
REM ────────────────────────────────────────────────────────────────────────────
REM tsmatrix_notify_run.bat
REM Usage: tsmatrix_notify_run.bat [--no-startup] [--debug] [...]
REM   e.g.    tsmatrix_notify_run.bat --no-startup --debug
REM
REM This wrapper:
REM  • cd’s to the script dir
REM  • creates a logs\ folder if missing
REM  • builds a timestamped logfile name
REM  • runs the bot, forwarding all args
REM  • redirects both stdout and stderr to the logfile
REM ────────────────────────────────────────────────────────────────────────────

REM ----- 1) Figure out where we live -----
SETLOCAL ENABLEDELAYEDEXPANSION
SET "SCRIPT_DIR=%~dp0"
CD /D "%SCRIPT_DIR%"

REM ----- 2) Ensure logs directory exists -----
SET "LOG_DIR=%SCRIPT_DIR%logs"
IF NOT EXIST "%LOG_DIR%" (
    MKDIR "%LOG_DIR%"
)

REM ----- 3) Build a safe timestamp -----
REM %DATE% is locale-dependent, so we parse via WMIC for yyyy-MM-dd_HH-mm-ss
FOR /F "skip=1 tokens=1-2 delims=." %%A IN ('WMIC OS GET LocalDateTime ^| FINDSTR /R "[0-9]"') DO (
    SET "ts=%%A"
    GOTO :gotTS
)
:gotTS
REM ts looks like: 20250530214430.123000-420
SET "YYYY=!ts:~0,4!"
SET "MM=!ts:~4,2!"
SET "DD=!ts:~6,2!"
SET "HH=!ts:~8,2!"
SET "Min=!ts:~10,2!"
SET "SS=!ts:~12,2!"
SET "STAMP=!YYYY!-!MM!-!DD!_!HH!-!Min!-!SS!"

REM ----- 4) Compose logfile path -----
SET "LOGFILE=%LOG_DIR%\bot_%STAMP%.log"

REM ----- 5) Invoke the bot -----
REM    - All arguments (%*) are passed through
REM    - Both stdout and stderr redirected to the logfile
"%SCRIPT_DIR%\.venv\Scripts\python.exe" "%SCRIPT_DIR%tsmatrix_notify.py" %* >"%LOGFILE%" 2>&1

REM ----- 6) Done -----
ENDLOCAL
