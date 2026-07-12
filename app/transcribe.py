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
    automaticky sa prepne na CPU a prepis zopakuje."""

    def __init__(self, model_size="medium", device="auto", language=None):
        self.model_size = model_size
        self.language = language
        self._device = "cuda" if device == "auto" else device
        self._load(self._device)

    def _load(self, device):
        compute_type = "float16" if device == "cuda" else "int8"
        self.model = WhisperModel(self.model_size, device=device, compute_type=compute_type)
        self._device = device

    def transcribe(self, audio_path):
        """Vráti (zoznam TranscriptSegment, detegovaný jazyk)."""
        try:
            return self._transcribe_once(audio_path)
        except RuntimeError:
            if self._device != "cpu":
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
