"""Zachytávanie obrazovky (mss) a zvuku počas nahrávania — buď celého zvukového
zariadenia (soundcard, WASAPI loopback), alebo len jedného konkrétneho bežiaceho
programu/prehliadača (proc-tap, WASAPI Process Loopback).

BEZPEČNOSTNÁ POZNÁMKA: Tento súbor (a celý projekt) zámerne NEOBSAHUJE žiadny
kód na prístup ku kamere/webkamere — žiadny import cv2, žiadne VideoCapture(0),
nič podobné. Zachytáva sa výhradne obrazovka a zvuk, a to LEN keď používateľ
ručne klikne na Štart v GUI (žiadne skryté/plánované/automatické spúšťanie).
Ak niekedy pribudne kód spomínajúci kameru, je to odchýlka od zámeru projektu."""

import shutil
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path

import imageio_ffmpeg
import mss
import numpy as np
# pycaw (comtypes) musí byť importované PRED soundcard — inak soundcard nastaví
# COM vlákno do iného režimu a comtypes potom padne s "Cannot change thread mode".
from pycaw.pycaw import AudioUtilities
import proctap
import soundcard as sc


def list_monitors():
    """Vráti zoznam (index, popis) pre každý fyzicky pripojený monitor
    (mss index 0 = všetky monitory spolu, preto sa tu vynecháva)."""
    with mss.mss() as sct:
        return [
            (i, f"Monitor {i} ({mon['width']}×{mon['height']})")
            for i, mon in enumerate(sct.monitors[1:], start=1)
        ]


def list_output_devices():
    """Vráti zoznam mien dostupných výstupných zvukových zariadení."""
    return [speaker.name for speaker in sc.all_speakers()]


def default_output_device():
    """Vráti meno predvoleného výstupného zvukového zariadenia."""
    return sc.default_speaker().name


def list_audio_processes():
    """Vráti zoznam mien programov (napr. 'opera.exe'), ktoré majú vo Windows
    aktuálne otvorenú zvukovú reláciu (aj keď práve nehrajú)."""
    names = []
    for session in AudioUtilities.GetAllSessions():
        if session.Process:
            name = session.Process.name()
            if name not in names:
                names.append(name)
    return names


def _resolve_process_id(process_name):
    """Nájde PID bežiaceho programu podľa mena. Uprednostní reláciu, ktorá
    práve aktívne prehráva zvuk (viac tabov v prehliadači = viac PID)."""
    matches = [
        s for s in AudioUtilities.GetAllSessions()
        if s.Process and s.Process.name().lower() == process_name.lower()
    ]
    if not matches:
        raise RuntimeError(
            f"Program '{process_name}' momentálne nebeží alebo neprehráva zvuk. "
            "Skontroluj, že je spustený a otvorený."
        )
    active = [s for s in matches if s.State == 1]
    chosen = active[0] if active else matches[0]
    return chosen.Process.pid


def _get_loopback_microphone(device_name=None):
    if device_name is None:
        device_name = default_output_device()
    return sc.get_microphone(id=device_name, include_loopback=True)


def _save_wav(path, audio, samplerate, channels):
    audio_int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        wf.writeframes(audio_int16.tobytes())


def convert_wav_to_mp3(wav_path, mp3_path, bitrate="192k"):
    """Jednorazová (nie priebežná) konverzia WAV na MP3 cez ffmpeg."""
    cmd = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-y",
        "-i", str(wav_path),
        "-codec:a", "libmp3lame", "-b:a", bitrate,
        str(mp3_path),
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def mux_video_audio(video_path, audio_path, output_path):
    """Skombinuje samostatne nahraté video (bez zvuku) a zvukovú stopu do
    jedného MP4 súboru."""
    cmd = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-y",
        "-i", str(video_path), "-i", str(audio_path),
        "-c:v", "copy", "-c:a", "aac", "-shortest",
        str(output_path),
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


