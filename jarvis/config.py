"""Carga y guarda la configuración del asistente (config.json)."""
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULTS = {
    "nombre_asistente": "JARVIS",
    "tratamiento": "señor",
    "ollama_url": "http://127.0.0.1:11434",
    "modelo_llm": "gemma4:e2b",
    "whisper_modelo": "small",
    "whisper_dispositivo": "cpu",
    "microfono": "Mic in at rear panel",
    "voz_piper": "models/piper/es_ES-davefx-medium.onnx",
    "velocidad_voz": 1.05,
    "voz_activada": True,
    "palabra_activacion": "jarvis",
    "escucha_continua": False,
    "atajo_teclado": "ctrl+alt+j",
    "aliases_apps": {
        "navegador": "Google Chrome",
        "chrome": "Google Chrome",
        "musica": "Spotify",
        "codigo": "Visual Studio Code",
        "vs code": "Visual Studio Code",
        "visual studio": "Visual Studio Code",
        "calculadora": "Calculadora",
        "bloc de notas": "Bloc de notas",
        "notas": "Bloc de notas",
        "explorador": "Explorador de archivos",
        "archivos": "Explorador de archivos",
        "configuracion": "Configuración",
        "ajustes": "Configuración",
    },
    "accesos_rapidos": ["Spotify", "Google Chrome", "Discord", "Steam", "Visual Studio Code"],
}


def load() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            for k, v in user.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except Exception as e:
            print(f"[config] Error leyendo config.json, uso valores por defecto: {e}")
    else:
        save(cfg)
    return cfg


def save(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else BASE_DIR / p
