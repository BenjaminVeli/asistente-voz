"""Síntesis de voz local: Piper (voz neuronal) con respaldo en las voces SAPI de Windows (pyttsx3).

Dos hilos: uno sintetiza frases y otro las reproduce, así la siguiente frase se
prepara mientras suena la actual.
"""
import queue
import re
import threading
import time

import numpy as np
import sounddevice as sd

from .config import resolve

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")


def limpiar_para_voz(texto: str) -> str:
    texto = _EMOJI.sub("", texto)
    texto = re.sub(r"```.*?```", " ", texto, flags=re.S)
    texto = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", texto)
    texto = re.sub(r"https?://\S+", "", texto)
    texto = re.sub(r"[*_#`>|~]", "", texto)
    return re.sub(r"\s+", " ", texto).strip()


class TTS:
    def __init__(self, cfg: dict, on_speaking=None, on_level=None):
        self.cfg = cfg
        self.on_speaking = on_speaking or (lambda b: None)
        self.on_level = on_level or (lambda v: None)
        self.enabled = cfg.get("voz_activada", True)
        self._texts: queue.Queue = queue.Queue()
        self._audio: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._gen = 0
        self._pending = 0
        self._lock = threading.Lock()
        self._piper = None
        self._syn_config = None
        try:
            from piper import PiperVoice
            self._piper = PiperVoice.load(str(resolve(cfg["voz_piper"])))
            try:
                from piper import SynthesisConfig
                self._syn_config = SynthesisConfig(length_scale=1.0 / float(cfg.get("velocidad_voz", 1.0)))
            except Exception:
                pass
            print("[tts] Voz Piper cargada")
        except Exception as e:
            print(f"[tts] Piper no disponible ({e}); uso voces de Windows")
        threading.Thread(target=self._synth_worker, daemon=True).start()
        threading.Thread(target=self._play_worker, daemon=True).start()

    @property
    def busy(self) -> bool:
        return self._pending > 0

    def say(self, texto: str):
        texto = limpiar_para_voz(texto)
        if not texto or not self.enabled:
            return
        self._stop.clear()
        gen = self._gen
        with self._lock:
            self._pending += 1
            if self._pending == 1:
                self.on_speaking(True)
        self._texts.put((gen, texto))

    def stop(self):
        self._gen += 1
        self._stop.set()
        for q in (self._texts, self._audio):
            while True:
                try:
                    q.get_nowait()
                    self._done()
                except queue.Empty:
                    break
        sd.stop()

    def wait(self, timeout: float = 60):
        t0 = time.time()
        while self.busy and time.time() - t0 < timeout:
            time.sleep(0.05)

    # ------------------------------------------------------------------
    def _done(self):
        with self._lock:
            self._pending = max(0, self._pending - 1)
            if self._pending == 0:
                self.on_level(0.0)
                self.on_speaking(False)

    def _synth_worker(self):
        while True:
            gen, texto = self._texts.get()
            if gen != self._gen:
                self._done()
                continue
            try:
                if self._piper:
                    kwargs = {"syn_config": self._syn_config} if self._syn_config else {}
                    partes = [c.audio_float_array for c in self._piper.synthesize(texto, **kwargs)]
                    audio = np.concatenate(partes).astype(np.float32) if partes else None
                    self._audio.put((gen, audio, self._piper.config.sample_rate, None))
                else:
                    self._audio.put((gen, None, 0, texto))
            except Exception as e:
                print(f"[tts] Error sintetizando: {e}")
                self._done()

    def _play_worker(self):
        sapi = None
        while True:
            gen, audio, sr, texto = self._audio.get()
            try:
                if gen != self._gen:
                    continue
                if audio is not None:
                    self._play(audio, sr)
                elif texto:
                    if sapi is None:
                        sapi = self._init_sapi()
                    sapi.say(texto)
                    sapi.runAndWait()
            except Exception as e:
                print(f"[tts] Error reproduciendo: {e}")
            finally:
                self._done()

    def _play(self, audio: np.ndarray, sr: int):
        sd.play(audio, sr)
        t0 = time.time()
        dur = len(audio) / sr
        win = int(sr * 0.05)
        while time.time() - t0 < dur + 0.05:
            if self._stop.is_set():
                sd.stop()
                return
            i = int((time.time() - t0) * sr)
            seg = audio[i:i + win]
            if len(seg):
                self.on_level(float(min(1.0, np.sqrt(np.mean(seg ** 2)) * 5)))
            time.sleep(0.05)
        sd.wait()

    @staticmethod
    def _init_sapi():
        import pyttsx3
        eng = pyttsx3.init()
        for v in eng.getProperty("voices"):
            if "spanish" in v.name.lower() or "es-" in v.id.lower():
                eng.setProperty("voice", v.id)
                break
        eng.setProperty("rate", 185)
        return eng
