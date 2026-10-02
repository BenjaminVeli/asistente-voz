"""Interpreta órdenes en español y las ejecuta localmente.

Las acciones (abrir apps, música, volumen...) se resuelven con reglas: es instantáneo y
fiable. Lo que no encaja con ninguna regla se envía al modelo de lenguaje, que puede pedir
esas mismas acciones como herramientas (ver Commands.herramientas).
"""
import datetime
import os
import re
from pathlib import Path

from . import media
from .apps import AppIndex, normalize
from .llm import fecha_hablada

RELLENO = re.compile(
    r"^(?:(?:oye|hey|ey|hola|bueno|vale|ok|okay|por favor|porfa|porfavor|puedes|podrias|quiero que|"
    r"necesito que|me|haz el favor de|seria posible|ahora)\s+)+")
FINAL = re.compile(r"(?:\s+(?:por favor|porfa|porfavor|gracias|jarvis|ya|ahora|ahora mismo|rapido))+$")
ARTICULO = r"(?:(?:el|la|los|las|un|una|mi|el programa|la aplicacion|la app|el juego)\s+)?"

NUMEROS = [(re.compile(r"\bdoble cero\b"), "00"), (re.compile(r"\bcero\b"), "0"), (re.compile(r"\bdiez\b"), "10")]


def _compacto(texto: str) -> str:
    """Texto normalizado, con los números en cifras y sin espacios: tolera cómo transcriba Whisper los códigos."""
    t = normalize(texto)
    for patron, cifra in NUMEROS:
        t = patron.sub(cifra, t)
    return t.replace(" ", "")


# destr…, descr…, detr…, distr…: Whisper no siempre acierta con las consonantes.
CODIGO_DESTRUCCION = re.compile(r"00(?:auto)?d[ei]s?[ctr]{1,2}[a-z]*?0")


def es_autodestruccion(texto: str) -> bool:
    """«Autodestrúyete, código de comando 00destruyete0».

    Whisper a menudo cambia el orden o la vocal («código de comando, cero cero, autodestróyete, cero»),
    así que basta con el código: 00 + destr…/descr… + 0.
    """
    return bool(CODIGO_DESTRUCCION.search(_compacto(texto)))


CODIGO_CANCELACION = re.compile(r"codigo(?:de)?(?:funcion|comando)?(?:de)?(?:funcion|comando)?10")


def es_cancelacion(texto: str) -> bool:
    """«Función de comando código 10».

    Basta con «código 10»: Whisper suele perder o deformar «función de comando» con la cuenta atrás de fondo,
    y a veces lo reordena («código de función 10», «código de comando 10»).
    """
    return bool(CODIGO_CANCELACION.search(_compacto(texto)))


RECORDAR = re.compile(
    r"^(?:(?:hey|oye|ey|hola|jarvis|yarvis|por favor)\W+)*(?:quiero que\s+|necesito que\s+)?"
    r"(?:recuerda|recuerde|guarda en (?:tu |la )?memoria|apunta)\s+que\s+(.+?)[.!]*$", re.I)

PREGUNTA = re.compile(r"[¿?]")
INTERROGATIVO = re.compile(r"^(?:(?:jarvis|oye|y)\s+)*(?:que|cual|cuales|cuando|donde|quien|quienes|como|cuanto|cuantos|"
                           r"cuanta|cuantas|sabes|recuerdas|te acuerdas)\b")
PIDE_BUSCAR = re.compile(r"\b(?:busca\w*|googlea\w*|google|youtube|wikipedia|videos?|ver|mira\w*|ensena\w*|muestra\w*)\b")

# Herramientas que devuelven información para que el modelo la comente (las demás se confirman tal cual).
HERRAMIENTAS_CONSULTA = {"estado_del_sistema"}

CARPETAS = {
    "descargas": "Downloads", "documentos": "Documents", "escritorio": "Desktop",
    "imagenes": "Pictures", "fotos": "Pictures", "videos": "Videos", "musica": "Music",
}


