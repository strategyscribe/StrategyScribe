"""Načítanie a ukladanie nastavení appky (lokálny config.json súbor, mimo git).

API kľúč sa na disku ukladá zašifrovaný cez Windows DPAPI (CryptProtectData) —
viazaný na konkrétne Windows prihlásenie používateľa, takže je nečitateľný pre
kohokoľvek iného/na inom počítači, aj keby súbor fyzicky skopíroval."""

import base64
import json

from . import paths

try:
    import win32crypt
    _DPAPI_AVAILABLE = True
except ImportError:
    _DPAPI_AVAILABLE = False

_PROTECTED_PREFIX = "dpapi:"

DEFAULT_CONFIG = {
    "app_mode": "full",  # "full", "video_only" alebo "audio_only"
    "api_key": "",
    "output_dir": str(paths.get_default_output_dir()),
    "screenshot_interval": 10,
    "whisper_model": "medium",
    "language": "auto",
    "audio_source_mode": "device",  # "device" alebo "process"
    "audio_device": None,
    "audio_process_names": [],  # napr. ["opera.exe"] — použije sa keď audio_source_mode == "process"
    "video_source_mode": "all_monitors",  # "all_monitors", "monitor" alebo "region"
    "monitor_index": 1,
    "screen_region": None,  # {"left","top","width","height"} — použije sa keď video_source_mode == "region"
    "video_fps": 10,
    "video_quality": "medium",  # "low", "medium" alebo "high"
    "video_include_audio": False,  # len pre app_mode == "video_only"
    "ai_model": "claude-opus-4-8",
    "cost_limit_usd": 0.0,  # 0 = bez limitu; appka zastaví ďalšiu AI analýzu po dosiahnutí
    "encrypt_output": False,
    "encryption_public_key_pem": "",  # verejný RSA kľúč z Bot Z / Aurion (PEM text)
    "keep_temp_files": False,
    "desktop_shortcut_offered": False,  # jednorazová ponuka odkazu na ploche pri prvom spustení
    "install_prompt_declined": False,  # používateľ odmietol inštaláciu na stále miesto — nepýtať sa znova
}


def _protect(plaintext):
    """Zašifruje reťazec cez DPAPI, viazané na aktuálneho Windows používateľa."""
    if not plaintext or not _DPAPI_AVAILABLE:
        return plaintext
    blob = win32crypt.CryptProtectData(
        plaintext.encode("utf-8"), "StrategyScribe", None, None, None, 0
    )
    return _PROTECTED_PREFIX + base64.b64encode(blob).decode("ascii")


def _unprotect(value):
    """Opačná operácia. Stará (pred-DPAPI) konfigurácia mala kľúč v čistom
    texte — taký sa vráti nezmenený a pri ďalšom uložení sa automaticky
    zašifruje."""
    if not value or not value.startswith(_PROTECTED_PREFIX):
        return value
    if not _DPAPI_AVAILABLE:
        return ""
    blob = base64.b64decode(value[len(_PROTECTED_PREFIX):])
    try:
        _, plaintext = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
    except Exception:
        return ""  # napr. kľúč patrí inému Windows používateľovi/PC — nedá sa rozšifrovať
    return plaintext.decode("utf-8")


def load():
    """Načíta nastavenia z config.json. Chýbajúce polia doplní predvolenými hodnotami."""
    config = dict(DEFAULT_CONFIG)
    if paths.CONFIG_PATH.exists():
        try:
            saved = json.loads(paths.CONFIG_PATH.read_text(encoding="utf-8"))
            config.update(saved)
        except (json.JSONDecodeError, OSError):
            pass
    config["api_key"] = _unprotect(config.get("api_key", ""))
    return config


def save(config):
    """Uloží nastavenia do config.json (API kľúč zašifrovaný cez DPAPI)."""
    to_save = dict(config)
    to_save["api_key"] = _protect(config.get("api_key", ""))
    paths.CONFIG_PATH.write_text(
        json.dumps(to_save, indent=2, ensure_ascii=False), encoding="utf-8"
    )
