"""Volania na Claude API: priebežné poznámky z úsekov videa a finálna syntéza —
buď obchodná stratégia (predvolené), alebo všeobecné výskumné zhrnutie videa."""

import base64
from pathlib import Path

DEFAULT_MODEL = "claude-opus-4-8"

MODEL_OPTIONS = [
    ("Claude Opus 4.8 (najkvalitnejší, drahší)", "claude-opus-4-8"),
    ("Claude Sonnet 5 (rýchlejší, lacnejší)", "claude-sonnet-5"),
    ("Claude Haiku 4.5 (najrýchlejší, najlacnejší)", "claude-haiku-4-5"),
    ("Claude Fable 5 (absolútna špička, najdrahší)", "claude-fable-5"),
]

# Cena v USD za 1 milión tokenov: (vstup, výstup). Orientačné — aktuálny cenník je na
# console.anthropic.com/settings/billing.
MODEL_PRICING = {
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-fable-5": (10.00, 50.00),
}

NOTE_SYSTEM_PROMPT = (
    "Si asistent, ktorý sleduje trading kurz (video) a robí si presné, vecné "
    "poznámky o pravidlách obchodnej stratégie, ktoré lektor práve vysvetľuje. "
    "Dostaneš úryvok prepisu reči a snímku obrazovky z rovnakého času videa. "
    "Zapíš si LEN konkrétne, overiteľné pravidlá a čísla (napr. veľkosť zóny, "
    "timeframe, podmienky vstupu/výstupu, risk management) — nič iné. "
    "Ak úryvok neobsahuje žiadne konkrétne pravidlo, odpovedz presne jedným "
    "slovom: PRESKOC. Píš po slovensky, stručne, v odrážkach."
)

SYNTHESIS_SYSTEM_PROMPT = (
    "Si asistent, ktorý zo série priebežných poznámok z trading kurzu vytvorí "
    "jeden finálny, štruktúrovaný dokument s pravidlami obchodnej stratégie — "
    "presne bod po bode, tak ako to vysvetľoval lektor vo videu. Použi jasné "
    "nadpisy (napr. Podmienky vstupu, Risk management, Timeframe, Výstup z "
    "pozície) a pod nimi konkrétne, číselne podložené pravidlá. Vynechaj "
    "opakovania a nepodstatné detaily. Píš po slovensky."
)

MERGE_SYSTEM_PROMPT = (
    "Si asistent, ktorý spravuje jeden priebežne rastúci dokument s pravidlami "
    "obchodnej stratégie. Dostaneš (1) EXISTUJÚCI dokument so stratégiou a (2) "
    "nové poznámky z ďalšieho videa o tej istej stratégii (napr. vysvetlenie "
    "ďalšieho nástroja/konceptu, spresnenie pravidla). Tvoja úloha: zluč to do "
    "jedného aktualizovaného, súdržného dokumentu — nové pravidlá pridaj na "
    "vhodné miesto pod správny nadpis, existujúce pravidlá uprav/spresni ak ich "
    "nové video dopĺňa alebo koriguje, nič nezduplikuj, zachovaj prehľadnú "
    "štruktúru (rovnaký štýl nadpisov ako v existujúcom dokumente). Vráť CELÝ "
    "výsledný dokument (nie len zmeny). Píš po slovensky."
)

CHANGELOG_SYSTEM_PROMPT = (
    "Porovnaj PôVODNÚ a NOVÚ verziu dokumentu so stratégiou. Stručne v bodoch "
    "zhrň, čo presne sa touto aktualizáciou zmenilo — čo pribudlo, čo sa "
    "spresnilo/upravilo. Ak je 'pôvodná verzia' prázdna, ide o úplne prvé "
    "vytvorenie dokumentu — zhrň, čo v ňom je. Píš stručne, po slovensky, "
    "v odrážkach, bez nadpisu."
)

RESEARCH_NOTE_SYSTEM_PROMPT = (
    "Si asistent, ktorý sleduje video (akéhokoľvek druhu — prednáška, tutoriál, "
    "rozhovor, dokument, prezentácia...) a robí si stručné priebežné poznámky. "
    "Dostaneš úryvok prepisu reči a snímky obrazovky z rovnakého času videa. "
    "Zapíš si stručne v bodoch, o čom je tento úsek — kľúčové témy, mená, fakty, "
    "čísla, závery. Ak úsek neobsahuje nič podstatné (napr. ticho, hudba, úvod "
    "bez obsahu), odpovedz presne jedným slovom: PRESKOC. Píš po slovensky, "
    "stručne, v odrážkach."
)

