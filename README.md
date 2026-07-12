# StrategyScribe

Windows appka, ktorá sleduje obrazovku a zvuk počas prehrávania video kurzu,
rozumie mu (reč + vizuál cez Claude AI) a na konci vytvorí štruktúrovaný
textový súbor s pravidlami — napríklad obchodnú stratégiu bod po bode, alebo
všeobecné zhrnutie akéhokoľvek videa.

Beží úplne lokálne na tvojom počítači. Von sa posielajú len konkrétne úryvky
(prepis reči + screenshoty) na Claude API kvôli pochopeniu obsahu — nič iné.

## Inštalácia (pre bežného používateľa)

1. Stiahni najnovší `StrategyScribe.exe` zo sekcie [Releases](../../releases)
2. Spusti ho — nič ďalšie sa neinštaluje, je to jeden súbor
3. Pri prvom spustení choď do **Nastavenia** a vlož svoj **Claude API kľúč**
   (získaš ho na [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys))
4. Over si zdroj zvuku a obrazu v Nastaveniach — appka ich pri spustení navrhne,
   ale over si, že sedia (najmä zvukové zariadenie/program)
5. Klikni **Štart**, spusti video kurzu, počkaj kým appka nahráva, klikni **Stop**
6. Appka prepíše reč, pošle to na AI analýzu a uloží výsledok — cesta k nemu sa
   zobrazí po dokončení (tlačidlo "Otvoriť priečinok s výstupom")

Whisper model (na prepis reči) sa pri prvom použití automaticky stiahne — to
môže chvíľu trvať, appka to ukáže v logu.

## Čo appka vie

**4 režimy** (prepínač v hlavnom okne):

| Režim | Čo robí | Výstup |
|---|---|---|
| Nahrávanie + AI analýza stratégie | Sleduje trading kurz, zapisuje si konkrétne pravidlá stratégie | `.md` (alebo `.ssenc` ak je zapnuté šifrovanie) |
| Výskum | Funguje na akomkoľvek videu — AI zistí o čom je a spraví zhrnutie | Word `.docx` |
| Len nahrávanie videa | Čisté nahrávanie obrazovky bez AI, voliteľne aj so zvukom | `.mp4` |
| Len nahrávanie zvuku | Čisté nahrávanie zvuku bez AI | `.mp3` |

**Zdroj zvuku:** vyber zvukové zariadenie (nahráva všetko, čo cez neho hrá),
alebo si zaškrtni jeden či viac konkrétnych bežiacich programov naraz (appka ich
zmixuje) — takto vieš nahrávať napr. len z prehliadača bez systémových zvukov.

**Zdroj obrazu:** celá obrazovka, konkrétny monitor, alebo vlastná oblasť,
ktorú vyznačíš myšou.

**AI model:** vyberateľný (Opus 4.8 / Sonnet 5 / Haiku 4.5 / Fable 5) — rôzny
pomer kvalita/rýchlosť/cena.

**Limit ceny relácie:** nastav si maximálnu cenu (USD) — appka priebežne počas
spracovania sleduje odhadovanú cenu a keď dosiahne limit, prestane analyzovať
ďalšie bloky (dokončí výstup z toho, čo už má).

## Bezpečnosť a súkromie

- **Appka nemá a nikdy nebude mať prístup ku kamere** — v kóde nie je ani
  jeden riadok týkajúci sa webkamery, zachytáva sa výhradne obrazovka a zvuk
- **Žiadne skryté nahrávanie** — appka začne nahrávať výhradne po kliknutí na
  Štart, žiadne plánované/automatické spúšťanie
- **Vizuálny indikátor** — počas nahrávania svieti červená bodka vpravo hore;
  je vylúčená z akéhokoľvek záznamu (nezobrazí sa vo výstupnom videu/screenshotoch),
  je viditeľná len naživo, aby si vždy vedel že appka nahráva
- **Voliteľné šifrovanie výstupu** — verejným RSA kľúčom (nie heslom), takže
  zašifrovaný výstup nevie prečítať ani ten, kto ho vytvoril — len ten, kto má
  zodpovedajúci súkromný kľúč
- **Zostatok API kreditu** appka priamo nezobrazuje (Claude API to neposkytuje)
  — v Nastaveniach je tlačidlo, čo ťa rovno prehodí na stránku s fakturáciou

## Vývoj / spustenie zo zdrojového kódu

```bash
pip install -r requirements.txt
python -m app.main
```

## Balenie do .exe

```bash
build_exe.bat
```

Vytvorí `dist/StrategyScribe.exe`. Whisper model sa do .exe nebalí — sťahuje sa
pri prvom použití appky, nie pri inštalácii.

## Štruktúra projektu

```
app/
  main.py         — vstupný bod, kontrola aktualizácie, spúšťa GUI
  gui.py          — okno appky (Start/Stop, Nastavenia, indikátor nahrávania)
  capture.py      — zachytávanie obrazovky, zvuku a videa
  transcribe.py   — lokálny prepis reči (faster-whisper)
  analyzer.py     — volania na Claude API (poznámky + finálna syntéza)
  docx_export.py  — export do Word dokumentu (Výskum režim)
  security.py     — voliteľné šifrovanie výstupu (RSA)
  config.py       — ukladanie nastavení (config.json, mimo git)
  paths.py        — cesty k výstupom/dočasným súborom
run.py            — vstupný skript pre PyInstaller
build_exe.bat     — zabalenie do .exe
```
