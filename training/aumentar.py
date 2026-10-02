"""Paso 2: convierte los clips en ventanas de 2 s «como las oiría tu micrófono» y extrae sus características.

Cada ventana se ensucia al azar: eco de habitación (respuestas al impulso reales), ruido de fondo
(MUSAN: música, gente hablando, ruidos; ESC-50: ruidos domésticos), ecualización de micrófono barato,
volumen alto o bajo, saturación... En los positivos, «Jarvis» termina justo al final de la ventana
(con unos milisegundos de margen), que es cuando el detector debe disparar.

Las características son las del modelo base de openWakeWord: 16 vectores de 96 valores por ventana.
Uso:  python aumentar.py
Salida: data/features/<conjunto>.npy  (float16, forma N x 16 x 96)
"""
import multiprocessing as mp
import os
import random
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, fftconvolve, resample_poly, sosfilt

from generar import cargar_conjunto

DATA = Path(__file__).parent / "data"
CLIPS = DATA / "clips"
FEATS = DATA / "features"
SR = 16000
VENTANA = 32000  # 2 s → 16 vectores de características

# (conjunto de clips, copias aumentadas por clip, ¿es positivo?, nivel de suciedad)
PLAN = [
    ("pos_train", 2, True, "normal"),
    ("pos_test", 1, True, "limpio"),
    ("pos_test", 2, True, "normal"),
    ("casi_train", 2, False, "normal"),
    ("casi_test", 2, False, "normal"),
    ("parecida_train", 2, False, "normal"),
    ("parecida_test", 2, False, "normal"),
    ("frase_train", 2, False, "normal"),
    ("frase_test", 2, False, "normal"),
    ("win_pos", 1, True, "limpio"),
    ("win_pos", 2, True, "normal"),
    ("win_neg", 3, False, "normal"),
]
N_FONDO = 20000  # ventanas de solo ruido/música/voz sin «Jarvis»
COPIAS_PERSONAL = 60  # tu voz vale mucho más que la sintética: cada grabación se repite muy ensuciada

_ruidos: list[np.ndarray] = []
_rirs: list[np.ndarray] = []


def _lista_ruidos():
    archivos = []
    for sub in ("noise", "music", "speech"):
        archivos += [(str(p), sub) for p in (DATA / "musan" / sub).rglob("*.wav")]
    archivos += [(str(p), "esc") for p in (DATA / "ESC-50-master" / "audio").glob("*.wav")]
    return archivos


def _leer_trozo(ruta, segundos, rng):
    info = sf.info(ruta)
    n = int(segundos * info.samplerate)
    inicio = rng.randrange(0, max(1, info.frames - n))
    a, sr = sf.read(ruta, start=inicio, frames=n, dtype="float32", always_2d=True)
    a = a.mean(axis=1)
    if sr != SR:
        a = resample_poly(a, SR, sr).astype(np.float32)
    return a


def _init(semilla):
    """Cada proceso carga su propia reserva de trozos de ruido y de habitaciones."""
    global _ruidos, _rirs
    rng = random.Random(semilla + os.getpid())  # cada proceso, su propia reserva de ruidos
    archivos = _lista_ruidos()
    # Más ruido y voz que música: es lo que suele sonar en una habitación.
    pesos = {"noise": 3.0, "speech": 2.0, "music": 1.5, "esc": 2.5}
    w = [pesos[t] for _, t in archivos]
    _ruidos = []
    for ruta, _ in rng.choices(archivos, weights=w, k=600):
        try:
            a = _leer_trozo(ruta, 2.5, rng)
            if len(a) >= VENTANA and np.abs(a).max() > 1e-4:
                _ruidos.append(a[:VENTANA].copy())
        except Exception:
            pass
    _rirs = []
    for p in sorted((DATA / "rir").glob("*.wav")):
        r, sr = sf.read(str(p), dtype="float32")
        if r.ndim > 1:
            r = r[:, 0]
        if sr != SR:
            r = resample_poly(r, SR, sr).astype(np.float32)
        _rirs.append(r / (np.abs(r).max() + 1e-9))