class ScreenshotCapture:
    """Zachytáva snímky obrazovky v pravidelnom intervale, súbory pomenované podľa
    uplynulého času od štartu (v sekundách), napr. frame_00042.30.png.

    Zdroj obrazu (v poradí priority):
    - region: presný obdĺžnik {"left","top","width","height"} — ak je zadaný
    - inak monitor_index: 0 = všetky monitory spolu (default), 1..N = konkrétny monitor
    """

    def __init__(self, output_dir, interval_seconds=10, monitor_index=0, region=None):
        self.output_dir = Path(output_dir)
        self.interval_seconds = interval_seconds
        self.monitor_index = monitor_index
        self.region = region
        self._stop_event = threading.Event()
        self._thread = None
        self._start_time = None

    def start(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._stop_event.clear()
        self._start_time = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self):
        with mss.mss() as sct:
            target = self.region if self.region else sct.monitors[self.monitor_index]
            while not self._stop_event.is_set():
                elapsed = time.monotonic() - self._start_time
                filename = self.output_dir / f"frame_{elapsed:08.2f}.png"
                img = sct.grab(target)
                mss.tools.to_png(img.rgb, img.size, output=str(filename))
                self._stop_event.wait(self.interval_seconds)


class VideoCapture:
    """Nahráva PLYNULÉ video obrazovky (nie periodické screenshoty) priamo do MP4
    súboru — snímky sa posielajú cez rúru (pipe) do ffmpeg (imageio-ffmpeg), ktorý
    ich priebežne kóduje (H.264). Rovnaké možnosti zdroja obrazu ako ScreenshotCapture."""

    QUALITY_CRF = {"low": 30, "medium": 23, "high": 18}

    def __init__(self, output_path, fps=10, quality="medium", monitor_index=0, region=None):
        self.output_path = Path(output_path)
        self.fps = fps
        self.quality = quality
        self.monitor_index = monitor_index
        self.region = region
        self._stop_event = threading.Event()
        self._thread = None
        self._process = None

    def start(self):
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=20)

    def _run(self):
        with mss.mss() as sct:
            target = self.region if self.region else sct.monitors[self.monitor_index]
            first = sct.grab(target)
            width = first.width - (first.width % 2)
            height = first.height - (first.height % 2)

            crf = str(self.QUALITY_CRF.get(self.quality, 23))
            cmd = [
                imageio_ffmpeg.get_ffmpeg_exe(), "-y",
                "-f", "rawvideo", "-pix_fmt", "bgra",
                "-s", f"{width}x{height}", "-r", str(self.fps),
                "-i", "-",
                "-an",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", crf,
                "-pix_fmt", "yuv420p",
                str(self.output_path),
            ]
            self._process = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

            frame_interval = 1.0 / self.fps
            next_frame_time = time.monotonic()
            while not self._stop_event.is_set():
                img = sct.grab(target)
                frame_bytes = self._to_bgra_bytes(img, width, height)
                try:
                    self._process.stdin.write(frame_bytes)
                except (BrokenPipeError, OSError):
                    break
                next_frame_time += frame_interval
                sleep_time = next_frame_time - time.monotonic()
                if sleep_time > 0:
                    self._stop_event.wait(sleep_time)
                else:
                    next_frame_time = time.monotonic()

            try:
                self._process.stdin.close()
            except OSError:
                pass
            self._process.wait(timeout=20)

    @staticmethod
    def _to_bgra_bytes(img, width, height):
        if img.width == width and img.height == height:
            return img.raw
        arr = np.frombuffer(img.raw, dtype=np.uint8).reshape(img.height, img.width, 4)
        return arr[:height, :width].tobytes()


class AudioCapture:
    """Nahráva systémový zvuk (loopback) daného výstupného zariadenia do WAV súboru
    a priebežne hlási aktuálnu hlasitosť (0..1) cez on_level callback pre VU meter."""

    def __init__(self, output_path, device_name=None, samplerate=48000,
                 channels=2, on_level=None):
        self.output_path = Path(output_path)
        self.device_name = device_name
        self.samplerate = samplerate
        self.channels = channels
        self.on_level = on_level
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _run(self):
        mic = _get_loopback_microphone(self.device_name)
        blocksize = 1024
        frames = []
        with mic.recorder(samplerate=self.samplerate, channels=self.channels) as recorder:
            while not self._stop_event.is_set():
                data = recorder.record(numframes=blocksize)
                frames.append(data)
                if self.on_level:
                    rms = float(np.sqrt(np.mean(np.square(data))))
                    self.on_level(min(rms * 4.0, 1.0))

        if frames:
            audio = np.concatenate(frames, axis=0)
            _save_wav(self.output_path, audio, self.samplerate, self.channels)


