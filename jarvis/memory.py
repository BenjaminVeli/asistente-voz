"""Memoria a largo plazo: datos que el usuario pide recordar, guardados en memoria.json."""
import datetime
import json
import threading

from .apps import normalize
from .config import resolve

MAX_DATOS = 60  # el system prompt tiene que caber en el contexto del modelo


class Memoria:
    def __init__(self, path: str = "memoria.json"):
        self.path = resolve(path)
        self._lock = threading.Lock()
        self.datos: list[dict] = []
        try:
            self.datos = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[memoria] No se pudo leer {self.path}: {e}")

    def _guardar(self):
        self.path.write_text(json.dumps(self.datos, ensure_ascii=False, indent=2), encoding="utf-8")

    def recordar(self, dato: str) -> str:
        dato = dato.strip().rstrip(".")
        if not dato:
            return "No hay nada que recordar."
        dato = dato[0].upper() + dato[1:]
        with self._lock:
            if any(normalize(d["dato"]) == normalize(dato) for d in self.datos):
                return "Eso ya lo tenía guardado."
            self.datos.append({"dato": dato, "fecha": datetime.date.today().isoformat()})
            self.datos = self.datos[-MAX_DATOS:]
            self._guardar()
        return "Dato guardado en memoria."

    def olvidar(self, texto: str) -> str:
        """Borra los datos que contienen todas las palabras significativas de `texto`."""
        palabras = [p for p in normalize(texto).split() if len(p) > 2]
        if not palabras:
            return "No sé qué dato olvidar."
        with self._lock:
            quedan = [d for d in self.datos if not all(p in normalize(d["dato"]) for p in palabras)]
            borrados = len(self.datos) - len(quedan)
            if borrados:
                self.datos = quedan
                self._guardar()
        return f"Olvidado ({borrados} dato{'s' if borrados != 1 else ''})." if borrados else \
            "No encuentro ningún dato así en memoria."

    def resumen(self) -> list[str]:
        return [d["dato"] for d in self.datos]

    def para_prompt(self) -> str:
        if not self.datos:
            return ""
        lineas = "\n".join(f"- {d['dato']} (guardado el {d['fecha']})" for d in self.datos)
        return ("\nDatos que el usuario te ha pedido recordar (los dijo él, en primera persona). "
                "Úsalos cuando vengan al caso, sin recitarlos si no se te pregunta:\n" + lineas)
