"""Orquestador: une micrófono, órdenes, modelo de lenguaje y voz, y notifica a la interfaz."""
import json
import queue
import random
import re
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

from . import media, wakeword
from .apps import AppIndex
from .commands import HERRAMIENTAS_CONSULTA, Commands, es_autodestruccion, es_cancelacion
from .llm import LLM
from .memory import Memoria
from .stats import SystemStats
from .stt import Listener
from .tts import TTS

FIN_FRASE = re.compile(r"(.+?[.!?;:])(\s+|$)", re.S)
PROMPT_CANCELACION = "Función de comando código 10."
LOG_DESTRUCCION = Path(__file__).resolve().parent.parent / "autodestruccion.log"
LOG_ACTIVACIONES = Path(__file__).resolve().parent.parent / "activaciones.jsonl"
MAX_ACTIVACIONES = 200  # las que se cargan al arrancar y se muestran en la interfaz


def saludo_inicial(tratamiento: str, hora: int | None = None) -> str:
    """Saludo de arranque según la hora: días de 6 a 12, tardes de 12 a 20, noches de 20 a 6."""
    hora = time.localtime().tm_hour if hora is None else hora
    if 6 <= hora < 12:
        saludo, extra = "Buenos días", ["Todos los sistemas en línea.", "Listo para empezar el día.",
                                        "Sistemas operativos y a su disposición."]
    elif 12 <= hora < 20:
        saludo, extra = "Buenas tardes", ["Todos los sistemas en línea.", "Sistemas operativos y a su disposición.",
                                          "¿En qué puedo ayudarle esta tarde?"]
    elif 20 <= hora:
        saludo, extra = "Buenas noches", ["Todos los sistemas en línea.", "Sistemas operativos y a su disposición.",
                                          "¿En qué puedo ayudarle esta noche?"]
    else:
        saludo, extra = "Buenas noches", ["Trabajando hasta tarde, por lo que veo. Todos los sistemas en línea.",
                                          "Sigue despierto a estas horas. Sistemas operativos y a su disposición."]
    return f"{saludo}, {tratamiento}. {random.choice(extra)}"


def despedida(tratamiento: str, hora: int | None = None) -> str:
    """Despedida al cerrar la sesión, según la hora."""
    hora = time.localtime().tm_hour if hora is None else hora
    if 20 <= hora or hora < 6:
        opciones = [f"Buenas noches, {tratamiento}. Que descanse.",
                    f"Que pase buena noche, {tratamiento}. Apagando sistemas.",
                    f"Descanse, {tratamiento}. Aquí estaré cuando me necesite."]
    else:
        opciones = [f"Hasta luego, {tratamiento}. Apagando sistemas.",
                    f"Ha sido un placer, {tratamiento}. Cerrando sesión.",
                    f"Hasta pronto, {tratamiento}. Aquí estaré cuando me necesite."]
    return random.choice(opciones)


def _log(msg: str):
    """Diagnóstico de la autodestrucción (la app corre sin consola)."""
    linea = f"{time.strftime('%H:%M:%S')} {msg}"
    print(f"[autodestruccion] {linea}")
    try:
        with open(LOG_DESTRUCCION, "a", encoding="utf-8") as f:
            f.write(linea + "\n")
    except OSError:
        pass


