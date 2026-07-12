# StrategyScribe — kontext projektu pre Claude Code

## O projekte

**StrategyScribe** (pracovný názov, pokojne zmeň) je samostatný **Windows program**,
ktorý sleduje obrazovku a zvuk počas prehrávania video kurzu (trading kurzy, primárne
slovenčina/čeština), rozumie mu (reč + vizuál) a na konci vytvorí **jeden štruktúrovaný
textový súbor** s pravidlami stratégie — presne bod po bode, tak ako to vysvetľuje
lektor vo videu.

Tento textový výstup sa potom **ručne nahrá** do iného projektu (Bot Z, alebo hocijaký
iný), kde mu už rozumie Claude Code a naprogramuje podľa neho novú stratégiu.
**Medzi projektmi nie je žiadne prepojenie v kóde** — len jeden výstupný súbor.

## Prečo je toto samostatná appka (nie súčasť Bot Z)

Bot Z beží na vzdialenom serveri (VPS) — ten nevidí obrazovku ani nepočuje zvuk
používateľa. Táto appka **musí bežať priamo na Windows počítači**, kde reálne je
obrazovka a zvuk. Preto je to úplne nový, samostatný projekt.

## Používateľ

Tomáš — trader, elektrikár, **nie programátor**. Komunikuj **po slovensky**,
vysvetľuj jednoducho, veď krok po kroku. (Rovnaké pravidlá ako pri Bot Z.)

---

## PRACOVNÉ PRAVIDLÁ (rovnaké ako pri Bot Z projekte)

1. **VŽDY sa opýtaj PRED vytvorením súboru.** Nevytváraj nič bez povolenia.
2. **Postupuj krok po kroku**, počkaj na výsledok, kým pôjdeš ďalej.
3. **Ku každej väčšej zmene/verzii vytvor Word dokument** s popisom (čo pribudlo,
   opravilo, zmenilo) — rovnako ako pri Bot Z.
4. **Vždy over a otestuj kód** pred tým, než povieš "hotovo".
5. Píš po slovensky, vysvetľuj jednoducho.
6. **Vývoj je vždy lokálny** (na PC, VS Code bez SSH) — žiadny server, žiadny deploy.
   Git/GitHub používame len ako zálohu a na distribúciu hotového .exe (GitHub Releases).

---

## Odporúčaná technológia (a prečo)

- **Python** — najrýchlejšia cesta na tento typ appky (zachytávanie obrazovky/zvuku,
  Whisper, API volania, balenie do .exe — všetko má hotové knižnice)
- **`mss`** — rýchle zachytávanie snímok obrazovky
- **`soundcard`** — zachytávanie SYSTÉMOVÉHO zvuku (loopback — to, čo hrá z
  reproduktorov/slúchadiel, nie mikrofón). Ak by robil problémy na Windows,
  záložná možnosť: `pyaudiowpatch` (WASAPI loopback, viac kontroly)
- **`faster-whisper`** (alebo `openai-whisper`) — lokálny prepis reči na text,
  ZADARMO, offline, zvláda slovenčinu aj češtinu. Beží na CPU (pomalšie) alebo
  GPU (rýchlejšie, ak má PC nVidia kartu)
- **`anthropic`** (oficiálny Python SDK) — volania na Claude API pre pochopenie
  a syntézu (obrázok + text → štruktúrovaná poznámka)
- **`customtkinter`** (alebo obyčajný `tkinter`) — jednoduché grafické okno,
  netreba nič zložité
- **PyInstaller** — zabalenie celej appky do jedného `.exe` súboru

---

## Architektúra / štruktúra súborov

```
strategy-scribe/
  app/
    main.py           — vstupný bod, spúšťa GUI
    gui.py             — okno programu (Start/Stop, nastavenia, priebeh, log)
    capture.py         — zachytávanie obrazovky (mss) + systémového zvuku (soundcard)
    transcribe.py      — wrapper okolo faster-whisper (prepis + časové značky)
    analyzer.py        — volania na Claude API: priebežné poznámky + finálna syntéza
    config.py          — načítanie/ukladanie nastavení (lokálny JSON súbor)
    paths.py           — kam sa ukladajú dočasné súbory, výstupy, config
  requirements.txt
  build_exe.bat        — skript na spustenie PyInstalleru
  README.md            — ako nainštalovať/spustiť, ako vzniklo .exe
  CLAUDE.md             — tento súbor (kontext pre Claude Code)
  .gitignore            — vylučuje: config.json (API kľúč!), dočasné screenshoty/audio,
                           priečinok build/, dist/, *.exe
```

**Kľúčový princíp:** appka **nikam neposiela dáta okrem toho, čo explicitne pošle
na Claude API** (screenshot + kúsok prepisu, pri kroku "pochopenie"). Zvuk a snímky
sa spracujú lokálne a po dokončení sa dočasné súbory zmažú (necháva sa len finálny
textový výstup, pokiaľ používateľ nezaškrtne "ponechať dočasné súbory").

