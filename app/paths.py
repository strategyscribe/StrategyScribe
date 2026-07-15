"""Cesty k dočasným súborom, výstupom a konfigurácii."""

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path


def get_app_dir():
    """Priečinok appky — pri vývoji koreň projektu, po zbalení priečinok .exe súboru."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _get_known_folder(guid_fields, fallback):
    """Priečinok používateľa cez Windows Known Folder API — funguje aj keď je
    priečinok premenovaný (lokalizovaný Windows)."""
    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_byte * 8),
        ]

    try:
        data1, data2, data3, data4 = guid_fields
        folder_id = GUID(data1, data2, data3, (ctypes.c_byte * 8)(*data4))
        path_ptr = ctypes.c_wchar_p()
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id), 0, 0, ctypes.byref(path_ptr)
        )
        if result == 0:
            path = Path(path_ptr.value)
            ctypes.windll.ole32.CoTaskMemFree(path_ptr)
            return path
    except Exception:
        pass
    return fallback


def get_documents_dir():
    return _get_known_folder(
        (0xFDD39AD0, 0x238F, 0x46AF, (0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7)),
        Path.home() / "Documents",
    )


def get_desktop_dir():
    return _get_known_folder(
        (0xB4BFCC3A, 0xDB2C, 0x424C, (0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41)),
        Path.home() / "Desktop",
    )


def create_desktop_shortcut():
    """Vytvorí (alebo prepíše) odkaz na program na pracovnej ploche. Vráti cestu
    k odkazu. Odkaz sa vytvára cez PowerShell v samostatnom procese, aby sa
    nezasahovalo do COM stavu appky (pycaw/soundcard)."""
    import subprocess

    if getattr(sys, "frozen", False):
        target = Path(sys.executable).resolve()
    else:
        # Pri vývoji zo zdrojáku by odkaz nemal zmysel — ukáž na run.py cez python.
        raise RuntimeError("Odkaz na ploche sa dá vytvoriť len zo zabalenej .exe verzie programu.")

    shortcut_path = get_desktop_dir() / "StrategyScribe.lnk"
    ps_command = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{shortcut_path}'); "
        f"$s.TargetPath = '{target}'; "
        f"$s.WorkingDirectory = '{target.parent}'; "
        f"$s.IconLocation = '{target}'; "
        "$s.Save()"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_command],
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=30,
    )
    if result.returncode != 0 or not shortcut_path.exists():
        error_text = (result.stderr or b"").decode("utf-8", errors="ignore").strip()
        raise RuntimeError(f"Odkaz sa nepodarilo vytvoriť ({error_text or 'neznáma chyba'}).")
    return shortcut_path


def get_default_output_dir():
    return get_documents_dir() / "StrategyScribe"


def get_temp_dir():
    """Dočasný priečinok appky pre screenshoty a surový zvuk počas spracovania."""
    temp_dir = get_app_dir() / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


CONFIG_PATH = get_app_dir() / "config.json"
