"""Grabación desde el micrófono con detección de voz por energía y transcripción local con faster-whisper."""
import contextlib
import threading
import time

import numpy as np
import sounddevice as sd

from .config import resolve

SR = 16000
PROMPT = ("Jarvis, abre Spotify. Pon música de Bad Bunny. Sube el volumen. Abre Google Chrome. "
          "¿Qué hora es? ¿Cómo está el sistema?")


def listar_microfonos() -> list[str]:
    nombres = []
    for d in sd.query_devices():
        if d["max_input_channels"] > 0 and sd.query_hostapis(d["hostapi"])["name"] == "MME":
            if d["name"] not in nombres:
                nombres.append(d["name"])
    return nombres


def _buscar_dispositivo(nombre: str):
    if not nombre:
        return None
    nombre = nombre.lower()
    # MME primero: hace la conversión de frecuencia de muestreo por nosotros y es el más compatible.
    for api in ("MME", "Windows DirectSound", "Windows WASAPI"):
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0 and nombre in d["name"].lower() \
                    and sd.query_hostapis(d["hostapi"])["name"] == api:
                return i
    return None


class Listener:
    def __init__(self, cfg: dict, on_level=None):
        self.cfg = cfg
        self.on_level = on_level or (lambda v: None)
        self.model = None
        self.ready = threading.Event()
        self.cancel = threading.Event()
        self.device = _buscar_dispositivo(cfg.get("microfono", ""))
        threading.Thread(target=self._load, daemon=True).start()

    def set_microfono(self, nombre: str):
        self.device = _buscar_dispositivo(nombre)

    def _load(self):
        from faster_whisper import WhisperModel
        dispositivo = self.cfg.get("whisper_dispositivo", "cpu")
        self.model = WhisperModel(self.cfg.get("whisper_modelo", "small"), device=dispositivo,
                                  compute_type="int8" if dispositivo == "cpu" else "float16",
                                  download_root=str(resolve("models/whisper")))
        # Primera pasada en vacío para que la primera orden real no sea lenta.
        list(self.model.transcribe(np.zeros(SR, dtype=np.float32), language="es")[0])
        self.ready.set()
        print("[stt] Whisper listo")

    def record(self, start_timeout: float | None = 7.0, max_seconds: float = 15.0,
               silence_s: float = 0.9, should_pause=None, stream=None) -> np.ndarray | None:
        """Graba una frase. Devuelve audio float32 a 16 kHz o None si no se habló.

        stream: un InputStream ya abierto (16 kHz, float32) del que seguir leyendo; lo usa la palabra de
        activación para no perder lo que se dice justo después de «Jarvis».
        """
        self.cancel.clear()
        blocks: list[np.ndarray] = []
        pre: list[np.ndarray] = []          # audio previo al inicio de la voz (no cortar la primera sílaba)
        noise = 0.004
        speaking = False
        silence = 0.0
        t0 = time.time()
        block_s = 0.03
        abrir = contextlib.nullcontext(stream) if stream is not None else             sd.InputStream(samplerate=SR, channels=1, dtype="float32", device=self.device, blocksize=int(SR * block_s))
        with abrir as stream:
            while not self.cancel.is_set():
                data, _ = stream.read(int(SR * block_s))
                chunk = data[:, 0].copy()
                if should_pause and should_pause():
                    # Mientras el asistente habla no escuchamos (evita que se oiga a sí mismo).
                    speaking, blocks, pre, silence = False, [], [], 0.0
                    t0 = time.time()
                    self.on_level(0.0)
                    continue
                rms = float(np.sqrt(np.mean(chunk ** 2)))
                self.on_level(min(1.0, rms * 12))
                umbral = max(noise * 3.2, 0.012)
                if not speaking:
                    noise = 0.95 * noise + 0.05 * rms if rms < umbral else noise
                    pre = (pre + [chunk])[-12:]
                    if rms > umbral:
                        speaking, blocks, silence = True, pre[:], 0.0
                        t_voice = time.time()
                    elif start_timeout and time.time() - t0 > start_timeout:
                        break
                else:
                    blocks.append(chunk)
                    silence = silence + block_s if rms < umbral * 0.8 else 0.0
                    if silence >= silence_s or time.time() - t_voice > max_seconds:
                        break
        self.on_level(0.0)
        if not speaking or self.cancel.is_set():
            return None
        audio = np.concatenate(blocks)
        if len(audio) < SR * 0.35:
            return None
        return audio

    def transcribe(self, audio: np.ndarray, prompt: str = PROMPT) -> str:
        self.ready.wait()
        segments, _ = self.model.transcribe(audio, language="es", beam_size=1, vad_filter=True,
                                            initial_prompt=prompt, condition_on_previous_text=False)
        texto = " ".join(s.text.strip() for s in segments).strip()
        # Whisper a veces "alucina" estas frases con ruido o silencio.
        basura = {"gracias.", "gracias por ver el video.", "subtítulos realizados por la comunidad de amara.org",
                  "¡suscríbete!", "...", "."}
        return "" if texto.lower() in basura else texto
