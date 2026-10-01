@echo off
REM Construction de l executable Windows AMUNTCHI V5 (app.py + core.py + cloud_sync.py)
python -m pip install -r requirements.txt
python -m PyInstaller --clean --noconfirm --windowed --name Amuntchi --add-data "assets;assets" app.py
pause
