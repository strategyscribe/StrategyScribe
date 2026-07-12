"""Vstupný bod aplikácie — najprv overí aktualizácie, potom spustí GUI."""

import json
import tkinter as tk
import webbrowser
from tkinter import messagebox
from urllib.request import urlopen

from . import gui

APP_VERSION = "0.1.0"
GITHUB_REPO = "tomako21/StrategyScribe"
UPDATE_CHECK_TIMEOUT = 4


def _parse_version(tag):
    return tuple(int(p) for p in tag.lstrip("v").split(".") if p.isdigit())


def check_for_update():
    """Overí na GitHub Releases, či existuje novšia verzia než APP_VERSION.
    Vráti (verzia, url) ak je dostupná novšia verzia, inak None. Pri akejkoľvek
    chybe (žiadny internet, repozitár ešte neexistuje, ...) sa ticho vzdá —
    kontrola aktualizácie nesmie zabrániť spusteniu programu."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    try:
        with urlopen(url, timeout=UPDATE_CHECK_TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
        latest_tag = data.get("tag_name", "")
        release_url = data.get("html_url", f"https://github.com/{GITHUB_REPO}/releases")
        if _parse_version(latest_tag) > _parse_version(APP_VERSION):
            return latest_tag, release_url
    except Exception:
        pass
    return None


def _prompt_update(latest_tag, release_url):
    root = tk.Tk()
    root.withdraw()
    should_open = messagebox.askyesno(
        "Nová verzia",
        f"K dispozícii je nová verzia {latest_tag} (aktuálna: {APP_VERSION}).\n\n"
        "Chceš otvoriť stránku na stiahnutie?",
    )
    root.destroy()
    if should_open:
        webbrowser.open(release_url)


def main():
    update = check_for_update()
    if update:
        _prompt_update(*update)
    gui.run()


if __name__ == "__main__":
    main()
