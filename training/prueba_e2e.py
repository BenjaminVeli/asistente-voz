"""Prueba de punta a punta del bucle de escucha continua con un micrófono falso.

Simula: ruido → «Jarvis, abre Spotify» (voz de Windows nunca vista al entrenar) → silencio,
y comprueba que el asistente detecta, transcribe y ejecuta la orden (handle_text).
"""
import sys
import threading
import time

import numpy as np

sys.path.insert(0, r"C:\asistente-voz")
import sounddevice as sd

from jarvis import assistant as A

SR = 16000


def sapi(texto, voz="Helena"):
    import comtypes
    import comtypes.client as cc
    from scipy.signal import resample_poly
    comtypes.CoInitialize()
    v = cc.CreateObject("SAPI.SpVoice")
    for i in range(v.GetVoices().Count):
        t = v.GetVoices().Item(i)
        if voz in t.GetDescription():
            v.Voice = t
    st = cc.CreateObject("SAPI.SpMemoryStream")
    f = cc.CreateObject("SAPI.SpAudioFormat")
    f.Type = 22
    st.Format = f
    v.AudioOutputStream = st
    v.Speak(texto)
    a = np.frombuffer(bytes(st.GetData()), dtype=np.int16).astype(np.float32) / 32768
    return resample_poly(a, SR, 22050).astype(np.float32)


class MicFalso:
    def __init__(self, audio):
        self.audio, self.i = audio, 0

    def __call__(self, *a, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def read(self, n):
        time.sleep(n / SR / 4)  # 4x tiempo real
        trozo = self.audio[self.i:self.i + n]
        self.i += n
        if len(trozo) < n:
            trozo = np.concatenate([trozo, np.random.randn(n - len(trozo)).astype(np.float32) * 0.002])
        return trozo[:, None], False


def escenario(nombre, partes, esperado):
    ruido = lambda s: np.random.randn(int(s * SR)).astype(np.float32) * 0.003
    audio = np.concatenate([ruido(2.5)] + [p if isinstance(p, np.ndarray) else ruido(p) for p in partes] + [ruido(4)])
    mic = MicFalso(audio)
    sd.InputStream = mic
    A.sd.InputStream = mic
    eventos, ordenes = [], []
    cfg = A.config_cargada
    a = A.Assistant.__new__(A.Assistant)
    a.__dict__.update(A.plantilla.__dict__)
    a.emit = lambda t, d=None: eventos.append((t, d))
    a.handle_text = lambda texto, por_voz=False: ordenes.append(texto)
    a.say = lambda t: eventos.append(("say", t))
    a._continuous.set()
    hilo = threading.Thread(target=a._continuous_loop, daemon=True)
    hilo.start()
    t0 = time.time()
    while time.time() - t0 < 25 and not ordenes and not any(e[0] == "say" for e in eventos):
        time.sleep(0.2)
    a._continuous.clear()
    a.listener.cancel.set()
    hilo.join(3)
    dichos = [e[1] for e in eventos if e[0] == "say"]
    ok = esperado(ordenes, dichos)
    acts = [e[1]["resultado"] for e in eventos if e[0] == "activation"]
    wakes = [e for e in eventos if e[0] == "wake"]
    print(f"{'OK ' if ok else 'MAL'} {nombre}: órdenes={ordenes} dijo={dichos} activaciones={acts} "
          f"eventos_detector={len(wakes)} (último={wakes[-1][1] if wakes else '-'})")
    return ok


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    from jarvis import config
    A.LOG_ACTIVACIONES = Path(tempfile.gettempdir()) / "jarvis_prueba_activaciones.jsonl"  # no tocar el real
    cfg = config.load()
    cfg["escucha_continua"] = False
    cfg["voz_activada"] = False  # que no hable por los altavoces durante la prueba
    A.config_cargada = cfg
    # Un asistente real (Whisper, comandos...) del que se copian las piezas para cada escenario.
    A.plantilla = A.Assistant(cfg, lambda *a: None)
    A.plantilla.listener.ready.wait()
    A.plantilla.tts.enabled = False
    time.sleep(2)
    jarvis = sapi("Jarvis")
    orden = sapi("abre Spotify")
    frase = sapi("Mañana vamos al jardín de Javier a comer con Travis.")
    r = [
        escenario("Jarvis + orden seguida", [jarvis, 0.15, orden],
                  lambda o, d: len(o) == 1 and "spotify" in o[0].lower()),
        escenario("solo Jarvis → ¿Sí?", [jarvis, 1.5],
                  lambda o, d: any("¿Sí" in x for x in d)),
        escenario("frase sin Jarvis → nada", [frase, 1.0],
                  lambda o, d: not o and not d),
    ]
    print(f"{sum(r)}/{len(r)} escenarios correctos")
