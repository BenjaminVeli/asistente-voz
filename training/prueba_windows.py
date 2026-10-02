"""Conjunto de prueba con las voces de Windows (Helena, Laura, Pablo, Zira...).

Son un sintetizador totalmente distinto de Piper y nunca se usan para entrenar, así que sirven para
comprobar que el modelo reconoce «Jarvis» en voces que no ha oído nunca.
Se ejecuta con el Python del asistente (necesita comtypes):  ..\\.venv\\Scripts\\python prueba_windows.py
"""
import itertools
import random
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

SALIDA = Path(__file__).parent / "data" / "clips"
SR = 16000
CATEGORIAS = [r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices",
              r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices"]

POSITIVOS_ES = ["Jarvis", "Yarvis", "Yárvis", "Llarvis", "Dyarvis", "Járbis", "Harvis"]
POSITIVOS_EN = ["Jarvis", "Jarvees", "Jarviss"]
ANTES_ES = ["oye", "hola", "bueno", "gracias"]
DESPUES_ES = ["abre Spotify", "qué hora es", "pon música"]
NEGATIVOS_ES = ["Javier", "jardín", "jarabe", "Travis", "Marvin", "Garbis", "Elvis", "Harvard", "servis",
                "Abriendo Spotify, señor.", "Son las diez y media de la noche.", "¿Qué película quieres ver?",
                "Mañana vamos al jardín de mi abuela.", "Javier llegó tarde otra vez.", "Pásame el jarabe.",
                "Todos los sistemas en línea.", "El servidor se ha caído.", "Me voy a dormir, hasta mañana."]
NEGATIVOS_EN = ["Travis", "Marvin", "Harvest", "service", "Jervis", "nervous", "Davis", "starfish",
                "Good evening, sir.", "I parked next to the garage.", "Harvey bought a new jar."]


def voces():
    import comtypes.client as cc
    vistas = {}
    for cat in CATEGORIAS:
        c = cc.CreateObject("SAPI.SpObjectTokenCategory")
        try:
            c.SetId(cat, False)
        except Exception:
            continue
        toks = c.EnumerateTokens()
        for i in range(toks.Count):
            t = toks.Item(i)
            desc = t.GetDescription()
            if ("Spanish" in desc or "English" in desc) and desc not in vistas:
                vistas[desc] = t
    return vistas


def sintetizar(voz, token, texto, rate):
    import comtypes.client as cc
    stream = cc.CreateObject("SAPI.SpMemoryStream")
    fmt = cc.CreateObject("SAPI.SpAudioFormat")
    fmt.Type = 22  # 22 kHz 16 bit mono
    stream.Format = fmt
    voz.Voice = token
    voz.Rate = rate
    voz.AudioOutputStream = stream
    voz.Speak(texto)
    a = np.frombuffer(bytes(stream.GetData()), dtype=np.int16).astype(np.float32) / 32768
    a = resample_poly(a, SR, 22050).astype(np.float32)
    idx = np.where(np.abs(a) > max(0.01, np.abs(a).max() * 0.03))[0]
    return a[max(0, idx[0] - 160): idx[-1] + 160] if len(idx) else None


def main():
    import comtypes
    import comtypes.client as cc
    comtypes.CoInitialize()
    voz = cc.CreateObject("SAPI.SpVoice")
    rng = random.Random(0)
    pos, neg = [], []
    for desc, token in voces().items():
        es = "Spanish" in desc
        print(desc)
        for texto, rate in itertools.product(POSITIVOS_ES if es else POSITIVOS_EN, (-3, -1, 0, 2, 4)):
            palabra = sintetizar(voz, token, texto, rate)
            if palabra is None:
                continue
            pos.append((palabra, len(palabra)))
            # La misma palabra dentro de una frase.
            antes = sintetizar(voz, token, rng.choice(ANTES_ES if es else ["hey", "okay"]), rate)
            despues = sintetizar(voz, token, rng.choice(DESPUES_ES if es else ["open spotify"]), rate)
            if antes is not None and despues is not None:
                pausa = np.zeros(int(SR * rng.uniform(0.05, 0.25)), np.float32)
                pos.append((np.concatenate([antes, pausa, palabra, pausa, despues]), len(antes) + len(pausa) + len(palabra)))
        for texto, rate in itertools.product(NEGATIVOS_ES if es else NEGATIVOS_EN, (-2, 0, 3)):
            a = sintetizar(voz, token, texto, rate)
            if a is not None:
                neg.append((a, -1))
    for nombre, datos in (("win_pos", pos), ("win_neg", neg)):
        audios = np.empty(len(datos), dtype=object)
        audios[:] = [(d[0] / max(1e-6, np.abs(d[0]).max()) * 0.9 * 32767).astype(np.int16) for d in datos]
        np.savez(SALIDA / f"{nombre}.npz", audio=audios, fin=np.array([d[1] for d in datos]))
        print(nombre, len(datos))


if __name__ == "__main__":
    main()