class Assistant:
    def __init__(self, cfg: dict, emit):
        """emit(tipo, datos) envía eventos a la interfaz."""
        self.cfg = cfg
        self.emit = emit
        self.state = "booting"
        self._busy = threading.Lock()
        self._continuous = threading.Event()
        self._last_level = 0.0
        self._last_level_t = 0.0
        self._destruct = threading.Event()        # cuenta atrás en marcha
        self._destruct_abort = threading.Event()  # orden de cancelarla
        self._destruct_lock = threading.Lock()
        self._oido: list[tuple[float, str]] = []  # trozos transcritos durante la cuenta atrás
        # Los trozos grabados en la cuenta atrás se transcriben de uno en uno: en paralelo se pisan en la CPU.
        self._cola_cancelacion: queue.Queue = queue.Queue()
        threading.Thread(target=self._trabajador_cancelacion, daemon=True).start()

        self.stats = SystemStats()
        self.apps = AppIndex(cfg.get("aliases_apps", {}))
        self.memoria = Memoria()
        self.memoria.on_change = lambda lista: self.emit("memoria", lista)
        self.activaciones = self._cargar_activaciones()
        self.llm = LLM(cfg, self.memoria)
        self.tts = TTS(cfg, on_speaking=self._on_speaking, on_level=self._level)
        self.listener = Listener(cfg, on_level=self._level)
        self.activador = None  # detector local de «Jarvis» (se carga al activar la escucha continua)
        self.commands = Commands(cfg, self.apps, self.stats, self.memoria,
                                 on_clear=self._clear_history, on_stop_voice=self.tts.stop)

        threading.Thread(target=self._boot, daemon=True).start()

    # --- estado / eventos ---------------------------------------------
    def set_state(self, state: str):
        self.state = state
        self.emit("state", state)

    def _on_speaking(self, speaking: bool):
        if speaking:
            self.set_state("speaking")
        elif self.state == "speaking":
            self.set_state("listening_wake" if self._continuous.is_set() else "idle")

    def _clear_history(self):
        """Olvida la conversación del modelo y vacía el registro de mensajes de la interfaz."""
        self.llm.reset()
        self.emit("clear", None)

    def _level(self, v: float):
        now = time.time()
        if now - self._last_level_t > 0.06 or (v == 0 and self._last_level != 0):
            self._last_level, self._last_level_t = v, now
            self.emit("level", round(v, 3))

    def _boot(self):
        self.set_state("booting")
        llm_ok = self.llm.available()
        self.emit("status", {"llm": llm_ok, "model": self.cfg["modelo_llm"]})
        if llm_ok:
            self.llm.warmup()
        self.listener.ready.wait()
        self.emit("status", {"stt": True, "llm": llm_ok, "model": self.cfg["modelo_llm"]})
        self.set_state("idle")
        msg = saludo_inicial(self.cfg["tratamiento"])
        if not llm_ok:
            msg += " Aunque no consigo conectar con Ollama; solo podré ejecutar órdenes."
        self.say(msg)
        if self.cfg.get("escucha_continua"):
            self.set_continuous(True)

    def say(self, texto: str):
        if texto:
            self.emit("assistant", texto)
            self.tts.say(texto)

    # --- entrada de texto ----------------------------------------------
    def handle_text(self, texto: str, por_voz: bool = False):
        texto = (texto or "").strip()
        if not texto:
            return
        if self._destruct.is_set():
            # Durante la autodestrucción solo se obedece el código de cancelación.
            if not por_voz:
                self.emit("user", texto)
            if es_cancelacion(texto):
                self._abortar_autodestruccion()
            return
        if not por_voz:
            self.emit("user", texto)
        self.tts.stop()
        if es_autodestruccion(texto):
            self._iniciar_autodestruccion()
            return
        if self.commands.es_despedida(texto):
            self._despedirse()
            return
        with self._busy:
            try:
                respuesta = self.commands.ejecutar(texto)
            except Exception as e:
                respuesta = f"Ha habido un error ejecutando la orden: {e}"
            if respuesta is not None:
                self.emit("action", True)
                self.say(respuesta)
                if not respuesta and self.state != "speaking":
                    self.set_state("idle")
                return
            self._chat(texto)

    def _acciones(self, texto: str) -> tuple[bool, str]:
        """Pregunta al modelo si el mensaje pide acciones y las ejecuta.

        Devuelve (se ejecutó algo, datos para que el modelo los comente). Si no hay datos que comentar,
        el resultado de la acción ya se ha dicho tal cual: así nunca afirma algo que no ha pasado.
        """
        try:
            llamadas = self.commands.validar_llamadas(texto, self.llm.decidir(texto, self.commands.herramientas()))
        except Exception as e:
            print(f"[llm] decisión: {e}")
            return False, ""
        if not llamadas:
            return False, ""
        resultados, consulta = [], False
        for nombre, args in llamadas:
            try:
                r = self.commands.ejecutar_herramienta(nombre, args)
            except Exception as e:
                r = f"Ha habido un error ejecutando la orden: {e}"
            print(f"[llm] {nombre}({args}) -> {r}")
            resultados.append(r or "")
            consulta |= nombre in HERRAMIENTAS_CONSULTA
        self.emit("action", True)
        respuesta = " ".join(r for r in resultados if r)
        if consulta:
            return True, f"Datos reales medidos ahora mismo en el equipo: {respuesta}"
        self.llm.anotar(texto, respuesta)
        self.say(respuesta)
        if self.state != "speaking":
            self.set_state("listening_wake" if self._continuous.is_set() else "idle")
        return True, ""

    def _chat(self, texto: str):
        self.set_state("thinking")
        hecho, contexto = self._acciones(texto)
        if hecho and not contexto:
            return
        self.emit("assistant_start", None)
        buffer, completo = "", ""
        try:
            for token in self.llm.stream(texto, contexto):
                if self._destruct.is_set():
                    buffer = ""
                    break
                buffer += token
                completo += token
                self.emit("assistant_token", token)
                # Envía a la voz cada frase completa en cuanto llega.
                while True:
                    m = FIN_FRASE.match(buffer)
                    if not m or len(m.group(1)) < 12:
                        break
                    self.tts.say(m.group(1))
                    buffer = buffer[m.end():]
            if buffer.strip() and not self._destruct.is_set():
                self.tts.say(buffer)
        except Exception as e:
            err = f"No puedo contactar con el modelo local, {self.cfg['tratamiento']}. ¿Está Ollama en marcha?"
            print(f"[llm] {e}")
            self.emit("assistant_token", err)
            self.tts.say(err)
        self.emit("assistant_end", completo)
        if not self.tts.busy and not self._destruct.is_set():
            self.set_state("listening_wake" if self._continuous.is_set() else "idle")

    # --- voz -------------------------------------------------------------
    def listen_once(self):
        """Pulsar el botón / atajo: escucha una orden y la ejecuta."""
        if self._continuous.is_set():
            return
        if self.state == "listening":
            self.listener.cancel.set()
            return
        threading.Thread(target=self._listen_once, daemon=True).start()

    def _listen_once(self):
        self.tts.stop()
        self.set_state("listening")
        self.emit("beep", "start")
        try:
            audio = self.listener.record(start_timeout=7)
        except Exception as e:
            self.emit("error", f"Error con el micrófono: {e}")
            self.set_state("idle")
            return
        if audio is None:
            self.set_state("idle")
            return
        self._process_audio(audio, requiere_activacion=False)

    def _process_audio(self, audio, requiere_activacion: bool):
        self.set_state("transcribing")
        texto = self.listener.transcribe(audio)
        if not texto:
            self.set_state("listening_wake" if self._continuous.is_set() else "idle")
            return
        if requiere_activacion:
            activado, resto = self.commands.quitar_activacion(texto)
            if not activado:
                self.set_state("listening_wake")
                return
            self.emit("user", texto)
            if len(resto.split()) == 0:
                texto = self._pedir_orden()
                if not texto:
                    return
        else:
            self.emit("user", texto)
        self.handle_text(texto, por_voz=True)

    def _pedir_orden(self) -> str | None:
        """Solo dijo «Jarvis»: responde y espera la orden."""
        self.say(f"¿Sí, {self.cfg['tratamiento']}?")
        self.tts.wait()
        self.set_state("listening")
        self.emit("beep", "start")
        audio = self.listener.record(start_timeout=6)
        if audio is None:
            self.set_state("listening_wake")
            return None
        self.set_state("transcribing")
        texto = self.listener.transcribe(audio)
        if not texto:
            self.set_state("listening_wake")
            return None
        self.emit("user", texto)
        return texto

    # --- palabra de activación local ------------------------------------
    PREVIO_S = 1.6     # audio anterior a la detección que se pasa a Whisper (para que oiga «Jarvis»)
    MUY_SEGURO = 1.01  # con esta puntuación se aceptaría la orden sin que Whisper oiga «Jarvis» (>1: siempre confirma)

    def _activador_local(self):
        """El detector de «Jarvis»; None si no se puede usar (entonces se activa con Whisper, como antes)."""
        if not wakeword.disponible(self.cfg):
            return None
        if self.activador is None:
            try:
                self.activador = wakeword.Activador(self.cfg)
                print("[activacion] Detector local de «Jarvis» cargado")
            except Exception as e:
                print(f"[activacion] No se pudo cargar el detector local: {e}")
                self.cfg["activacion_local"] = False
                return None
        return self.activador

    @staticmethod
    def _cargar_activaciones() -> list[dict]:
        try:
            lineas = LOG_ACTIVACIONES.read_text(encoding="utf-8").splitlines()[-MAX_ACTIVACIONES:]
            return [json.loads(l) for l in lineas if l.strip()]
        except FileNotFoundError:
            return []
        except Exception as e:
            print(f"[activacion] No se pudo leer {LOG_ACTIVACIONES.name}: {e}")
            return []

    def _registrar_activacion(self, pico: float, resultado: str, texto: str):
        """Guarda cada vez que saltó el detector y qué pasó después, para medir el modelo en uso real."""
        a = {"hora": time.strftime("%Y-%m-%d %H:%M:%S"), "puntuacion": round(pico, 3),
             "umbral": round(float(self.cfg.get("umbral_activacion", 0.5)), 2), "resultado": resultado,
             "texto": texto or ""}
        self.activaciones = (self.activaciones + [a])[-MAX_ACTIVACIONES:]
        try:
            with open(LOG_ACTIVACIONES, "a", encoding="utf-8") as f:
                f.write(json.dumps(a, ensure_ascii=False) + "\n")
        except OSError:
            pass
        self.emit("activation", a)

    def _esperar_palabra(self, act):
        """Escucha con el detector hasta oír «Jarvis» y graba la orden sin cerrar el micrófono.

        Devuelve (audio previo con la palabra, audio de la orden o None, puntuación máxima) o None si se
        interrumpe la escucha (se apaga el modo continuo, autodestrucción...).
        """
        n_previo = int(self.PREVIO_S * 16000 / wakeword.TROZO)
        previo: list[np.ndarray] = []
        act.reset()
        self.listener.cancel.clear()
        en_pausa = False
        pico_ventana, t_envio = 0.0, 0.0  # la puntuación se manda a la interfaz ~10 veces por segundo
        try:
            with sd.InputStream(samplerate=16000, channels=1, dtype="float32", device=self.listener.device,
                                blocksize=wakeword.TROZO) as stream:
                while self._continuous.is_set() and not self._destruct.is_set() and not self.listener.cancel.is_set():
                    chunk = stream.read(wakeword.TROZO)[0][:, 0].copy()
                    if self.tts.busy or self._busy.locked():
                        # Mientras habla o trabaja no escucha: no debe activarse con su propia voz.
                        if not en_pausa:
                            self.emit("wake", {"p": 0.0, "u": act.umbral})
                        en_pausa, previo = True, []
                        self._level(0.0)
                        continue
                    if en_pausa:
                        act.reset()
                        en_pausa = False
                    previo = (previo + [chunk])[-n_previo:]
                    self._level(min(1.0, float(np.sqrt(np.mean(chunk ** 2))) * 12))
                    p = act.puntuar(chunk)
                    pico_ventana = max(pico_ventana, p)
                    if time.time() - t_envio > 0.1 or p >= act.umbral:
                        self.emit("wake", {"p": round(pico_ventana, 3), "u": act.umbral})
                        pico_ventana, t_envio = 0.0, time.time()
                    if p < act.umbral:
                        continue
                    # ¡Jarvis! Unos trozos más para ver hasta dónde sube la puntuación, y luego la orden.
                    # Sin pitido todavía: solo se avisa cuando Whisper confirme que era «Jarvis».
                    pico = p
                    for _ in range(3):
                        c = stream.read(wakeword.TROZO)[0][:, 0].copy()
                        previo.append(c)
                        pico = max(pico, act.puntuar(c))
                    orden = self.listener.record(start_timeout=1.2, max_seconds=6, silence_s=0.9, stream=stream)
                    print(f"[activacion] «Jarvis» detectado (puntuación {pico:.2f})")
                    return np.concatenate(previo), orden, pico
        finally:
            self.emit("wake", None)  # se oculta el indicador: ya no escucha con el detector
        return None

    def _tras_palabra(self, previo, orden, pico):
        self.set_state("transcribing")
        audio = previo if orden is None else np.concatenate([previo, orden])
        texto = self.listener.transcribe(audio)
        activado, resto = self.commands.quitar_activacion(texto) if texto else (False, "")
        if not activado and pico < self.MUY_SEGURO:
            # Ni Whisper oye «Jarvis» ni el detector está muy seguro: probablemente era la tele.
            print(f"[activacion] descartado: «{texto}» (puntuación {pico:.2f})")
            self._registrar_activacion(pico, "descartada", texto)
            self.set_state("listening_wake")
            return
        self._registrar_activacion(pico, "confirmada" if activado else "aceptada", texto)
        if orden is None or not resto.strip():
            texto = self._pedir_orden()
            if not texto:
                return
        else:
            self.emit("beep", "start")
            if not activado:
                texto = f"{self.cfg.get('palabra_activacion', 'jarvis').capitalize()}, {texto}"
            self.emit("user", texto)
        self.handle_text(texto, por_voz=True)

    def set_continuous(self, on: bool):
        if on and not self._continuous.is_set():
            self._continuous.set()
            threading.Thread(target=self._continuous_loop, daemon=True).start()
        elif not on:
            self._continuous.clear()
            self.listener.cancel.set()
            if self.state == "listening_wake":
                self.set_state("idle")
        self.emit("continuous", on)

    def _continuous_loop(self):
        self.listener.ready.wait()
        self.set_state("listening_wake")
        while self._continuous.is_set():
            act = None if self._destruct.is_set() else self._activador_local()
            if act:
                try:
                    oido = self._esperar_palabra(act)
                except Exception as e:
                    self.emit("error", f"Error con el micrófono: {e}")
                    time.sleep(2)
                    continue
                if oido and self._continuous.is_set():
                    self._tras_palabra(*oido)
                    if not self._destruct.is_set():
                        self.tts.wait()
                continue
            destruct = self._destruct.is_set()
            try:
                # En autodestrucción se escucha en trozos cortos.
                audio = self.listener.record(
                    start_timeout=None, max_seconds=4 if destruct else 12, silence_s=0.6 if destruct else 0.9,
                    should_pause=lambda: self.tts.busy or (not self._destruct.is_set() and self._busy.locked()))
            except Exception as e:
                self.emit("error", f"Error con el micrófono: {e}")
                time.sleep(2)
                continue
            if audio is None or not self._continuous.is_set():
                continue
            if self._destruct.is_set():
                _log(f"escucha continua: grabado {len(audio) / 16000:.1f}s")
                self._cola_cancelacion.put((audio, time.monotonic()))
            else:
                self._process_audio(audio, requiere_activacion=True)
                if not self._destruct.is_set():
                    self.tts.wait()
        self.set_state("idle")

    def _despedirse(self):
        """Se despide y, cuando termina de hablar, pide a la interfaz que cierre la aplicación."""
        self._continuous.clear()  # que no se oiga a sí mismo mientras se despide
        self.listener.cancel.set()
        self.say(despedida(self.cfg["tratamiento"]))
        time.sleep(0.3)  # deja que la voz arranque antes de comprobar si sigue hablando
        self.tts.wait(timeout=15)
        time.sleep(0.5)
        self.emit("quit", None)

    # --- autodestrucción -------------------------------------------------
    @property
    def autodestruccion(self) -> bool:
        return self._destruct.is_set()

    def _iniciar_autodestruccion(self):
        with self._destruct_lock:
            if self._destruct.is_set():
                return
            self._destruct_abort.clear()
            self._oido = []
            self._destruct.set()
        self.tts.stop()
        self.listener.cancel.set()  # corta la escucha en curso; se retoma en modo autodestrucción
        self.emit("selfdestruct", {"active": True, "n": None})
        _log(f"=== autodestrucción iniciada (t={time.monotonic():.1f}, escucha continua: {self._continuous.is_set()})")
        threading.Thread(target=self._cuenta_atras, daemon=True).start()
        if not self._continuous.is_set():
            threading.Thread(target=self._vigilar_cancelacion, daemon=True).start()

    def _cuenta_atras(self):
        self.say("Código de comando aceptado. Secuencia de autodestrucción iniciada.")
        while self.tts.busy and not self._destruct_abort.wait(0.05):
            pass
        inicio = time.monotonic()
        for n in range(10, -1, -1):
            if self._destruct_abort.is_set():
                return
            self.emit("selfdestruct", {"active": True, "n": n})
            # Los números no se dicen en voz alta (la interfaz pita): el micrófono los oiría y taparían la orden.
            _log(f"cuenta {n} (t={time.monotonic():.1f})")
            espera = inicio + (11 - n) - time.monotonic()
            if self._destruct_abort.wait(max(0.0, espera)):
                return
        # Antes de suspender, da tiempo a que se transcriba lo ya grabado: la orden puede estar ahí.
        limite = time.monotonic() + 8
        while self._cola_cancelacion.unfinished_tasks and time.monotonic() < limite:
            if self._destruct_abort.wait(0.1):
                return
        with self._destruct_lock:
            if self._destruct_abort.is_set():
                return
            self._destruct.clear()
        _log(f"=== cuenta terminada sin cancelar; suspendiendo (pendientes: {self._cola_cancelacion.unfinished_tasks})")
        # Se restaura todo antes de suspender para que al despertar el equipo esté normal.
        self.listener.cancel.set()
        self.emit("selfdestruct", {"active": False})
        self.set_state("listening_wake" if self._continuous.is_set() else "idle")
        media.suspender_equipo()

    def _abortar_autodestruccion(self):
        with self._destruct_lock:
            if not self._destruct.is_set():
                return
            self._destruct_abort.set()
            self._destruct.clear()
        _log("=== autodestrucción CANCELADA")
        self.tts.stop()
        self.listener.cancel.set()
        self.emit("selfdestruct", {"active": False, "aborted": True})
        self.emit("action", True)
        self.say(f"Autodestrucción cancelada. Todos los sistemas a salvo, {self.cfg['tratamiento']}.")

    def _vigilar_cancelacion(self):
        """Escucha el código de cancelación cuando la escucha continua está desactivada."""
        self.listener.ready.wait()
        while self._destruct.is_set():
            try:
                audio = self.listener.record(start_timeout=2, max_seconds=4, silence_s=0.6,
                                             should_pause=lambda: self.tts.busy)
            except Exception as e:
                self.emit("error", f"Error con el micrófono: {e}")
                time.sleep(1)
                continue
            _log(f"vigilancia: {'nada (sin voz)' if audio is None else f'grabado {len(audio) / 16000:.1f}s'}")
            if audio is not None and self._destruct.is_set():
                self._cola_cancelacion.put((audio, time.monotonic()))
        _log("vigilancia terminada")

    def _trabajador_cancelacion(self):
        # Whisper tarda casi lo mismo con 1 s que con 10 s de audio (procesa una ventana fija),
        # así que todo lo que se haya acumulado mientras tanto se transcribe de una sola vez.
        while True:
            trozos = [self._cola_cancelacion.get()]
            while True:
                try:
                    trozos.append(self._cola_cancelacion.get_nowait())
                except queue.Empty:
                    break
            try:
                audio = np.concatenate([a for a, _ in trozos])[-int(16000 * 20):]
                self._comprobar_cancelacion(audio, trozos[-1][1])
            except Exception as e:
                _log(f"error: {e!r}")
            finally:
                for _ in trozos:
                    self._cola_cancelacion.task_done()

    def _comprobar_cancelacion(self, audio, t: float):
        if not self._destruct.is_set():
            return
        t0 = time.monotonic()
        texto = self.listener.transcribe(audio, prompt=PROMPT_CANCELACION)
        _log(f"trozo de {len(audio) / 16000:.1f}s grabado en t={t:.1f}, transcrito en {time.monotonic() - t0:.1f}s "
             f"(en cola: {self._cola_cancelacion.qsize()}): {texto!r}")
        if not texto or not self._destruct.is_set():
            return
        self.emit("user", texto)
        # La frase puede llegar partida en varios trozos: se juntan los de los últimos segundos.
        with self._destruct_lock:
            self._oido.append((t, texto))
            self._oido = [(ti, tx) for ti, tx in self._oido if t - ti < 8]
            reciente = " ".join(tx for _, tx in sorted(self._oido))
        if es_cancelacion(reciente):
            self._abortar_autodestruccion()

    def stop_all(self):
        self.tts.stop()
        self.listener.cancel.set()