RESEARCH_SYNTHESIS_SYSTEM_PROMPT = (
    "Si asistent, ktorý zo série priebežných poznámok z videa vytvorí jedno "
    "súvislé zhrnutie — čo presne video obsahuje a o čom je. Použi jasné nadpisy "
    "(napr. O čom video je, Hlavné témy, Kľúčové body, Zhrnutie) a pod nimi "
    "stručný, výstižný text. Vynechaj opakovania a nepodstatné detaily. Cieľom "
    "je, aby si niekto prečítaním tohto dokumentu vedel urobiť obraz o celom "
    "videu bez toho, aby ho musel pozerať. Píš po slovensky."
)


def _encode_image(path):
    data = Path(path).read_bytes()
    return base64.standard_b64encode(data).decode("utf-8")


def _response_text(message):
    return "".join(block.text for block in message.content if block.type == "text").strip()


def _usage_dict(message):
    return {
        "input_tokens": message.usage.input_tokens,
        "output_tokens": message.usage.output_tokens,
    }


def estimate_cost_usd(usage, model):
    """Odhadovaná cena (USD) za jedno volanie API podľa počtu tokenov a modelu."""
    input_price, output_price = MODEL_PRICING.get(model, MODEL_PRICING[DEFAULT_MODEL])
    return (
        usage["input_tokens"] / 1_000_000 * input_price
        + usage["output_tokens"] / 1_000_000 * output_price
    )


def analyze_segment(client, transcript_excerpt, screenshot_paths, model=DEFAULT_MODEL,
                     system_prompt=None):
    """Pošle úryvok prepisu + jeden alebo viac screenshotov z toho istého úseku na
    Claude. Vráti (poznámka alebo None, usage) — None keď úsek neobsahuje nič
    podstatné (podľa zvoleného system_prompt — predvolené: pravidlá stratégie)."""
    if system_prompt is None:
        system_prompt = NOTE_SYSTEM_PROMPT
    if isinstance(screenshot_paths, (str, Path)):
        screenshot_paths = [screenshot_paths]
    image_blocks = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": _encode_image(path),
            },
        }
        for path in screenshot_paths
    ]
    response = client.messages.create(
        model=model,
        # 4096 namiesto tesnejšieho limitu — modely s "vždy zapnutým" premýšľaním
        # (napr. Claude Fable 5) si časť max_tokens berú na interné uvažovanie.
        max_tokens=4096,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": [*image_blocks, {"type": "text", "text": f"Prepis:\n{transcript_excerpt}"}],
        }],
    )
    text = _response_text(response)
    note = None if text.upper().startswith("PRESKOC") else text
    return note, _usage_dict(response)


def synthesize_notes(client, notes, model=DEFAULT_MODEL, system_prompt=None):
    """Spojí všetky priebežné poznámky do jedného finálneho dokumentu (stratégia,
    alebo iný výstup podľa zvoleného system_prompt). Vráti (text, usage)."""
    if system_prompt is None:
        system_prompt = SYNTHESIS_SYSTEM_PROMPT
    joined = "\n\n".join(notes)
    with client.messages.stream(
        model=model,
        max_tokens=16000,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": f"Priebežné poznámky z celého videa:\n\n{joined}",
        }],
    ) as stream:
        message = stream.get_final_message()
    return _response_text(message), _usage_dict(message)


def merge_notes(client, existing_document, notes, model=DEFAULT_MODEL):
    """Zlúči nové priebežné poznámky do existujúceho dokumentu so stratégiou.
    Vráti (aktualizovaný celý dokument, usage)."""
    joined = "\n\n".join(notes)
    with client.messages.stream(
        model=model,
        max_tokens=16000,
        system=MERGE_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": (
                f"EXISTUJÚCI dokument:\n\n{existing_document}\n\n"
                f"---\n\nNOVÉ poznámky z ďalšieho videa:\n\n{joined}"
            ),
        }],
    ) as stream:
        message = stream.get_final_message()
    return _response_text(message), _usage_dict(message)


def summarize_changes(client, old_text, new_text, model=DEFAULT_MODEL):
    """Zhrnie, čo sa medzi dvomi verziami dokumentu zmenilo. Vráti (text, usage)."""
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=CHANGELOG_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"PÔVODNÁ verzia:\n\n{old_text or '(žiadna — ide o prvé vytvorenie)'}\n\n---\n\nNOVÁ verzia:\n\n{new_text}",
        }],
    )
    return _response_text(response), _usage_dict(response)
