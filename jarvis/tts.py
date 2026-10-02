"""Síntesis de voz local: Piper (voz neuronal) con respaldo en las voces SAPI de Windows.

Dos hilos: uno sintetiza frases y otro las reproduce, así la siguiente frase se
prepara mientras suena la actual.
"""
import json
import queue
import re
import threading
import time

import numpy as np
import sounddevice as sd

from .config import BASE_DIR, resolve

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")


def limpiar_para_voz(texto: str) -> str:
    texto = _EMOJI.sub("", texto)
    texto = re.sub(r"```.*?```", " ", texto, flags=re.S)
    texto = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", texto)
    texto = re.sub(r"https?://\S+", "", texto)
    texto = re.sub(r"[*_#`>|~]", "", texto)
    return re.sub(r"\s+", " ", texto).strip()


def listar_voces() -> list[dict]:
    """Voces disponibles: modelos Piper en models/piper y voces SAPI instaladas en Windows."""
    voces = []
    for onnx in sorted((BASE_DIR / "models" / "piper").glob("*.onnx")):
        nombre = onnx.stem
        try:
            meta = json.loads(onnx.with_suffix(".onnx.json").read_text(encoding="utf-8"))
            lang = meta.get("language", {}).get("name_native") or meta.get("language", {}).get("code", "")
            nombre = f"{meta.get('dataset', onnx.stem).capitalize()} · {lang} · {meta.get('audio', {}).get('quality', '')}"
        except Exception:
            pass
        voces.append({"motor": "piper", "id": onnx.relative_to(BASE_DIR).as_posix(), "nombre": nombre})
    try:
        import winreg
        raiz = r"SOFTWARE\Microsoft\Speech\Voices\Tokens"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, raiz) as k:
            for i in range(winreg.QueryInfoKey(k)[0]):
                token = winreg.EnumKey(k, i)
                with winreg.OpenKey(k, token) as tk:
                    nombre = winreg.QueryValueEx(tk, "")[0]
                voces.append({"motor": "windows", "id": f"HKEY_LOCAL_MACHINE\\{raiz}\\{token}",
                              "nombre": nombre.replace("Microsoft ", "").replace(" Desktop", "")})
    except Exception as e:
        print(f"[tts] No se pudieron listar las voces de Windows: {e}")
    return voces


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
        self._sapi_dirty = True  # el hilo de reproducción reaplica voz/velocidad/volumen de SAPI
        self._update_speed()
        if cfg.get("motor_voz", "piper") == "piper":
            self._piper = self._load_piper(cfg["voz_piper"])
        threading.Thread(target=self._synth_worker, daemon=True).start()
        threading.Thread(target=self._play_worker, daemon=True).start()

    @property
    def busy(self) -> bool:
        return self._pending > 0

    @staticmethod
    def _load_piper(ruta: str):
        try:
            from piper import PiperVoice
            voz = PiperVoice.load(str(resolve(ruta)))
            print(f"[tts] Voz Piper cargada: {ruta}")
            return voz
        except Exception as e:
            print(f"[tts] Piper no disponible ({e}); uso voces de Windows")
            return None

    def _update_speed(self):
        try:
            from piper import SynthesisConfig
            self._syn_config = SynthesisConfig(length_scale=1.0 / float(self.cfg.get("velocidad_voz", 1.0)))
        except Exception:
            self._syn_config = None

    def set_voice(self, motor: str, voz_id: str) -> bool:
        """Cambia de voz en caliente. Devuelve False si la voz Piper no se pudo cargar."""
        if motor == "piper":
            voz = self._load_piper(voz_id)
            if voz is None:
                return False  # se mantiene la voz actual
            self.stop()
            self._piper = voz
            self.cfg["voz_piper"] = voz_id
        else:
            self.stop()
            self._piper = None
            self.cfg["voz_windows"] = voz_id
        self.cfg["motor_voz"] = motor
        self._sapi_dirty = True
        return True

    def set_speed(self, velocidad: float):
        self.cfg["velocidad_voz"] = float(velocidad)
        self._update_speed()
        self._sapi_dirty = True

    def set_volume(self, volumen: float):
        self.cfg["volumen_voz"] = float(volumen)
        self._sapi_dirty = True

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
        sapi = None  # objetos COM de SAPI: se crean y usan solo en este hilo
        while True:
            gen, texto = self._texts.get()
            if gen != self._gen:
                self._done()
                continue
            try:
                piper = self._piper
                if piper:
                    kwargs = {"syn_config": self._syn_config} if self._syn_config else {}
                    partes = [c.audio_float_array for c in piper.synthesize(texto, **kwargs)]
                    audio = np.concatenate(partes).astype(np.float32) if partes else None
                    sr = piper.config.sample_rate
                else:
                    if sapi is None:
                        sapi = self._init_sapi()
                    audio, sr = self._sapi_synth(sapi, texto), self.SAPI_SR
                if audio is None or not len(audio):
                    self._done()
                else:
                    self._audio.put((gen, audio, sr))
            except Exception as e:
                print(f"[tts] Error sintetizando: {e}")
                self._done()

    def _play_worker(self):
        while True:
            gen, audio, sr = self._audio.get()
            try:
                if gen != self._gen:
                    continue
                vol = float(self.cfg.get("volumen_voz", 1.0))
                self._play(np.clip(audio * vol, -1.0, 1.0) if vol != 1.0 else audio, sr)
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

    # --- Voces de Windows (SAPI) ------------------------------------------
    # Se sintetiza a memoria y se reproduce como el audio de Piper (pyttsx3 se queda mudo
    # tras la primera frase cuando se usa desde un hilo).
    SAPI_SR = 22050
    _SAFT22kHz16BitMono = 22

    @staticmethod
    def _init_sapi():
        import comtypes
        import comtypes.client as cc
        comtypes.CoInitialize()
        return cc.CreateObject("SAPI.SpVoice")

    def _config_sapi(self, voz):
        self._sapi_dirty = False
        tokens = voz.GetVoices()
        lista = [tokens.Item(i) for i in range(tokens.Count)]
        elegida = next((t for t in lista if t.Id == self.cfg.get("voz_windows")), None)
        if elegida is None:
            elegida = next((t for t in lista if "spanish" in t.GetDescription().lower()), None)
        if elegida:
            voz.Voice = elegida
        # SAPI: Rate va de -10 a 10 y +10 equivale a ~3 veces más rápido
        vel = max(0.3, float(self.cfg.get("velocidad_voz", 1.0)))
        voz.Rate = max(-10, min(10, round(10 * np.log(vel) / np.log(3))))

    def _sapi_synth(self, voz, texto: str):
        import comtypes.client as cc
        if self._sapi_dirty:
            self._config_sapi(voz)
        stream = cc.CreateObject("SAPI.SpMemoryStream")
        fmt = cc.CreateObject("SAPI.SpAudioFormat")
        fmt.Type = self._SAFT22kHz16BitMono
        stream.Format = fmt
        voz.AudioOutputStream = stream
        voz.Speak(texto)
        return np.frombuffer(bytes(stream.GetData()), dtype=np.int16).astype(np.float32) / 32768.0
