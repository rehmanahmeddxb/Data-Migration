@echo off
rem AMS Database Migration Tool launcher (Windows)
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python migrate_tool.py
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        py migrate_tool.py
    ) else (
        echo Python was not found. Install Python from https://python.org and tick "Add to PATH".
        pause
    )
)