def _rms(a):
    return float(np.sqrt(np.mean(a ** 2)) + 1e-9)


def ensuciar(x: np.ndarray, rng: random.Random, nivel: str) -> np.ndarray:
    """Aplica al azar lo que le pasa a una voz real antes de llegar al micrófono."""
    if nivel == "limpio":
        return x
    if rng.random() < 0.5 and _rirs:
        r = rng.choice(_rirs)
        y = fftconvolve(x, r)[: len(x)]
        x = y / (np.abs(y).max() + 1e-9) * np.abs(x).max()
    if rng.random() < 0.5:  # micrófono: corta graves y agudos
        bajo = rng.uniform(60, 400)
        alto = rng.uniform(3000, 7800)
        x = sosfilt(butter(2, [bajo, alto], btype="band", fs=SR, output="sos"), x).astype(np.float32)
    if rng.random() < 0.85 and _ruidos:
        ruido = rng.choice(_ruidos)
        if rng.random() < 0.3:
            ruido = ruido + rng.choice(_ruidos) * rng.uniform(0.2, 1.0)
        snr = rng.uniform(0, 30)
        x = x + ruido * (_rms(x) / _rms(ruido)) / (10 ** (snr / 20))
    if rng.random() < 0.3:  # zumbido eléctrico / ventilador
        t = np.arange(len(x)) / SR
        x = x + np.sin(2 * np.pi * rng.choice([50, 60, 100, 120]) * t).astype(np.float32) * _rms(x) * rng.uniform(0.02, 0.2)
    pico = rng.uniform(0.03, 0.95)
    x = x / (np.abs(x).max() + 1e-9) * pico
    if rng.random() < 0.05:
        x = np.clip(x * rng.uniform(1.5, 4), -1, 1)
    return x


def _colocar(audio, fin, rng):
    """Pone el clip en una ventana de 2 s. Positivos: la palabra acaba al final (con margen)."""
    if rng.random() < 0.3:  # ritmo y tono un poco distintos
        f = rng.choice([(23, 25), (24, 25), (25, 24), (25, 23), (12, 13), (13, 12)])
        audio = resample_poly(audio, *f).astype(np.float32)
        if fin >= 0:
            fin = int(fin * f[0] / f[1])
    x = np.zeros(VENTANA, np.float32)
    if fin >= 0:
        margen = int(SR * rng.uniform(0.0, 0.25))
        fin_en_ventana = VENTANA - margen
        inicio = fin_en_ventana - fin  # posición del clip en la ventana (puede ser negativa)
    else:
        if len(audio) >= VENTANA:
            inicio = -rng.randrange(0, len(audio) - VENTANA + 1)
        else:
            inicio = rng.randrange(0, VENTANA - len(audio) + 1)
    a0, b0 = max(0, inicio), min(VENTANA, inicio + len(audio))
    x[a0:b0] = audio[a0 - inicio: b0 - inicio]
    return x


def _trabajo(args):
    audios, fines, copias, nivel, semilla = args
    rng = random.Random(semilla)
    out = []
    for audio, fin in zip(audios, fines):
        audio = audio.astype(np.float32) / 32768
        for _ in range(copias):
            x = ensuciar(_colocar(audio, int(fin), rng), rng, nivel)
            out.append((x * 32767).astype(np.int16))
    return np.stack(out) if out else np.zeros((0, VENTANA), np.int16)


def fin_de_voz(audio):
    """Última muestra con voz (donde acaba «Jarvis» en tus grabaciones)."""
    energia = np.convolve(audio.astype(np.float32) ** 2, np.ones(320) / 320, mode="same")
    voz = np.where(energia > energia.max() * 0.02)[0]
    return int(voz[-1]) if len(voz) else len(audio)


