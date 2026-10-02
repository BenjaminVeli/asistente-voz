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

from jarvis import config, media, wakeword
from jarvis.assistant import Assistant
from jarvis.stt import listar_microfonos
from jarvis.tts import listar_voces

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
        if tipo == "quit":  # despedida: cerrar la ventana termina la aplicación (ver main)
            if self._window:
                self._window.destroy()
            return
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
            "motor_voz": self._cfg.get("motor_voz", "piper"),
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

    # --- ajustes (panel del engranaje) ------------------------------------
    def get_settings(self):
        c = self._cfg
        return {
            "voces": listar_voces(),
            "motor": c.get("motor_voz", "piper"),
            "voz": c["voz_piper"] if c.get("motor_voz", "piper") == "piper" else c.get("voz_windows", ""),
            "velocidad": c.get("velocidad_voz", 1.0),
            "volumen": c.get("volumen_voz", 1.0),
            "nombre": c["nombre_asistente"],
            "tratamiento": c["tratamiento"],
            "palabra": c.get("palabra_activacion", "jarvis"),
            "umbral": c.get("umbral_activacion", 0.5),
            "activacion_local": wakeword.disponible(c),
            "modelos": self._assistant.llm.modelos() if self._assistant else [],
            "modelo": c["modelo_llm"],
        }

    def update_setting(self, clave, valor):
        """Aplica un ajuste al momento y lo guarda en config.json."""
        if self._bloqueado():
            return {"ok": False, "error": "Controles bloqueados durante la autodestrucción"}
        a, c = self._assistant, self._cfg
        if clave == "voz":
            motor, voz_id = valor["motor"], valor["id"]
            if a and not a.tts.set_voice(motor, voz_id):
                return {"ok": False, "error": "No se pudo cargar esa voz"}
            c["motor_voz"] = motor
            c["voz_piper" if motor == "piper" else "voz_windows"] = voz_id
            self._emit("tts_engine", motor)
        elif clave == "velocidad":
            c["velocidad_voz"] = round(float(valor), 2)
            if a:
                a.tts.set_speed(c["velocidad_voz"])
        elif clave == "volumen":
            c["volumen_voz"] = round(float(valor), 2)
            if a:
                a.tts.set_volume(c["volumen_voz"])
        elif clave in ("nombre", "tratamiento", "palabra"):
            valor = str(valor).strip()
            if not valor:
                return {"ok": False, "error": "No puede quedar vacío"}
            if clave == "nombre":
                c["nombre_asistente"] = valor
            elif clave == "tratamiento":
                c["tratamiento"] = valor
            else:
                c["palabra_activacion"] = valor.lower()
                if a:
                    a.commands.set_palabra(valor)
                    a.listener.cancel.set()  # la escucha continua se reinicia y elige detector local o Whisper
        elif clave == "sensibilidad":
            c["umbral_activacion"] = round(1 - float(valor), 2)
        elif clave == "modelo":
            c["modelo_llm"] = str(valor)
            if a:
                a.llm.model = c["modelo_llm"]
                threading.Thread(target=a.llm.warmup, daemon=True).start()
                self._emit("status", {"llm": True, "model": c["modelo_llm"]})
        else:
            return {"ok": False, "error": f"Ajuste desconocido: {clave}"}
        config.save(c)
        return {"ok": True}

    def test_voice(self):
        if self._assistant and not self._bloqueado():
            a = self._assistant
            if not a.tts.enabled:
                self._emit("error", "La voz está desactivada: actívala con el botón del altavoz para oír la prueba.")
                return
            a.tts.stop()
            a.tts.say(f"Hola, {self._cfg['tratamiento']}. Así es como sueno ahora. ¿Le parece bien?")

    def media(self, accion):
        if self._bloqueado():
            return
        acciones = {"play": media.play_pause, "next": media.siguiente, "prev": media.anterior,
                    "up": lambda: media.subir_volumen(3), "down": lambda: media.bajar_volumen(3),
                    "mute": media.silenciar}
        if accion in acciones:
            acciones[accion]()

    def get_media(self):
        """Lo que suena ahora y el volumen del sistema (la interfaz lo consulta cada pocos segundos)."""
        return {"pista": media.reproduciendo(), "volumen": media.volumen()}

    def set_volumen(self, nivel):
        if not self._bloqueado():
            media.fijar_volumen(int(nivel))

    # --- memoria y activaciones ----------------------------------------------
    def get_memoria(self):
        return self._assistant.memoria.lista() if self._assistant else []

    def memoria_agregar(self, dato):
        if not self._assistant or self._bloqueado():
            return {"ok": False, "error": "No disponible ahora"}
        r = self._assistant.memoria.recordar(str(dato))
        return {"ok": r == "Dato guardado en memoria.", "error": r}

    def memoria_editar(self, indice, dato):
        ok = bool(self._assistant) and not self._bloqueado() and self._assistant.memoria.editar(int(indice), str(dato))
        return {"ok": ok}

    def memoria_borrar(self, indice):
        ok = bool(self._assistant) and not self._bloqueado() and self._assistant.memoria.borrar(int(indice))
        return {"ok": ok}

    def get_activaciones(self):
        return self._assistant.activaciones if self._assistant else []

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
