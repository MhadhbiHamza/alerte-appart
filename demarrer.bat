@echo off
REM Lance l'outil d'alerte appartement (double-clic, ou au demarrage de Windows).
REM Le journal est dans logs\alerte-appart.log
cd /d "%~dp0"
if exist "%USERPROFILE%\anaconda3\Scripts\activate.bat" call "%USERPROFILE%\anaconda3\Scripts\activate.bat"
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
python main.py