class MultiProcessAudioCapture:
    """Nahráva zvuk súčasne z VIACERÝCH konkrétnych bežiacich programov (napr.
    prehliadač + Zoom, ale bez Discordu) — každý samostatne cez ProcessAudioCapture,
    na konci sa stopy zmixujú do jednej WAV. Rovnaké rozhranie ako AudioCapture."""

    def __init__(self, output_path, process_names, samplerate=48000,
                 channels=2, on_level=None):
        self.output_path = Path(output_path)
        self.process_names = process_names
        self.samplerate = samplerate
        self.channels = channels
        self.on_level = on_level
        self._captures = []
        self._temp_dir = None

    def start(self):
        if not self.process_names:
            raise RuntimeError("Nie je vybraný žiadny program na nahrávanie zvuku.")
        self._temp_dir = Path(tempfile.mkdtemp(prefix="strategyscribe_mix_"))
        self._captures = []
        for i, name in enumerate(self.process_names):
            cap = ProcessAudioCapture(
                self._temp_dir / f"track_{i}.wav", process_name=name,
                samplerate=self.samplerate, channels=self.channels, on_level=self.on_level,
            )
            try:
                cap.start()
            except RuntimeError:
                for started in self._captures:
                    started.stop()
                raise
            self._captures.append(cap)

    def stop(self):
        for cap in self._captures:
            cap.stop()
        self._mix_and_save()
        if self._temp_dir:
            shutil.rmtree(self._temp_dir, ignore_errors=True)

    def _mix_and_save(self):
        tracks = []
        for cap in self._captures:
            if not cap.output_path.exists():
                continue
            with wave.open(str(cap.output_path), "rb") as wf:
                raw = wf.readframes(wf.getnframes())
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0
            tracks.append(audio.reshape(-1, self.channels))

        if not tracks:
            return
        max_len = max(t.shape[0] for t in tracks)
        mixed = np.zeros((max_len, self.channels), dtype=np.float32)
        for t in tracks:
            mixed[: t.shape[0]] += t
        mixed = np.clip(mixed, -1.0, 1.0)
        _save_wav(self.output_path, mixed, self.samplerate, self.channels)


class ProcessAudioCapture:
    """Nahráva zvuk len z JEDNÉHO konkrétneho bežiaceho programu (napr. len
    prehliadača), nie celého systému — cez Windows Process Loopback API
    (knižnica proc-tap). Rovnaké verejné rozhranie ako AudioCapture."""

    def __init__(self, output_path, process_name, samplerate=48000,
                 channels=2, on_level=None):
        self.output_path = Path(output_path)
        self.process_name = process_name
        self.samplerate = samplerate
        self.channels = channels
        self.on_level = on_level
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        """Vyhľadá cieľový proces HNEĎ (synchrónne) — ak nebeží, vyhodí RuntimeError
        skôr, než appka začne nahrávať prázdno."""
        pid = _resolve_process_id(self.process_name)
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, args=(pid,), daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _run(self, pid):
        frames = []

        def on_data(pcm_bytes, frame_count):
            audio = np.frombuffer(pcm_bytes, dtype=np.float32).reshape(-1, self.channels)
            frames.append(audio.copy())
            if self.on_level:
                rms = float(np.sqrt(np.mean(np.square(audio))))
                self.on_level(min(rms * 4.0, 1.0))

        tap = proctap.ProcessAudioCapture(pid, on_data=on_data)
        tap.start()
        self._stop_event.wait()
        tap.stop()
        tap.close()

        if frames:
            audio = np.concatenate(frames, axis=0)
            _save_wav(self.output_path, audio, self.samplerate, self.channels)
