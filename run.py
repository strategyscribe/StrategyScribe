"""Vstupný skript pre PyInstaller (mimo balíčka app/, kvôli relatívnym importom)."""

from app.main import main

if __name__ == "__main__":
    main()
