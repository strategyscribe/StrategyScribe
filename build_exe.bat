@echo off
echo Balim StrategyScribe do .exe...
pyinstaller --onefile --windowed --noconfirm --name StrategyScribe --collect-data customtkinter --collect-data faster_whisper run.py
echo.
echo Hotovo! .exe najdes v priecinku dist\StrategyScribe.exe
pause
