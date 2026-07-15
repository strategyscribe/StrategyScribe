@echo off
echo Balim StrategyScribe do .exe...
pyinstaller --onefile --windowed --noconfirm --name StrategyScribe --icon app\icon.ico --collect-data customtkinter --collect-data faster_whisper --collect-data imageio_ffmpeg run.py
if errorlevel 1 goto :eof

echo.
echo Pocitam kontrolny sucet (SHA-256)...
certutil -hashfile dist\StrategyScribe.exe SHA256 > dist\_hash_raw.txt
findstr /v /c:"hash" /v /c:"CertUtil" dist\_hash_raw.txt > dist\_hash_clean.txt
for /f "usebackq delims= " %%h in (dist\_hash_clean.txt) do echo %%h > dist\StrategyScribe.exe.sha256
del dist\_hash_raw.txt dist\_hash_clean.txt

echo.
echo Hotovo! .exe aj kontrolny sucet su v priecinku dist\
pause
