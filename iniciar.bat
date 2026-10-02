@echo off
rem Arranca Ollama si no está en marcha y abre JARVIS sin ventana de consola.
cd /d "%~dp0"
tasklist /FI "IMAGENAME eq ollama.exe" | find /I "ollama.exe" >nul || start "" /min "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" serve
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py"
