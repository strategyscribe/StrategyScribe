"""Wrapper okolo faster-whisper na lokálny prepis reči s časovými značkami."""

from dataclasses import dataclass

from faster_whisper import WhisperModel


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str


class Transcriber:
    """device="auto" skúsi najprv GPU (cuda), ak zlyhá (chýbajúce CUDA/cuDNN
    knižnice sa prejavia až pri reálnom výpočte, nie pri vytvorení modelu),
    automaticky sa prepne na CPU a prepis zopakuje.

    on_log (voliteľné) sa zavolá s textom o tom, na čom appka reálne beží —
    bez toho je prípadný pád na CPU úplne neviditeľný a prepis môže trvať
    rádovo dlhšie bez akéhokoľvek vysvetlenia v logu appky."""

    def __init__(self, model_size="medium", device="auto", language=None, on_log=None):
        self.model_size = model_size
        self.language = language
        self._on_log = on_log or (lambda text: None)
        self._device = "cuda" if device == "auto" else device
        self._load(self._device)

    def _load(self, device):
        compute_type = "float16" if device == "cuda" else "int8"
        # cpu_threads=0 necháva CTranslate2 použiť všetky dostupné jadrá
        # procesora — bez tohto niekedy beží len na časti jadier a prepis na
        # CPU je zbytočne pomalší.
        kwargs = {"cpu_threads": 0} if device == "cpu" else {}
        self.model = WhisperModel(self.model_size, device=device, compute_type=compute_type, **kwargs)
        self._device = device
        self._on_log(
            "Prepis beží na grafickej karte (GPU)." if device == "cuda"
            else "Prepis beží na procesore (CPU) — pri modeli 'medium'/'large' to môže "
                 "trvať výrazne dlhšie než na GPU."
        )

    def transcribe(self, audio_path):
        """Vráti (zoznam TranscriptSegment, detegovaný jazyk)."""
        try:
            return self._transcribe_once(audio_path)
        except RuntimeError as exc:
            if self._device != "cpu":
                self._on_log(f"Grafická karta zlyhala ({exc}) — prepínam na procesor...")
                self._load("cpu")
                return self._transcribe_once(audio_path)
            raise

    def _transcribe_once(self, audio_path):
        segments, info = self.model.transcribe(
            str(audio_path),
            language=self.language,
            vad_filter=True,
        )
        result = [
            TranscriptSegment(start=seg.start, end=seg.end, text=seg.text.strip())
            for seg in segments
        ]
        return result, info.language


def format_timestamp(seconds):
    m, s = divmod(seconds, 60)
    h, m = divmod(int(m), 60)
    return f"{h:02d}:{int(m):02d}:{s:05.2f}"


def segments_to_text(segments):
    lines = [
        f"[{format_timestamp(seg.start)}–{format_timestamp(seg.end)}] {seg.text}"
        for seg in segments
    ]
    return "\n".join(lines)
