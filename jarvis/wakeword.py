"""Palabra de activación local con openWakeWord: detecta «Jarvis» sin pasar el audio por Whisper.

Un modelo diminuto (entrenado en training/) puntúa cada trozo de 80 ms del micrófono. Gasta muy poca
CPU y responde en cuanto termina la palabra; Whisper solo se usa para la orden que viene después.
"""
import numpy as np

from .config import resolve

TROZO = 1280  # 80 ms a 16 kHz: lo que procesa openWakeWord de cada vez
CARPETA = "models/wakeword"


class Activador:
    def __init__(self, cfg: dict):
        from openwakeword.model import Model
        carpeta = resolve(CARPETA)
        ruta = resolve(cfg.get("modelo_activacion", f"{CARPETA}/jarvis.onnx"))
        self.nombre = ruta.stem
        self.model = Model(wakeword_models=[str(ruta)], inference_framework="onnx",
                           melspec_model_path=str(carpeta / "melspectrogram.onnx"),
                           embedding_model_path=str(carpeta / "embedding_model.onnx"))
        self.cfg = cfg

    @property
    def umbral(self) -> float:
        return float(self.cfg.get("umbral_activacion", 0.5))

    def reset(self):
        self.model.reset()

    def puntuar(self, chunk: np.ndarray) -> float:
        """chunk: 1280 muestras float32 (-1..1). Devuelve la probabilidad de que acabe de oírse «Jarvis»."""
        pcm = (np.clip(chunk, -1, 1) * 32767).astype(np.int16)
        return float(self.model.predict(pcm)[self.nombre])


def disponible(cfg: dict) -> bool:
    """Se usa si está activado, el modelo existe y la palabra configurada es «Jarvis»."""
    from .apps import normalize
    if not cfg.get("activacion_local", True) or normalize(cfg.get("palabra_activacion", "jarvis")) != "jarvis":
        return False
    ruta = resolve(cfg.get("modelo_activacion", f"{CARPETA}/jarvis.onnx"))
    return ruta.exists() and resolve(f"{CARPETA}/embedding_model.onnx").exists()