class Commands:
    def __init__(self, cfg: dict, apps: AppIndex, stats, memoria, on_clear=None, on_stop_voice=None):
        self.cfg = cfg
        self.apps = apps
        self.stats = stats
        self.memoria = memoria
        self.on_clear = on_clear or (lambda: None)
        self.on_stop_voice = on_stop_voice or (lambda: None)
        w = normalize(cfg.get("palabra_activacion", "jarvis"))
        self.wake = re.compile(rf"^(?:(?:hey|oye|ey|hola)\s+)?(?:{w}|yarvis|charvis|jarbis|harvis|jarvi)\b\s*")
        self.reglas = [
            (r"^(abre|abrir|abreme|ejecuta|inicia|lanza|cierra|cerrar|pon|ponme|reproduce|busca|buscame)$", self.incompleta),
            (r"^(?:callate|calla|deja de hablar|para de hablar|silencio jarvis|basta)$", self.callar),
            # --- Memoria a largo plazo («recuerda que…» se trata aparte, sobre el texto original) ---
            (r"^(?:que sabes (?:de|sobre) mi|que recuerdas (?:de|sobre) mi|que tienes en (?:tu )?memoria|"
             r"que datos tienes (?:de|sobre) mi)$", self.que_sabes),
            (r"^olvida (?:que|lo de|el dato de)\s+(.+)$", self.olvidar),
            (r"^(?:borra|borrar|borrame|limpia|limpiar|limpiame|reinicia|reiniciar|olvida|elimina|eliminar|vacia|vaciar|resetea)\b"
             r".*\b(?:chat|historial|conversacion|conversaciones|memoria|mensaje|mensajes|sesion|pantalla)$"
             r"|^(?:nueva conversacion|empecemos de nuevo|empieza de cero|borron y cuenta nueva)$", self.limpiar),
            (r"^(?:que hora es|dime la hora|la hora|que horas son)$", self.hora),
            (r"^(?:que (?:dia|fecha) es(?: hoy)?|a que (?:dia|fecha) estamos|dime la fecha|la fecha)$", self.fecha),
            (r"^(?:(?:como (?:esta|va)|estado|diagnostico|informe|uso) (?:del |de la |de )?"
             r"(?:sistema|equipo|ordenador|pc|computadora|cpu|procesador|ram|memoria|gpu|grafica|tarjeta grafica)"
             r"|como (?:esta|va) (?:el|la|mi) (?:sistema|equipo|ordenador|pc|computadora|cpu|ram|gpu))$", self.sistema),
            (r"^(?:bloquea|bloquear)\s+(?:el |la |mi )?(?:equipo|ordenador|pc|computadora|pantalla|sesion)$", self.bloquear),
            # --- Volumen ---
            (r"^(?:pon|ponme|sube|baja|ajusta|fija|deja)?\s*(?:el )?volumen (?:al|a|en)\s+(\d{1,3})(?:\s*(?:%|por ciento))?$", self.volumen_fijo),
            (r"^(?:sube|subir|aumenta|mas)\s+(?:el |un poco el )?(?:volumen|sonido)(?: un poco| mucho)?$|^mas alto$|^volumen arriba$", self.volumen_arriba),
            (r"^(?:baja|bajar|disminuye|reduce|menos)\s+(?:el |un poco el )?(?:volumen|sonido)(?: un poco| mucho)?$|^mas bajo$|^volumen abajo$", self.volumen_abajo),
            (r"^(?:silencia|mutea|silencio|quita el sonido|activa el sonido|desmutea)(?: el (?:volumen|sonido|equipo))?$", self.mute),
            # --- Reproducción ---
            (r"^(?:pausa|pausar|pausa la musica|pausa la cancion|para la musica|deten la musica|detén la musica|"
             r"continua|continuar|reanuda|reanudar|sigue|play|dale play|reproducir|reanuda la musica|"
             r"continua la musica|quita la pausa)$", self.play_pause),
            (r"^(?:siguiente|siguiente cancion|pasa(?: la)? cancion|salta(?: la)? cancion|cambia(?: la)? cancion|"
             r"otra cancion|pon la siguiente|next|siguiente tema)$", self.siguiente),
            (r"^(?:anterior|cancion anterior|la anterior|pon la anterior|vuelve a la anterior|tema anterior|atras)$", self.anterior),
            # --- Búsquedas web ---
            (r"^(?:busca|buscar|buscame|pon|ponme|reproduce)\s+(.+?)\s+en\s+(youtube|google|wikipedia)$", self.buscar_web),
            (r"^(?:busca|buscar|buscame)\s+en\s+(youtube|google|wikipedia)\s+(.+)$", self.buscar_web_inv),
            # --- Spotify ---
            (r"^(?:pon|ponme|reproduce|reproduceme|toca|busca|buscame|quiero escuchar|escuchar)\s+"
             r"(?:musica|algo de musica|una cancion|canciones)$", self.spotify_musica),
            (r"^(?:pon|ponme|reproduce|reproduceme|toca|quiero escuchar|busca en spotify|buscame en spotify)\s+"
             r"(?:(?:musica|canciones|la cancion|el tema|el album|la playlist|algo) (?:de |del )?)?(.+?)"
             r"(?:\s+en spotify)?$", self.spotify),
            (r"^(?:busca|buscame)\s+(.+?)\s+en spotify$", self.spotify),
            # --- Apps y carpetas ---
            (rf"^(?:abre|abrir|abreme|ejecuta|inicia|lanza|arranca|abre me)\s+(?:la )?carpeta (?:de )?(?:mis )?(\w+)$", self.carpeta),
            (rf"^(?:abre|abrir|abreme|ejecuta|inicia|lanza|arranca|abre me)\s+{ARTICULO}(.+)$", self.abrir),
            (rf"^(?:cierra|cerrar|cierrame|mata|termina|finaliza)\s+{ARTICULO}(.+)$", self.cerrar),
            (r"^(?:busca|buscar|buscame|googlea)\s+(.+)$", lambda q: self.buscar_web(q, "google")),
        ]
        self.reglas = [(re.compile(p), f) for p, f in self.reglas]

    # ------------------------------------------------------------------
    def quitar_activacion(self, texto: str) -> tuple[bool, str]:
        """Devuelve (tenía palabra de activación, resto del texto normalizado)."""
        t = normalize(texto)
        m = self.wake.match(t)
        return (bool(m), t[m.end():] if m else t)

    def ejecutar(self, texto: str) -> str | None:
        """Ejecuta la orden si coincide con alguna regla. Devuelve la respuesta o None si no es una orden."""
        # «Recuerda que…» se guarda tal cual lo dijo (con tildes y mayúsculas), no normalizado.
        m = RECORDAR.match(texto.strip())
        if m:
            return self.recordar(m.group(1))
        _, t = self.quitar_activacion(texto)
        t = RELLENO.sub("", t).strip()
        t = FINAL.sub("", t).strip()
        for patron, accion in self.reglas:
            m = patron.match(t)
            if m:
                return accion(*[g for g in m.groups() if g is not None])
        return None

    @property
    def trato(self):
        return self.cfg.get("tratamiento", "señor")

    # --- acciones -----------------------------------------------------
    def incompleta(self, verbo):
        que = {"abre": "abra", "abrir": "abra", "abreme": "abra", "ejecuta": "ejecute", "inicia": "inicie",
               "lanza": "lance", "cierra": "cierre", "cerrar": "cierre", "busca": "busque", "buscame": "busque"}
        return f"¿Qué quiere que {que.get(verbo, 'ponga')}, {self.trato}?"

    def callar(self):
        self.on_stop_voice()
        return ""

    def recordar(self, dato):
        r = self.memoria.recordar(dato)
        return f"Entendido, {self.trato}. Lo recordaré." if r == "Dato guardado en memoria." else r

    def olvidar(self, dato):
        return self.memoria.olvidar(dato)

    def que_sabes(self):
        datos = self.memoria.resumen()
        if not datos:
            return f"Aún no me ha pedido recordar nada, {self.trato}. Diga «recuerda que…» y lo guardaré."
        return f"Esto es lo que recuerdo, {self.trato}: " + ". ".join(datos) + "."

    def limpiar(self):
        self.on_clear()
        return f"Memoria de conversación borrada, {self.trato}."

    def hora(self):
        now = datetime.datetime.now()
        return f"Son las {now.hour}:{now.minute:02d}, {self.trato}."

    def fecha(self):
        return f"Hoy es {fecha_hablada()}."

    def sistema(self):
        return self.stats.resumen_hablado()

    def bloquear(self):
        media.bloquear_equipo()
        return "Equipo bloqueado."

    def volumen_fijo(self, n):
        media.fijar_volumen(int(n))
        return f"Volumen al {min(int(n), 100)} por ciento."

    def volumen_arriba(self):
        media.subir_volumen(5)
        return "Subiendo volumen."

    def volumen_abajo(self):
        media.bajar_volumen(5)
        return "Bajando volumen."

    def mute(self):
        media.silenciar()
        return "Hecho."

    def play_pause(self):
        media.play_pause()
        return "Hecho."

    def siguiente(self):
        media.siguiente()
        return "Siguiente canción."

    def anterior(self):
        media.anterior()
        return "Canción anterior."

    def buscar_web(self, consulta, sitio="google"):
        media.buscar_web(consulta, sitio)
        return f"Buscando {consulta} en {sitio.capitalize()}."

    def buscar_web_inv(self, sitio, consulta):
        return self.buscar_web(consulta, sitio)

    def spotify_musica(self):
        media.spotify_abrir()
        return f"Abriendo Spotify, {self.trato}. Dime qué quieres escuchar."

    def spotify(self, consulta):
        if consulta in ("spotify", "musica en spotify"):
            return self.spotify_musica()
        media.spotify_buscar(consulta)
        return f"Reproduciendo {consulta} en Spotify."

    def carpeta(self, nombre):
        sub = CARPETAS.get(nombre)
        if not sub:
            return None
        ruta = Path.home() / sub
        if not ruta.exists():  # carpetas redirigidas a OneDrive
            ruta = Path.home() / "OneDrive" / sub
        os.startfile(ruta)
        return f"Abriendo {nombre}."

    def abrir(self, nombre):
        nombre = nombre.strip()
        if nombre in CARPETAS:
            return self.carpeta(nombre)
        encontrado = self.apps.find(nombre)
        if encontrado:
            titulo, appid = encontrado
            self.apps.launch(appid)
            return f"Abriendo {titulo}, {self.trato}."
        url = media.SITIOS.get(nombre.replace(" ", "")) or media.SITIOS.get(nombre)
        if url:
            media.abrir_web(url)
            return f"Abriendo {nombre}."
        return f"No encuentro ninguna aplicación llamada {nombre}, {self.trato}."

    def cerrar(self, nombre):
        n = self.apps.close(nombre)
        if n:
            return f"{nombre.capitalize()} cerrado."
        return f"No encuentro {nombre} en ejecución."

    # --- herramientas para el modelo de lenguaje ----------------------
    # Lo que no encaja con ninguna regla llega al modelo, que puede pedir estas acciones
    # aunque el usuario las exprese de forma indirecta («ponme algo para concentrarme»).
    def herramientas(self) -> list[dict]:
        apps = sorted(set(self.cfg.get("accesos_rapidos", [])) | set(self.cfg.get("aliases_apps", {}).values()))

        def f(nombre, descripcion, props=None, requeridos=None):
            return {"type": "function", "function": {
                "name": nombre, "description": descripcion,
                "parameters": {"type": "object", "properties": props or {}, "required": requeridos or []}}}

        texto = lambda d: {"type": "string", "description": d}
        return [
            f("abrir_aplicacion", "Abre una aplicación instalada en el PC o una web conocida. Usa el nombre real del "
              f"programa, no una descripción. Algunas instaladas: {', '.join(apps)}.",
              {"nombre": texto("nombre real de la aplicación, p. ej. 'Visual Studio Code'")}, ["nombre"]),
            f("cerrar_aplicacion", "Cierra una aplicación que está en ejecución.",
              {"nombre": texto("nombre de la aplicación")}, ["nombre"]),
            f("abrir_carpeta", "Abre una carpeta personal del usuario en el explorador.",
              {"nombre": {"type": "string", "enum": sorted(CARPETAS)}}, ["nombre"]),
            f("reproducir_musica", "Busca y reproduce música en Spotify. Si la petición es vaga (un estado de ánimo, "
              "una actividad), elige tú una búsqueda concreta y adecuada, p. ej. 'lofi para concentrarse'.",
              {"consulta": texto("canción, artista, álbum, género o playlist")}, ["consulta"]),
            f("controlar_reproduccion", "Controla la música o el vídeo que suena.",
              {"accion": {"type": "string", "enum": ["pausar_o_reanudar", "siguiente", "anterior"]}}, ["accion"]),
            f("ajustar_volumen", "Cambia el volumen del equipo.",
              {"accion": {"type": "string", "enum": ["subir", "bajar", "fijar", "silenciar_o_activar"]},
               "nivel": {"type": "integer", "description": "0-100, solo con accion 'fijar'"}}, ["accion"]),
            f("buscar_en_web", "Abre en el navegador una búsqueda en Google, YouTube o Wikipedia.",
              {"consulta": texto("lo que hay que buscar"),
               "sitio": {"type": "string", "enum": ["google", "youtube", "wikipedia"]}}, ["consulta"]),
            f("estado_del_sistema", "Devuelve el uso actual de CPU, RAM y GPU del equipo."),
            f("bloquear_equipo", "Bloquea la sesión de Windows. Solo si el usuario lo pide explícitamente."),
            f("recordar_dato", "Guarda en la memoria a largo plazo un dato sobre el usuario o algo que quiere que "
              "recuerdes. Úsalo cuando el usuario te cuente algo personal que convenga recordar o te lo pida.",
              {"dato": texto("el dato, redactado en primera persona del usuario, p. ej. 'Mi cumpleaños es el 3 de mayo'")},
              ["dato"]),
            f("olvidar_dato", "Borra de la memoria a largo plazo un dato que el usuario ya no quiere que recuerdes.",
              {"dato": texto("palabras clave del dato a olvidar")}, ["dato"]),
        ]

    def ejecutar_herramienta(self, nombre: str, args: dict) -> str:
        a = {k: (v.strip() if isinstance(v, str) else v) for k, v in (args or {}).items()}
        if nombre == "abrir_aplicacion":
            return self.abrir(normalize(a["nombre"]))
        if nombre == "cerrar_aplicacion":
            return self.cerrar(normalize(a["nombre"]))
        if nombre == "abrir_carpeta":
            return self.carpeta(normalize(a["nombre"])) or f"No conozco la carpeta {a['nombre']}."
        if nombre == "reproducir_musica":
            return self.spotify(a["consulta"])
        if nombre == "controlar_reproduccion":
            return {"siguiente": self.siguiente, "anterior": self.anterior}.get(a["accion"], self.play_pause)()
        if nombre == "ajustar_volumen":
            accion = a.get("accion")
            if accion == "fijar":
                return self.volumen_fijo(int(a["nivel"])) if a.get("nivel") is not None else "Falta el nivel de volumen."
            return {"subir": self.volumen_arriba, "bajar": self.volumen_abajo,
                    "silenciar_o_activar": self.mute}.get(accion, lambda: f"Acción de volumen desconocida: {accion}.")()
        if nombre == "buscar_en_web":
            return self.buscar_web(a["consulta"], a.get("sitio") or "google")
        if nombre == "estado_del_sistema":
            return self.sistema()
        if nombre == "bloquear_equipo":
            return self.bloquear()
        if nombre == "recordar_dato":
            return self.recordar(a["dato"])
        if nombre == "olvidar_dato":
            return self.olvidar(a["dato"])
        return f"La herramienta {nombre} no existe."

    def validar_llamadas(self, texto: str, llamadas: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
        """Descarta llamadas que el modelo pequeño se inventa a menudo."""
        t = normalize(texto)
        nombres = {h["function"]["name"] for h in self.herramientas()}
        validas = []
        for nombre, args in llamadas:
            if nombre not in nombres:
                continue
            # «¿Cuándo es mi cumpleaños?» → recordar_dato con una fecha inventada.
            if nombre == "recordar_dato" and (PREGUNTA.search(texto) or INTERROGATIVO.match(t)):
                continue
            # «¿Quién era Napoleón?» → búsqueda en Wikipedia, cuando debería contestar él.
            if nombre == "buscar_en_web" and not PIDE_BUSCAR.search(t):
                continue
            validas.append((nombre, args))
        return validas
