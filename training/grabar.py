"""Graba tu voz para afinar el detector: lo mejor que se le puede dar es tu voz y tu micrófono.

    python grabar.py jarvis       → dices «Jarvis» cada vez que lo pida (unas 40 veces, de formas distintas)
    python grabar.py ambiente     → graba unos minutos de tu habitación normal (tele, música, hablar sin decir Jarvis)

Se guardan en data/personal/{pos,neg}/ y aumentar.py los mezcla con el resto (con más peso).
Después:  python aumentar.py && python entrenar.py
"""
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

SR = 16000
CARPETA = Path(__file__).parent / "data" / "personal"
CONSEJOS = ["normal", "normal", "más rápido", "más despacio", "más bajo, casi susurrando", "más alto",
            "lejos del micrófono", "como pregunta: ¿Jarvis?", "con «oye» delante: «Oye Jarvis»", "cansado", "con prisa"]


def _microfono():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from jarvis import config
        from jarvis.stt import _buscar_dispositivo
        return _buscar_dispositivo(config.load().get("microfono", ""))
    except Exception:
        return None


def grabar_jarvis(n=40):
    dest = CARPETA / "pos"
    dest.mkdir(parents=True, exist_ok=True)
    dev = _microfono()
    print(f"Vas a decir «Jarvis» {n} veces. Tras el pitido tienes 2,5 s. Ctrl+C para parar.\n")
    for i in range(n):
        consejo = CONSEJOS[i % len(CONSEJOS)]
        input(f"[{i + 1}/{n}] Pulsa Enter y di «Jarvis» ({consejo})...")
        print("  ● grabando")
        audio = sd.rec(int(2.5 * SR), samplerate=SR, channels=1, dtype="float32", device=dev)
        sd.wait()
        audio = audio[:, 0]
        if np.abs(audio).max() < 0.01:
            print("  no se oyó nada, repetimos")
            continue
        sf.write(dest / f"{int(time.time() * 1000)}.wav", audio, SR)
        print("  ✓")


def grabar_ambiente(minutos=5.0):
    dest = CARPETA / "neg"
    dest.mkdir(parents=True, exist_ok=True)
    dev = _microfono()
    print(f"Grabando {minutos} min de ambiente: tele, música, conversación... SIN decir «Jarvis». Ctrl+C para parar.")
    trozos = []
    try:
        with sd.InputStream(samplerate=SR, channels=1, dtype="float32", device=dev) as s:
            t0 = time.time()
            while time.time() - t0 < minutos * 60:
                trozos.append(s.read(SR)[0][:, 0].copy())
                print(f"\r  {time.time() - t0:5.0f} s", end="")
    except KeyboardInterrupt:
        pass
    if trozos:
        sf.write(dest / f"{int(time.time())}.wav", np.concatenate(trozos), SR)
        print("\n  ✓ guardado")


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "jarvis"
    grabar_ambiente(float(sys.argv[2]) if len(sys.argv) > 2 else 5) if modo == "ambiente" else grabar_jarvis()
