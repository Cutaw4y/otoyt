@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Google Drive izni aliniyor...
if not exist client_secret.json (
  echo HATA: client_secret.json bu klasorde yok. Google Cloud'dan indirip buraya koy.
  pause
  exit /b 1
)
python --version >nul 2>&1 || (
  echo HATA: Python kurulu degil. python.org'dan kur, kurulumda "Add to PATH" kutusunu isaretle.
  pause
  exit /b 1
)
python -m pip install --quiet google-auth-oauthlib
python get_drive_token.py
pause
