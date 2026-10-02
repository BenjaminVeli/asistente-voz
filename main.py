"""J.A.R.V.I.S — asistente de voz local (Ollama + Whisper + Piper) con interfaz futurista."""
import json
import os
import queue
import sys
import threading
import time

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")  # los modelos ya están descargados: nada sale a internet

import webview

from jarvis import config, media
from jarvis.assistant import Assistant
from jarvis.stt import listar_microfonos

BASE = os.path.dirname(os.path.abspath(__file__))


class Api:
    """Métodos accesibles desde JavaScript como window.pywebview.api.<método>()."""

    def __init__(self, cfg):
        self._cfg = cfg
        self._window = None
        self._events: queue.Queue = queue.Queue()
        self._assistant = None

    # --- puente Python -> interfaz -------------------------------------
    def _emit(self, tipo, datos=None):
        self._events.put({"type": tipo, "data": datos})

    def _pump(self):
        while True:
            batch = [self._events.get()]
            time.sleep(0.02)
            while not self._events.empty():
                batch.append(self._events.get_nowait())
            js = f"window.jarvis && window.jarvis.onEvents({json.dumps(batch, ensure_ascii=False)})"
            try:
                if hasattr(self._window, "run_js"):
                    self._window.run_js(js)
                else:
                    self._window.evaluate_js(js)
            except Exception as e:
                print(f"[ui] {e}")

    def _start(self, window):
        self._window = window
        threading.Thread(target=self._pump, daemon=True).start()
        self._assistant = Assistant(self._cfg, self._emit)
        try:
            import keyboard
            keyboard.add_hotkey(self._cfg.get("atajo_teclado", "ctrl+alt+j"), self.listen)
        except Exception as e:
            print(f"[hotkey] No se pudo registrar el atajo global: {e}")

    # --- interfaz -> Python --------------------------------------------
    def _bloqueado(self):
        """Durante la autodestrucción los controles se ignoran: solo vale el código de cancelación."""
        return bool(self._assistant and self._assistant.autodestruccion)

    def get_init(self):
        return {
            "nombre": self._cfg["nombre_asistente"],
            "modelo": self._cfg["modelo_llm"],
            "atajo": self._cfg.get("atajo_teclado", "ctrl+alt+j"),
            "microfonos": listar_microfonos(),
            "microfono": self._cfg.get("microfono", ""),
            "continua": self._cfg.get("escucha_continua", False),
            "voz": self._cfg.get("voz_activada", True),
            "accesos": self._cfg.get("accesos_rapidos", []),
            "palabra": self._cfg.get("palabra_activacion", "jarvis"),
        }

    def get_stats(self):
        if self._assistant:
            return self._assistant.stats.snapshot()
        return None

    def send_text(self, texto):
        if self._assistant:
            threading.Thread(target=self._assistant.handle_text, args=(texto,), daemon=True).start()

    def listen(self):
        if self._assistant and not self._bloqueado():
            self._assistant.listen_once()

    def stop(self):
        if self._assistant and not self._bloqueado():
            self._assistant.stop_all()

    def set_continuous(self, on):
        if self._bloqueado():
            self._emit("continuous", self._cfg.get("escucha_continua", False))
            return
        self._cfg["escucha_continua"] = bool(on)
        config.save(self._cfg)
        if self._assistant:
            self._assistant.set_continuous(bool(on))

    def set_voice(self, on):
        if self._bloqueado():
            return
        self._cfg["voz_activada"] = bool(on)
        config.save(self._cfg)
        if self._assistant:
            self._assistant.tts.enabled = bool(on)
            if not on:
                self._assistant.tts.stop()

    def set_microfono(self, nombre):
        if self._bloqueado():
            return
        self._cfg["microfono"] = nombre
        config.save(self._cfg)
        if self._assistant:
            self._assistant.listener.set_microfono(nombre)

    def media(self, accion):
        if self._bloqueado():
            return
        acciones = {"play": media.play_pause, "next": media.siguiente, "prev": media.anterior,
                    "up": lambda: media.subir_volumen(3), "down": lambda: media.bajar_volumen(3),
                    "mute": media.silenciar}
        if accion in acciones:
            acciones[accion]()

    def open_app(self, nombre):
        self.send_text(f"abre {nombre}")


def main():
    cfg = config.load()
    api = Api(cfg)
    window = webview.create_window(
        "J.A.R.V.I.S", os.path.join(BASE, "web", "index.html"), js_api=api,
        width=1440, height=880, min_size=(1100, 700), background_color="#03070c",
    )
    webview.start(api._start, window, debug="--debug" in sys.argv)
    os._exit(0)


if __name__ == "__main__":
    main()
