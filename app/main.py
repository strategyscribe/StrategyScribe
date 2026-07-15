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
    """Napíše a asynchrónne spustí PowerShell skript, ktorý dokončí aktualizáciu:
    počká kým úplne skončia VŠETKY procesy bežiace z aktuálnej .exe cesty
    (PyInstaller onefile má pri behu zvyčajne dva procesy), nahradí .exe a
    spustí novú verziu. Počas celého priebehu ukazuje jedno normálne okno
    „Inštalujem aktualizáciu…“ s priebehom — všetko beží v JEDNOM skrytom
    procese (CREATE_NO_WINDOW), takže neblikajú žiadne konzolové okná (pôvodný
    .bat spúšťal powershell/timeout v slučkách a každé volanie bliklo oknom).

    "Failed to load Python DLL" po výmene rieši PYINSTALLER_RESET_ENVIRONMENT=1
    (skript dedí _PYI_* premenné starej appky — nová inštancia sa musí správať
    ako nezávislá; oficiálne odporúčanie PyInstalleru). Kontrola okna novej
    verzie + opakované spustenie ostáva ako poistka.

    POZOR: tento skript generuje VŽDY STARÁ (bežiaca) verzia appky — úpravy tu
    sa prejavia až pri aktualizácii Z verzie, ktorá ich už obsahuje.

    Táto funkcia sa vracia hneď — volajúci musí appku ihneď potom ukončiť
    (os._exit)."""
    current_exe = Path(sys.executable)
    ps1_path = Path(tempfile.gettempdir()) / "strategyscribe_update.ps1"
    ps1_content = f"""
$exe = '{current_exe}'
$new = '{new_exe_path}'

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$form = New-Object System.Windows.Forms.Form
$form.Text = 'StrategyScribe — aktualizácia'
$form.ClientSize = New-Object System.Drawing.Size(420, 110)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.ControlBox = $false
$form.TopMost = $true
$label = New-Object System.Windows.Forms.Label
$label.Text = 'Inštalujem aktualizáciu...'
$label.AutoSize = $true
$label.Location = New-Object System.Drawing.Point(20, 20)
$bar = New-Object System.Windows.Forms.ProgressBar
$bar.Style = 'Marquee'
$bar.MarqueeAnimationSpeed = 30
$bar.Location = New-Object System.Drawing.Point(20, 55)
$bar.Size = New-Object System.Drawing.Size(380, 22)
$form.Controls.Add($label)
$form.Controls.Add($bar)
$form.Show()
$form.Refresh()

function Pump {{ [System.Windows.Forms.Application]::DoEvents() }}

# 1. Pockaj, kym skoncia vsetky procesy povodnej verzie.
$label.Text = 'Čakám na ukončenie programu...'
Pump
$deadline = (Get-Date).AddMinutes(3)
while ((Get-Process | Where-Object {{ $_.Path -eq $exe }}) -and ((Get-Date) -lt $deadline)) {{
    Pump
    Start-Sleep -Milliseconds 400
}}
Start-Sleep -Seconds 1

# 2. Vymen subor programu (s opakovanim, kym sa neuvolni).
$label.Text = 'Vymieňam súbor programu...'
Pump
$moved = $false
for ($i = 0; $i -lt 15; $i++) {{
    try {{
        Move-Item -Force -LiteralPath $new -Destination $exe -ErrorAction Stop
        $moved = $true
        break
    }} catch {{
        Pump
        Start-Sleep -Seconds 1
    }}
}}
if (-not $moved) {{
    $label.Text = 'Aktualizácia zlyhala — spusti prosím program ručne.'
    Pump
    Start-Sleep -Seconds 5
    $form.Close()
    Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
    exit 1
}}

# 3. Spusti novu verziu ako nezavislu instanciu (reset PyInstaller prostredia).
$label.Text = 'Spúšťam novú verziu...'
Pump
$env:PYINSTALLER_RESET_ENVIRONMENT = '1'
for ($attempt = 1; $attempt -le 5; $attempt++) {{
    Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe)
    $started = $false
    for ($i = 0; $i -lt 30; $i++) {{
        Pump
        Start-Sleep -Milliseconds 800
        $err = Get-Process | Where-Object {{ $_.Path -eq $exe -and $_.MainWindowTitle -eq 'Error' }}
        if ($err) {{
            $err | Stop-Process -Force
            break
        }}
        if (Get-Process | Where-Object {{ $_.Path -eq $exe -and $_.MainWindowTitle -and $_.MainWindowTitle -ne 'Error' }}) {{
            $started = $true
            break
        }}
    }}
    if ($started) {{ break }}
    Start-Sleep -Seconds 2
}}

$form.Close()
Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
"""
    # utf-8-sig (BOM): Windows PowerShell 5.1 bez BOM cita .ps1 ako ANSI a
    # rozbil by slovenske znaky v textoch okna.
    ps1_path.write_text(ps1_content, encoding="utf-8-sig")
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1_path)],
        creationflags=subprocess.CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
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
    tom, čo úplne skončia všetky jej procesy. Beží ako JEDEN skrytý PowerShell
    proces (CREATE_NO_WINDOW) — žiadne blikajúce konzolové okná."""
    ps1_path = Path(tempfile.gettempdir()) / "strategyscribe_install_cleanup.ps1"
    config_line = ""
    if delete_config_too:
        config_path = downloaded_exe.parent / "config.json"
        config_line = f"Remove-Item -LiteralPath '{config_path}' -Force -ErrorAction SilentlyContinue"
    ps1_content = f"""
$exe = '{downloaded_exe}'
$deadline = (Get-Date).AddMinutes(3)
while ((Get-Process | Where-Object {{ $_.Path -eq $exe }}) -and ((Get-Date) -lt $deadline)) {{
    Start-Sleep -Milliseconds 400
}}
Start-Sleep -Seconds 1
for ($i = 0; $i -lt 10; $i++) {{
    try {{
        Remove-Item -LiteralPath $exe -Force -ErrorAction Stop
        break
    }} catch {{
        Start-Sleep -Seconds 1
    }}
}}
{config_line}
Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
"""
    ps1_path.write_text(ps1_content, encoding="utf-8-sig")
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1_path)],
        creationflags=subprocess.CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
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
            "Obnoví sa aj odkaz na pracovnej ploche a táto stiahnutá kópia sa "
            "potom automaticky zmaže, aby sa na disku nehromadili staré verzie.\n\n"
            "(Nie = program pobeží priamo z tohto súboru a už sa nebude pýtať.)"
        )
    else:
        question = (
            f"Chceš StrategyScribe nainštalovať na stále miesto?\n{install_dir}\n\n"
            "Vytvorí sa odkaz na pracovnej ploche, program bude na jednom mieste "
            "(aktualizácie budú vždy smerovať naň) a táto stiahnutá kópia sa po "
            "inštalácii automaticky zmaže.\n\n"
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

        # Odkaz na ploche je súčasť inštalácie (v dialógu vyššie to bolo
        # oznámené) — a nainštalovaná kópia sa naň už nemá pýtať znova.
        from . import paths as paths_module

        try:
            paths_module.create_desktop_shortcut(target=installed_exe)
        except Exception:
            pass  # bez odkazu sa dá žiť — dá sa vytvoriť v Nastaveniach
        try:
            installed_cfg_data = {}
            if installed_config.exists():
                installed_cfg_data = json.loads(installed_config.read_text(encoding="utf-8"))
            installed_cfg_data["desktop_shortcut_offered"] = True
            installed_config.write_text(
                json.dumps(installed_cfg_data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass
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
