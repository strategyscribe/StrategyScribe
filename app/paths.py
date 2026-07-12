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


def get_documents_dir():
    """Skutočný priečinok Dokumenty/Documents aktuálneho používateľa cez Windows
    Known Folder API — funguje aj keď je priečinok premenovaný (lokalizovaný Windows)."""
    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_byte * 8),
        ]

    try:
        folderid_documents = GUID(
            0xFDD39AD0, 0x238F, 0x46AF,
            (ctypes.c_byte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7),
        )
        path_ptr = ctypes.c_wchar_p()
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folderid_documents), 0, 0, ctypes.byref(path_ptr)
        )
        if result == 0:
            path = Path(path_ptr.value)
            ctypes.windll.ole32.CoTaskMemFree(path_ptr)
            return path
    except Exception:
        pass
    return Path.home() / "Documents"


def get_default_output_dir():
    return get_documents_dir() / "StrategyScribe"


def get_temp_dir():
    """Dočasný priečinok appky pre screenshoty a surový zvuk počas spracovania."""
    temp_dir = get_app_dir() / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


CONFIG_PATH = get_app_dir() / "config.json"