def personales():
    """data/personal/pos/*.wav (tú diciendo «Jarvis») y neg/*.wav (tu ambiente), si los has grabado."""
    pos, neg = [], []
    for p in sorted((DATA / "personal" / "pos").glob("*.wav")):
        a, _ = sf.read(str(p), dtype="int16")
        pos.append((a, fin_de_voz(a)))
    for p in sorted((DATA / "personal" / "neg").glob("*.wav")):
        a, _ = sf.read(str(p), dtype="int16")
        # Trozos de 4 s: de cada uno sale una ventana al azar.
        neg += [(a[i:i + 4 * SR], -1) for i in range(0, max(1, len(a) - 4 * SR), 2 * SR)]
    return pos, neg


def _fondo(args):
    n, semilla = args
    rng = random.Random(semilla)
    out = []
    for _ in range(n):
        x = rng.choice(_ruidos).copy()
        if rng.random() < 0.4:
            x = x + rng.choice(_ruidos) * rng.uniform(0.1, 1.0)
        x = x / (np.abs(x).max() + 1e-9) * rng.uniform(0.01, 0.95)
        out.append((x * 32767).astype(np.int16))
    return np.stack(out)


class Extractor:
    def __init__(self):
        import onnxruntime as ort
        ort.preload_dlls()
        from openwakeword.utils import AudioFeatures
        self.f = AudioFeatures(inference_framework="onnx", device="gpu")

    def __call__(self, ventanas: np.ndarray) -> np.ndarray:
        partes = [self.f.embed_clips(ventanas[i:i + 2048], batch_size=256) for i in range(0, len(ventanas), 2048)]
        return np.concatenate(partes).astype(np.float16)


def main():
    global FEATS, N_FONDO
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--rapido", action="store_true", help="prueba del proceso: pocas copias, a data/features_prueba")
    a = ap.parse_args()
    plan = PLAN
    if a.rapido:
        FEATS = DATA / "features_prueba"
        N_FONDO = 3000
        plan = [(n, 1, p, nv) for n, _, p, nv in PLAN]
    FEATS.mkdir(parents=True, exist_ok=True)
    extractor = Extractor()
    with mp.Pool(os.cpu_count(), initializer=_init, initargs=(1234,)) as pool:
        hechos = {}
        for nombre, copias, positivo, nivel in plan:
            if (CLIPS / nombre).is_dir():
                audios, fines = cargar_conjunto(nombre)
            elif (CLIPS / f"{nombre}.npz").exists():
                d = np.load(CLIPS / f"{nombre}.npz", allow_pickle=True)
                audios, fines = d["audio"], d["fin"]
            else:
                print("falta", nombre)
                continue
            if len(audios) == 0:
                continue
            trozos = [(audios[i:i + 200], fines[i:i + 200], copias, nivel, hash((nombre, nivel, i)) & 0xFFFFFF)
                      for i in range(0, len(audios), 200)]
            ventanas = np.concatenate(pool.map(_trabajo, trozos))
            clave = f"{nombre}_{nivel}" if nombre.endswith("test") or nombre.startswith("win") else nombre
            hechos.setdefault(clave, []).append(extractor(ventanas))
            print(f"{clave}: {len(ventanas)} ventanas", flush=True)
        pos_p, neg_p = personales()
        for clave, datos, copias in (("personal_pos", pos_p, COPIAS_PERSONAL), ("personal_neg", neg_p, 4)):
            if datos:
                audios = np.empty(len(datos), dtype=object)
                audios[:] = [d[0] for d in datos]
                fines = np.array([d[1] for d in datos])
                trozos = [(audios[i:i + 20], fines[i:i + 20], copias, "normal", 777 + i) for i in range(0, len(audios), 20)]
                ventanas = np.concatenate(pool.map(_trabajo, trozos))
                hechos[clave] = [extractor(ventanas)]
                print(f"{clave}: {len(ventanas)} ventanas", flush=True)
        fondo = np.concatenate(pool.map(_fondo, [(1000, s) for s in range(N_FONDO // 1000)]))
        hechos["fondo"] = [extractor(fondo)]
        print(f"fondo: {len(fondo)} ventanas", flush=True)
    for clave, partes in hechos.items():
        np.save(FEATS / f"{clave}.npy", np.concatenate(partes))


if __name__ == "__main__":
    main()