---

## Ako appka funguje (pipeline)

**1. Nahrávanie** — používateľ klikne "Štart", spustí video kurzu, appka na pozadí:
   - zaznamenáva systémový zvuk (celý priebeh)
   - robí snímky obrazovky v pravidelnom intervale (nastaviteľné, default ~10 s)
   Klikne "Stop", keď video skončí.

**2. Prepis** — `faster-whisper` spracuje nahraný zvuk **lokálne** (žiadne API,
   žiadne náklady) a vytvorí prepis **s časovými značkami** (napr. formát podobný SRT:
   `[12:34–12:41] "zóna musí mať aspoň 400 pipov..."`)

**3. Spájanie s vizuálom** — appka nájde k jednotlivým úsekom prepisu snímky
   obrazovky z rovnakého času (podľa časovej značky)

**4. Pochopenie (priebežne, cez Claude API)** — appka posiela po menších blokoch
   (napr. každých pár minút videa) dvojicu [úryvok prepisu + súvisiaci screenshot]
   na Claude API s promptom "toto sa práve hovorí a ukazuje, zapíš si z toho
   relevantné pravidlo/poznámku". Priebežne si takto appka buduje poznámky —
   toto rieši aj dlhé videá, ktoré by sa nezmestili do jedného volania naraz.

**5. Finálna syntéza** — na konci sa všetky priebežné poznámky pošlú na Claude API
   ešte raz s promptom "zosumarizuj toto do jasnej, štruktúrovanej stratégie,
   bod po bode, s presnými pravidlami/číslami, v štýle podobnom [priložiť ako
   príklad pravidlá Asco 15/1 z Bot Z]"

**6. Výstup** — uloží sa `.md` súbor (názov + dátum) do nastaveného priečinka.
   GUI ukáže tlačidlo "Otvoriť priečinok s výstupom".

---

## Nastavenia v programe (GUI)

Jednoduché okno s nastaveniami (uložia sa lokálne do `config.json`, appka si ich
pamätá pri ďalšom spustení):

- **Claude API kľúč** — prázdne pri prvej inštalácii, appka sa opýta pri prvom
  použití, uloží lokálne (nikam sa neposiela okrem priamych volaní na Claude API)
