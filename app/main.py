"""Vstupný bod aplikácie — najprv overí aktualizácie (a ponúkne automatickú
inštaláciu novej verzie, ak beží ako zbalené .exe), potom spustí GUI."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk
from urllib.request import Request, urlopen

from . import gui

APP_VERSION = "0.1.1"
GITHUB_REPO = "tomako21/StrategyScribe"
UPDATE_CHECK_TIMEOUT = 4

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def _parse_version(tag):
    return tuple(int(p) for p in tag.lstrip("v").split(".") if p.isdigit())


def _expected_asset_prefix():
    return f"https://github.com/{GITHUB_REPO}/"


def check_for_update():
    """Overí na GitHub Releases, či existuje novšia verzia než APP_VERSION.
    Vráti dict s informáciami o vydaní (tag, release_url, exe_url, sha256_url)
    ak je dostupná novšia verzia, inak None. Pri akejkoľvek chybe (žiadny
    internet, repozitár ešte neexistuje, ...) sa ticho vzdá — kontrola
    aktualizácie nesmie zabrániť spusteniu programu."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    try:
        with urlopen(url, timeout=UPDATE_CHECK_TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None

    latest_tag = data.get("tag_name", "")
    release_url = data.get("html_url", f"https://github.com/{GITHUB_REPO}/releases")
    # Bezpečnostná poistka: nikdy neotváraj/nesťahuj nič mimo github.com pre
    # tento repozitár, aj keby bola odpoveď API niekedy pozmenená.
    if not release_url.startswith(_expected_asset_prefix()):
        return None
    if _parse_version(latest_tag) <= _parse_version(APP_VERSION):
        return None

    exe_url = None
    sha256_url = None
    for asset in data.get("assets", []):
        name = asset.get("name", "")
        download_url = asset.get("browser_download_url", "")
        if not download_url.startswith(_expected_asset_prefix()):
            continue
        if name == "StrategyScribe.exe":
            exe_url = download_url
        elif name == "StrategyScribe.exe.sha256":
            sha256_url = download_url

    return {
        "tag": latest_tag,
        "release_url": release_url,
        "exe_url": exe_url,
        "sha256_url": sha256_url,
    }


class _ProgressWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("StrategyScribe — aktualizácia")
        self.root.geometry("380x110")
        self.root.resizable(False, False)
        self.label = tk.Label(self.root, text="Sťahujem aktualizáciu...")
        self.label.pack(pady=(16, 8))
        self.progress = ttk.Progressbar(self.root, length=320, mode="determinate", maximum=100)
        self.progress.pack(pady=8)
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)  # nedovoľ zavrieť kým sťahuje
        self.root.update()

    def set_progress(self, fraction, text=None):
        self.progress["value"] = max(0.0, min(fraction, 1.0)) * 100
        if text:
            self.label.config(text=text)
        self.root.update_idletasks()
        self.root.update()

    def close(self):
        self.root.destroy()


def _fetch_text(url, timeout=10):
    with urlopen(url, timeout=timeout) as response:
        return response.read().decode("utf-8").strip()


DOWNLOAD_MAX_RETRIES = 8
DOWNLOAD_READ_TIMEOUT = 30


def _download_with_progress(url, dest_path, progress_window, max_retries=DOWNLOAD_MAX_RETRIES):
    """Stiahne súbor s podporou pokračovania (HTTP Range) a opakovaných
    pokusov — veľký súbor na pomalšom/nestabilnom pripojení môže inak
    ľahko naraziť na timeout uprostred sťahovania. Vráti SHA-256 hash
    kompletného súboru."""
    hasher = hashlib.sha256()
    downloaded = 0
    total = None

    if dest_path.exists():
        dest_path.unlink()

    for attempt in range(1, max_retries + 1):
        headers = {"Range": f"bytes={downloaded}-"} if downloaded else {}
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=DOWNLOAD_READ_TIMEOUT) as response:
                is_resumed = response.status == 206
                if not is_resumed and downloaded:
                    # Server nepodporuje Range (poslal by celý súbor odznova) —
                    # začni nanovo, inak by sa obsah zdvojil.
                    downloaded = 0
                    hasher = hashlib.sha256()
                    dest_path.unlink(missing_ok=True)
                if total is None:
                    content_range = response.headers.get("Content-Range", "")
                    if "/" in content_range:
                        total = int(content_range.rsplit("/", 1)[-1])
                    else:
                        total = int(response.headers.get("Content-Length", 0)) or None
                mode = "ab" if downloaded else "wb"
                with open(dest_path, mode) as f:
                    while True:
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        hasher.update(chunk)
                        downloaded += len(chunk)
                        if total:
                            mb_done = downloaded / (1024 * 1024)
                            mb_total = total / (1024 * 1024)
                            progress_window.set_progress(
                                downloaded / total,
                                f"Sťahujem aktualizáciu... {mb_done:.0f} / {mb_total:.0f} MB",
                            )
            return hasher.hexdigest()
        except Exception:
            if attempt == max_retries:
                raise
            progress_window.set_progress(
                (downloaded / total) if total else 0,
                f"Výpadok siete, skúšam znova ({attempt}/{max_retries})...",
            )
            time.sleep(2)
    raise RuntimeError("Sťahovanie zlyhalo po viacerých pokusoch.")


