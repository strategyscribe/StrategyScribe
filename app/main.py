"""Vstupný bod aplikácie — najprv ponúkne inštaláciu na stále miesto (ak beží
zo stiahnutej kópie), potom overí aktualizácie (a ponúkne automatickú
inštaláciu novej verzie, ak beží ako zbalené .exe), nakoniec spustí GUI."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk
from urllib.request import Request, urlopen

from . import config as config_module, gui

APP_VERSION = "0.1.4"
GITHUB_REPO = "strategyscribe/StrategyScribe"
# Repozitár bol presunutý z tomako21/StrategyScribe pod organizáciu — stará
# adresa presmerováva na novú, obe sú dôveryhodné pre bezpečnostnú kontrolu.
LEGACY_GITHUB_REPO = "tomako21/StrategyScribe"
UPDATE_CHECK_TIMEOUT = 6

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def _parse_version(tag):
    return tuple(int(p) for p in tag.lstrip("v").split(".") if p.isdigit())


def _trusted_url(url):
    return url.startswith(f"https://github.com/{GITHUB_REPO}/") or url.startswith(
        f"https://github.com/{LEGACY_GITHUB_REPO}/"
    )


def check_for_update():
    """Overí na GitHub Releases, či existuje novšia verzia než APP_VERSION.
    Vráti dict s informáciami o vydaní (tag, release_url, exe_url, sha256_url)
    ak je dostupná novšia verzia, inak None. Pri akejkoľvek chybe (žiadny
    internet, repozitár ešte neexistuje, ...) sa ticho vzdá — kontrola
    aktualizácie nesmie zabrániť spusteniu programu. Na kolísavej sieti môže
    jediný pokus s krátkym limitom zlyhať, preto skúša dvakrát."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    data = None
    for attempt in range(2):
        try:
            with urlopen(url, timeout=UPDATE_CHECK_TIMEOUT) as response:
                data = json.loads(response.read().decode("utf-8"))
            break
        except Exception:
            if attempt == 0:
                time.sleep(1)
    if data is None:
        return None

    latest_tag = data.get("tag_name", "")
    release_url = data.get("html_url", f"https://github.com/{GITHUB_REPO}/releases")
    # Bezpečnostná poistka: nikdy neotváraj/nesťahuj nič mimo github.com pre
    # tento repozitár, aj keby bola odpoveď API niekedy pozmenená.
    if not _trusted_url(release_url):
        return None
    if _parse_version(latest_tag) <= _parse_version(APP_VERSION):
        return None

    exe_url = None
    sha256_url = None
    for asset in data.get("assets", []):
        name = asset.get("name", "")
        download_url = asset.get("browser_download_url", "")
        if not _trusted_url(download_url):
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


DOWNLOAD_MAX_RETRIES = 10


def _find_curl():
    for candidate in ("curl.exe", "curl"):
        path = shutil.which(candidate)
        if path:
            return path
    # Windows 10 (1803+) / 11 vždy majú vstavaný System32\curl.exe, aj keby
    # PATH bol z nejakého dôvodu nezvyčajný.
    system32_curl = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "curl.exe"
    if system32_curl.exists():
        return str(system32_curl)
    raise RuntimeError("curl.exe sa nenašiel — automatická aktualizácia nie je možná.")