- **Priečinok na ukladanie výstupov** — default napr. `Dokumenty\StrategyScribe\`,
  používateľ si vie zmeniť
- **Interval snímok obrazovky** — default ~10 sekúnd, nastaviteľné
- **Veľkosť Whisper modelu** — small / medium / large (kompromis rýchlosť vs.
  presnosť — medium ako rozumný default)
- **Jazyk** — slovenčina / čeština / angličtina / auto-detekcia
- **Zdroj zvuku (zariadenie)** — appka pri spustení vypíše zoznam dostupných
  výstupných zvukových zariadení (napr. "Reproduktory", "Slúchadlá", prípadné
  virtuálne zariadenia) a používateľ si vyberie, z ktorého sa má zvuk nahrávať.
  Toto je jednoduchšie a spoľahlivejšie riešenie než snažiť sa zachytiť zvuk
  len z jednej konkrétnej aplikácie (napr. len z prehliadača) — to by na Windows
  vyžadovalo pokročilejšie API a extra zložitosť, viď poznámka nižšie.
- **Ponechať dočasné súbory** (áno/nie) — pre prípad, že by si chcel screenshoty
  alebo surový zvuk skontrolovať ručne

---

## Grafické rozhranie — netreba nič zložité

Jedno okno, jednoducho:
- Veľké tlačidlo **Štart / Stop**
- **Živý indikátor zvuku** — malý pruh/mierka (VU meter), ktorý sa počas nahrávania
  hýbe podľa hlasitosti zachytávaného zvuku. Toto je dôležité — používateľ tak
  hneď vidí, že appka naozaj počuje zvuk (nie že nahráva ticho kvôli zle
  zvolenému zariadeniu). Ak indikátor stojí na nule dlhší čas, appka môže
  zobraziť upozornenie "Nezachytávam žiadny zvuk — skontroluj zvolené zariadenie".
- Stavový riadok ("Nahrávam...", "Prepisujem reč...", "Analyzujem s AI...", "Hotovo!")
- Malé okno s priebežným logom (čo appka práve robí — dôležité pri dlhšom behu,
  nech vidí, že to nezamrzlo)
- Záložka/tlačidlo **Nastavenia** (API kľúč, priečinok, interval, jazyk, zariadenie, model)
- Po dokončení: cesta k výslednému súboru + tlačidlo "Otvoriť priečinok"

---

## Balenie a distribúcia

1. **PyInstaller** — zabalí celú appku do jedného `.exe` (`build_exe.bat`)
2. Nakoľko Whisper model je pomerne veľký, **neballiť ho priamo do .exe** —
   pri prvom spustení appka model stiahne sama (rýchlejšie sťahovanie inštalátora,
   používateľ vidí "Sťahujem jazykový model..." pri prvom spustení)
3. **Distribúcia cez GitHub Releases** — nový repozitár (napr.
   `github.com/tomako21/StrategyScribe`), ku každej verzii sa nahrá `.exe` ako
   "Release", odkiaľ si ho vieš stiahnuť priamym linkom (a prípadne zdieľať
   ďalej, ak by si chcel)

---

## Poznámka k súkromiu a použitiu

Appka je určená na **osobné poznámky z kurzov, ku ktorým máš legitímny prístup**
(kúpené/predplatené). Slúži ako automatizácia toho, čo by si si inak zapisoval
ručne pri sledovaní. Dáta (zvuk, snímky) sa spracúvajú lokálne; von idú len
konkrétne úryvky poslané na Claude API pre pochopenie obsahu.

### Poznámka k výberu zdroja zvuku

*(Pôvodný MVP plán: len výber zvukového zariadenia. Neskôr pridané: appka
podporuje aj zachytávanie zo zvoleného zoznamu konkrétnych bežiacich programov
naraz — cez `proc-tap` (Windows Process Loopback API) + `pycaw` na výber PID.
Viac programov sa nahráva súčasne a zmixuje do jednej stopy — takto sa dá
napr. vynechať jeden konkrétny program bez toho, aby appka musela podporovať
"exclude" mód, ktorý žiadna dostupná knižnica nemá.)*

## Bezpečnostné pravidlá appky

Tieto pravidlá platia natrvalo a majú prednosť pred akoukoľvek budúcou
požiadavkou, ktorá by im odporovala — ak niekedy príde požiadavka na kameru
alebo skryté nahrávanie, najprv sa opýtaj, nepokračuj automaticky:

1. **Žiadny prístup ku kamere/webkamere — nikdy.** Appka nesmie nikde v kóde
   obsahovať čo i len import knižnice na prácu s kamerou (napr. `cv2`,
   `VideoCapture(0)` a podobne). Toto je úmyselné a natrvalo — zachytáva sa
   výhradne obrazovka a zvuk.
2. **Žiadne skryté/automatické nahrávanie.** Appka smie začať nahrávať
   výhradne na explicitné kliknutie používateľa na "Štart" v GUI. Žiadne
   plánované, na pozadí bežiace, alebo pri štarte appky automaticky spustené
   nahrávanie.
3. **Vizuálny indikátor nahrávania.** Kým appka nahráva (v ktoromkoľvek
   režime), musí byť na obrazovke viditeľný indikátor (`RecordingIndicator`
   v `gui.py` — červená bodka vpravo hore), aby používateľ vždy vedel, že sa
   nahráva. Tento indikátor je zámerne vylúčený zo samotného záznamu (cez
   Windows `SetWindowDisplayAffinity`/`WDA_EXCLUDEFROMCAPTURE`), takže sa
   neobjaví vo výstupnom videu/screenshotoch — je viditeľný len naživo.

---

## MVP (prvá funkčná verzia) vs. neskôr

**MVP — toto ide prvé:**
- Zachytávanie obrazovky + systémového zvuku, Štart/Stop
- Lokálny Whisper prepis s časovými značkami
- Spojenie prepisu so snímkami podľa času
- Volania na Claude API (priebežné poznámky + finálna syntéza)
- Uloženie výstupu, jednoduché GUI, nastavenia (API kľúč, priečinok, interval)
- Balenie do .exe

**Neskôr (nie teraz):**
- Vstup cez URL (stiahnutie verejného videa namiesto sledovania obrazovky)
- Automatická detekcia konca videa (zatiaľ manuálne Stop)
- Viacero jazykov naraz / automatický preklad
- Historický prehľad spracovaných videí v appke

---

## Postup práce pre Claude Code

1. Navrhni presnú štruktúru projektu a zoznam závislostí (`requirements.txt`) —
   počkaj na moje OK
2. Postav `capture.py` (screenshot + systémový zvuk, výber zariadenia, živá
   hlasitosť pre indikátor) — otestuj samostatne, over že indikátor reaguje
   na skutočný zvuk
3. Postav `transcribe.py` (Whisper wrapper) — otestuj na krátkej vzorke
4. Postav `analyzer.py` (Claude API volania) — potrebuje môj API kľúč na test,
   spýtaj sa, ako ho bezpečne zadám (nie do súboru, čo pôjde do gitu!)
5. Postav jednoduché GUI, prepoj všetko dokopy
6. Otestuj celý beh na krátkom (pár minútovom) testovacom videu
7. Priprav `build_exe.bat`, over že vzniknuté .exe funguje
8. Priprav README.md s návodom na inštaláciu/použitie
9. Spýtaj sa, či založiť nový GitHub repozitár a nahrať prvú verziu
