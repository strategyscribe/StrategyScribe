"""Načítanie a ukladanie nastavení appky (lokálny config.json súbor, mimo git)."""

import json

from . import paths

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
}


def load():
    """Načíta nastavenia z config.json. Chýbajúce polia doplní predvolenými hodnotami."""
    config = dict(DEFAULT_CONFIG)
    if paths.CONFIG_PATH.exists():
        try:
            saved = json.loads(paths.CONFIG_PATH.read_text(encoding="utf-8"))
            config.update(saved)
        except (json.JSONDecodeError, OSError):
            pass
    return config


def save(config):
    """Uloží nastavenia do config.json."""
    paths.CONFIG_PATH.write_text(
        json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8"
    )