def _download_with_progress(url, dest_path, progress_window, max_retries=DOWNLOAD_MAX_RETRIES):
    """Stiahne súbor cez systémový curl.exe (s podporou pokračovania -C - a
    opakovaných pokusov --retry) — spoľahlivejšie na pomalších/nestabilných
    pripojeniach než čisto Python riešenie. Počas sťahovania sleduje veľkosť
    súboru na disku a priebežne aktualizuje progress bar. Vráti SHA-256 hash
    kompletného súboru."""
    if dest_path.exists():
        dest_path.unlink()

    total = None
    try:
        head_request = Request(url, method="HEAD")
        with urlopen(head_request, timeout=15) as response:
            total = int(response.headers.get("Content-Length", 0)) or None
    except Exception:
        pass  # nekritické — progress bar bude bez celkového súčtu, sťahovanie beží ďalej

    curl_exe = _find_curl()
    process = subprocess.Popen(
        [
            curl_exe, "-L", "--fail", "--silent", "--show-error",
            "--retry", str(max_retries), "--retry-delay", "2", "--retry-all-errors",
            "-C", "-",
            "-o", str(dest_path),
            url,
        ],
        creationflags=subprocess.CREATE_NO_WINDOW,
        stderr=subprocess.PIPE,
    )

    while process.poll() is None:
        time.sleep(0.5)
        size = dest_path.stat().st_size if dest_path.exists() else 0
        if total:
            progress_window.set_progress(
                size / total,
                f"Sťahujem aktualizáciu... {size / (1024*1024):.0f} / {total / (1024*1024):.0f} MB",
            )
        else:
            progress_window.set_progress(0, f"Sťahujem aktualizáciu... {size / (1024*1024):.0f} MB")

    if process.returncode != 0:
        stderr_text = (process.stderr.read() or b"").decode("utf-8", errors="ignore").strip()
        raise RuntimeError(f"Sťahovanie zlyhalo (curl: {stderr_text or process.returncode}).")

    progress_window.set_progress(1.0, "Overujem kontrolný súčet...")
    hasher = hashlib.sha256()
    with open(dest_path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _apply_update(new_exe_path):
    """Napíše a asynchrónne spustí .bat, ktorý počká kým úplne skončia VŠETKY
    procesy bežiace z aktuálnej .exe cesty (PyInstaller onefile má pri behu
    zvyčajne dva procesy — spúšťací aj samotnú appku, čakanie len na jeden PID
    nestačí), nahradí .exe a znova ho spustí.

    Skutočná príčina "Failed to load Python DLL" po výmene: .bat (spustený
    ešte STOU appkou) dedí jej PyInstaller _PYI_* premenné prostredia, takže
    novo spustené .exe si myslí, že je už rozbalené v STAROM (medzičasom
    zmazanom) dočasnom priečinku. Oficiálna dokumentácia PyInstalleru na
    presne tento prípad (spustenie nezávislej inštancie zo starej) odporúča
    PYINSTALLER_RESET_ENVIRONMENT=1 — nastavené nižšie v .bat. Kontrola
    hlavného okna + retry ostáva ako poistka pre iné prechodné zlyhania.

    POZOR: tento .bat generuje VŽDY STARÁ (bežiaca) verzia appky — úpravy tu
    sa prejavia až pri aktualizácii Z verzie, ktorá ich už obsahuje.

    Táto funkcia sa vracia hneď — volajúci musí appku ihneď potom ukončiť
    (os._exit)."""
    current_exe = Path(sys.executable)
    bat_path = Path(tempfile.gettempdir()) / "strategyscribe_update.bat"
    ps_still_running = (
        f"if (Get-Process | Where-Object {{ $_.Path -eq '{current_exe}' }}) "
        "{ exit 1 } else { exit 0 }"
    )
    bat_content = (
        "@echo off\r\n"
        "set PYINSTALLER_RESET_ENVIRONMENT=1\r\n"
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
        "timeout /t 1 /nobreak >NUL\r\n"
        "set START_RETRY=0\r\n"
        ":startapp\r\n"
        f'start "" "{current_exe}"\r\n'
        "set CHECK_COUNT=0\r\n"
        ":checkloop\r\n"
        "timeout /t 1 /nobreak >NUL\r\n"
        "set /a CHECK_COUNT+=1\r\n"
        f'powershell -NoProfile -Command "'
        f"$ok = Get-Process | Where-Object {{ $_.Path -eq '{current_exe}' -and $_.MainWindowTitle -eq 'StrategyScribe' }}; "
        "if ($ok) { exit 0 }; "
        f"$err = Get-Process | Where-Object {{ $_.Path -eq '{current_exe}' -and $_.MainWindowTitle -eq 'Error' }}; "
        'if ($err) { $err | Stop-Process -Force; exit 1 }; exit 2"\r\n'
        "if errorlevel 2 (\r\n"
        "    if %CHECK_COUNT% LSS 20 goto checkloop\r\n"
        "    goto giveup\r\n"
        ")\r\n"
        "if errorlevel 1 (\r\n"
        "    set /a START_RETRY+=1\r\n"
        "    if %START_RETRY% GEQ 5 goto giveup\r\n"
        "    timeout /t 3 /nobreak >NUL\r\n"
        "    goto startapp\r\n"
        ")\r\n"
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


def _install_dir():
    """Stále miesto programu — per-user, bez admin práv (ako napr. VS Code)."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Programs" / "StrategyScribe"


def _spawn_downloaded_copy_cleanup(downloaded_exe, delete_config_too):
    """Asynchrónne zmaže stiahnutú kópiu .exe (a prípadne jej config.json) po
    tom, čo úplne skončia všetky jej procesy — rovnaký vzor ako pri aktualizácii."""
    bat_path = Path(tempfile.gettempdir()) / "strategyscribe_install_cleanup.bat"
    ps_still_running = (
        f"if (Get-Process | Where-Object {{ $_.Path -eq '{downloaded_exe}' }}) "
        "{ exit 1 } else { exit 0 }"
    )
    config_line = ""
    if delete_config_too:
        config_line = f'del "{downloaded_exe.parent / "config.json"}" >NUL 2>&1\r\n'
    bat_content = (
        "@echo off\r\n"
        ":wait\r\n"
        f'powershell -NoProfile -Command "{ps_still_running}"\r\n'
        "if errorlevel 1 (\r\n"
        "    timeout /t 1 /nobreak >NUL\r\n"
        "    goto wait\r\n"
        ")\r\n"
        "timeout /t 1 /nobreak >NUL\r\n"
        "set DEL_RETRY=0\r\n"
        ":delloop\r\n"
        f'del "{downloaded_exe}" >NUL 2>&1\r\n'
        f'if exist "{downloaded_exe}" (\r\n'
        "    set /a DEL_RETRY+=1\r\n"
        "    if %DEL_RETRY% GEQ 10 goto cleanup\r\n"
        "    timeout /t 1 /nobreak >NUL\r\n"
        "    goto delloop\r\n"
        ")\r\n"
        f"{config_line}"
        ":cleanup\r\n"
        'del "%~f0"\r\n'
    )
    bat_path.write_text(bat_content, encoding="utf-8")
    subprocess.Popen(
        ["cmd.exe", "/c", str(bat_path)],
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


def _self_install_check():
    """Ak program beží zo stiahnutej kópie (mimo stáleho miesta), ponúkne
    inštaláciu — skopíruje sa do LOCALAPPDATA\\Programs\\StrategyScribe,
    prenesie nastavenia, spustí nainštalovanú kópiu a stiahnutý súbor zmaže.
    Takto sa na disku nehromadia kópie z každého stiahnutia. Pri odmietnutí
    si voľbu zapamätá (config vedľa .exe) a už sa nepýta."""
    if not getattr(sys, "frozen", False):
        return

    current_exe = Path(sys.executable).resolve()
    install_dir = _install_dir()
    installed_exe = install_dir / "StrategyScribe.exe"
    if current_exe.parent == install_dir:
        return  # už bežíme z nainštalovaného miesta

    cfg = config_module.load()
    if cfg.get("install_prompt_declined"):
        return

    root = tk.Tk()
    root.withdraw()
    if installed_exe.exists():
        question = (
            f"StrategyScribe je už nainštalovaný v:\n{install_dir}\n\n"
            "Chceš nainštalovanú verziu nahradiť touto kópiou a spustiť ju?\n"
            "Táto stiahnutá kópia sa potom automaticky zmaže, aby sa na disku "
            "nehromadili staré verzie.\n\n"
            "(Nie = program pobeží priamo z tohto súboru a už sa nebude pýtať.)"
        )
    else:
        question = (
            f"Chceš StrategyScribe nainštalovať na stále miesto?\n{install_dir}\n\n"
            "Program tak bude na jednom mieste (odkaz na ploche aj aktualizácie "
            "budú vždy smerovať naň) a táto stiahnutá kópia sa po inštalácii "
            "automaticky zmaže.\n\n"
            "(Nie = program pobeží priamo z tohto súboru a už sa nebude pýtať.)"
        )
    should_install = messagebox.askyesno("Inštalácia StrategyScribe", question)
    if not should_install:
        root.destroy()
        cfg["install_prompt_declined"] = True
        config_module.save(cfg)
        return

    try:
        install_dir.mkdir(parents=True, exist_ok=True)
        for attempt in range(3):
            try:
                shutil.copy2(current_exe, installed_exe)
                break
            except OSError:
                if attempt == 2:
                    raise RuntimeError(
                        "nainštalovaná verzia je pravdepodobne práve spustená — "
                        "zatvor ju a skús to znova"
                    )
                time.sleep(1)
        # Prenes nastavenia (API kľúč atď.), ale neprepisuj existujúce na cieli.
        current_config = current_exe.parent / "config.json"
        installed_config = install_dir / "config.json"
        config_migrated = False
        if current_config.exists() and not installed_config.exists():
            shutil.copy2(current_config, installed_config)
            config_migrated = True
    except Exception as exc:
        messagebox.showerror(
            "Inštalácia zlyhala",
            f"Program sa nepodarilo nainštalovať ({exc}).\n\n"
            "Pobeží zatiaľ priamo z tohto súboru.",
        )
        root.destroy()
        return

    root.destroy()
    # Spusti nainštalovanú kópiu ako nezávislú inštanciu (reset PyInstaller
    # prostredia — inak by hľadala rozbalené súbory tejto končiacej kópie).
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen(
        [str(installed_exe)], cwd=str(install_dir), env=env, close_fds=True
    )
    _spawn_downloaded_copy_cleanup(current_exe, delete_config_too=config_migrated)
    os._exit(0)


def main():
    _self_install_check()
    info = check_for_update()
    if info:
        _prompt_update(info)
    gui.run()


if __name__ == "__main__":
    main()
