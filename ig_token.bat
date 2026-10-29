@echo off
chcp 65001 >nul
cd /d "%~dp0"
python --version >nul 2>&1 || (
  echo HATA: Python kurulu degil. python.org'dan kur, kurulumda "Add to PATH" kutusunu isaretle.
  pause
  exit /b 1
)
python -m pip install --quiet requests
python ig_token.py
pause