def _apply_update(new_exe_path):
    """Napíše a asynchrónne spustí .bat, ktorý počká kým úplne skončia VŠETKY
    procesy bežiace z aktuálnej .exe cesty (PyInstaller onefile má pri behu
    zvyčajne dva procesy — spúšťací aj samotnú appku, čakanie len na jeden PID
    nestačí), nahradí .exe a znova ho spustí. Táto funkcia sa vracia hneď —
    volajúci musí appku ihneď potom ukončiť (os._exit)."""
    current_exe = Path(sys.executable)
    bat_path = Path(tempfile.gettempdir()) / "strategyscribe_update.bat"
    ps_still_running = (
        f"if (Get-Process | Where-Object {{ $_.Path -eq '{current_exe}' }}) "
        "{ exit 1 } else { exit 0 }"
    )
    bat_content = (
        "@echo off\r\n"
        ":wait\r\n"
        f'powershell -NoProfile -Command "{ps_still_running}"\r\n'
        "if errorlevel 1 (\r\n"
        "    timeout /t 1 /nobreak >NUL\r\n"
        "    goto wait\r\n"
        ")\r\n"
        "timeout /t 1 /nobreak >NUL\r\n"
        "set MOVE_RETRY=0\r\n"
        ":move\r\n"
        f'move /Y "{new_exe_path}" "{current_exe}" >NUL 2>&1\r\n'
        "if errorlevel 1 (\r\n"
        "    set /a MOVE_RETRY+=1\r\n"
        "    if %MOVE_RETRY% GEQ 10 goto giveup\r\n"
        "    timeout /t 1 /nobreak >NUL\r\n"
        "    goto move\r\n"
        ")\r\n"
        f'start "" "{current_exe}"\r\n'
        ":giveup\r\n"
        'del "%~f0"\r\n'
    )
    bat_path.write_text(bat_content, encoding="utf-8")
    subprocess.Popen(
        ["cmd.exe", "/c", str(bat_path)],
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


def _run_auto_update(info):
    progress = _ProgressWindow()
    try:
        new_exe_path = Path(tempfile.gettempdir()) / "StrategyScribe_new.exe"
        progress.set_progress(0, "Overujem kontrolný súčet...")
        expected_sha256 = _fetch_text(info["sha256_url"]).split()[0].lower()
        actual_sha256 = _download_with_progress(info["exe_url"], new_exe_path, progress)
        if actual_sha256.lower() != expected_sha256:
            progress.close()
            new_exe_path.unlink(missing_ok=True)
            messagebox.showerror(
                "Chyba aktualizácie",
                "Stiahnutý súbor sa nezhoduje s očakávaným kontrolným súčtom — "
                "aktualizácia bola kvôli bezpečnosti zrušená. Appka bude pokračovať "
                "v pôvodnej verzii; novú si môžeš stiahnuť ručne z GitHub Releases.",
            )
            return
        progress.set_progress(1.0, "Inštalujem...")
        _apply_update(new_exe_path)
        progress.close()
        os._exit(0)
    except Exception as exc:
        progress.close()
        messagebox.showerror(
            "Chyba aktualizácie",
            f"Automatická aktualizácia zlyhala ({exc}). Appka bude pokračovať v "
            "pôvodnej verzii — novú verziu si môžeš stiahnuť ručne z GitHub Releases.",
        )


def _prompt_update(info):
    root = tk.Tk()
    root.withdraw()
    can_auto_update = getattr(sys, "frozen", False) and info.get("exe_url") and info.get("sha256_url")

    if can_auto_update:
        should_update = messagebox.askyesno(
            "Nová verzia",
            f"K dispozícii je nová verzia {info['tag']} (aktuálna: {APP_VERSION}).\n\n"
            "Chceš ju automaticky stiahnuť a nainštalovať?",
        )
        root.destroy()
        if should_update:
            _run_auto_update(info)
    else:
        should_open = messagebox.askyesno(
            "Nová verzia",
            f"K dispozícii je nová verzia {info['tag']} (aktuálna: {APP_VERSION}).\n\n"
            "Chceš otvoriť stránku na stiahnutie?",
        )
        root.destroy()
        if should_open:
            webbrowser.open(info["release_url"])


def main():
    info = check_for_update()
    if info:
        _prompt_update(info)
    gui.run()


if __name__ == "__main__":
    main()
