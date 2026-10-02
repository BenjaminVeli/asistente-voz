"""Cliente de Ollama local: decide qué herramienta usar y conversa (en streaming) con memoria corta.

Con un modelo pequeño, la personalidad de JARVIS («responde breve, se lee en voz alta») hace que conteste
con texto en vez de llamar a herramientas. Por eso son dos pasos: `decidir` usa un prompt mínimo solo para
elegir acción, y `stream` conversa con la personalidad completa cuando no hay acción que hacer.
"""
import datetime
import json
import re

import requests

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre"]

PROMPT_DECISION = (
    "Controlas el PC del usuario. Si su mensaje pide una acción en el PC que una herramienta puede hacer, aunque lo "
    "diga de forma indirecta o vaga, llama a esa herramienta eligiendo tú valores concretos. "
    "Usa recordar_dato solo cuando el usuario AFIRMA un dato personal nuevo; nunca cuando pregunta. "
    "Usa buscar_en_web solo si pide expresamente buscar algo o ver vídeos. "
    "Las preguntas, explicaciones, la charla y los chistes NO necesitan herramienta: en ese caso responde únicamente: NO")

PARAM_TEXTO = re.compile(r"""(\w+)\s*[:=]\s*(?:<\|"\|>|["'])(.*?)(?:<\|"\|>|["'])""")
PARAM_NUMERO = re.compile(r"(\w+)\s*[:=]\s*(-?\d+)")


def fecha_hablada(now: datetime.datetime | None = None) -> str:
    now = now or datetime.datetime.now()
    return f"{DIAS[now.weekday()]} {now.day} de {MESES[now.month - 1]} de {now.year}"


def _llamada_en_texto(texto: str, herramientas: list[dict]) -> tuple[str, dict] | None:
    """A veces el modelo escribe la llamada como texto: f(a='x'), f{a:<|"|>x<|"|>}, «f a x»."""
    t = texto.strip(" `*\n")
    nombres = sorted((h["function"]["name"] for h in herramientas), key=len, reverse=True)
    nombre = next((n for n in nombres if t.startswith(n)), None)
    if not nombre:
        return None
    resto = t[len(nombre):]
    args = dict(PARAM_TEXTO.findall(resto))
    args.update({k: int(v) for k, v in PARAM_NUMERO.findall(resto) if k not in args})
    if not args:
        # Con un único parámetro, el resto del texto es su valor.
        props = next(h["function"]["parameters"]["properties"] for h in herramientas if h["function"]["name"] == nombre)
        if len(props) == 1:
            clave = next(iter(props))
            valor = re.sub(rf"^\W*{clave}\W*", "", resto).strip(" (){}.:='\"")
            if valor:
                args = {clave: valor}
    return nombre, args


class LLM:
    def __init__(self, cfg: dict, memoria=None):
        self.cfg = cfg
        self.memoria = memoria
        self.url = cfg["ollama_url"].rstrip("/")
        self.model = cfg["modelo_llm"]
        self.history: list[dict] = []

    def system_prompt(self) -> str:
        now = datetime.datetime.now()
        nombre, trato = self.cfg["nombre_asistente"], self.cfg["tratamiento"]
        return (
            f"Eres {nombre}, el asistente personal de inteligencia artificial de tu usuario, al estilo del J.A.R.V.I.S. "
            f"de Iron Man: educado, eficiente, con un toque de humor británico sutil. Llamas al usuario '{trato}'. "
            "Responde SIEMPRE en español y de forma breve (una a tres frases), porque tus respuestas se leen en voz alta. "
            "No uses markdown, listas, asteriscos ni emojis. Si te piden algo largo, resume lo esencial. "
            f"Hoy es {fecha_hablada(now)} y son las {now:%H:%M}. "
            "Funcionas 100% en local en el ordenador del usuario. Las acciones en el PC (abrir y cerrar aplicaciones, "
            "música, volumen, búsquedas, estado del sistema, recordar datos) las ejecuta el sistema antes de que el "
            "mensaje te llegue, y cuando se ejecuta una te llegan sus datos entre corchetes. Si no te llegan, NO se ha "
            "ejecutado nada: nunca digas que has abierto, puesto o hecho algo; si el usuario pedía una acción, "
            "sugiérele cómo pedírtela más claramente."
            + (self.memoria.para_prompt() if self.memoria else "")
        )

    def available(self) -> bool:
        try:
            r = requests.get(f"{self.url}/api/tags", timeout=2)
            return r.ok and any(m["name"].startswith(self.model) for m in r.json().get("models", []))
        except Exception:
            return False

    def modelos(self) -> list[str]:
        """Modelos instalados en Ollama."""
        try:
            r = requests.get(f"{self.url}/api/tags", timeout=2)
            return sorted(m["name"] for m in r.json().get("models", []))
        except Exception:
            return []

    def warmup(self):
        """Carga el modelo en memoria para que la primera respuesta sea rápida."""
        try:
            requests.post(f"{self.url}/api/generate", json={"model": self.model, "keep_alive": "30m"}, timeout=180)
        except Exception as e:
            print(f"[llm] No se pudo precargar el modelo: {e}")

    def reset(self):
        self.history.clear()

    def _recortar(self):
        self.history = self.history[-12:]
        while self.history and self.history[0]["role"] != "user":
            self.history.pop(0)

    def anotar(self, texto: str, respuesta: str):
        """Guarda en la conversación un turno resuelto sin el modelo (p. ej. una acción ejecutada)."""
        self.history += [{"role": "user", "content": texto}, {"role": "assistant", "content": respuesta}]
        self._recortar()

    def decidir(self, texto: str, herramientas: list[dict]) -> list[tuple[str, dict]]:
        """Pregunta al modelo qué herramientas hay que usar para este mensaje (lista vacía si ninguna)."""
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": PROMPT_DECISION}, {"role": "user", "content": texto}],
            "tools": herramientas,
            "stream": False,
            "think": False,
            "keep_alive": "30m",
            "options": {"temperature": 0.1, "num_ctx": 4096, "num_predict": 60},
        }
        r = requests.post(f"{self.url}/api/chat", json=body, timeout=(5, 60))
        if not r.ok:
            raise RuntimeError(r.text[:200])
        msg = r.json().get("message", {})
        llamadas = []
        for c in msg.get("tool_calls") or []:
            args = c["function"].get("arguments") or {}
            llamadas.append((c["function"]["name"], json.loads(args) if isinstance(args, str) else args))
        if not llamadas:
            en_texto = _llamada_en_texto(msg.get("content", ""), herramientas)
            if en_texto:
                llamadas.append(en_texto)
        return llamadas

    def stream(self, texto: str, contexto: str = ""):
        """Genera la respuesta token a token. `contexto` (datos de una herramienta) se añade al mensaje."""
        if contexto:
            texto = f"{texto}\n\n[{contexto} Respóndeme basándote en estos datos.]"
        self.history.append({"role": "user", "content": texto})
        self._recortar()
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": self.system_prompt()}] + self.history,
            "stream": True,
            "think": False,
            "keep_alive": "30m",
            "options": {"temperature": 0.6, "num_ctx": 4096},
        }
        respuesta = ""
        try:
            with requests.post(f"{self.url}/api/chat", json=body, stream=True, timeout=(5, 300)) as r:
                if not r.ok:
                    raise RuntimeError(r.text[:200])
                for line in r.iter_lines():
                    if not line:
                        continue
                    msg = json.loads(line)
                    if msg.get("error"):
                        raise RuntimeError(msg["error"])
                    token = msg.get("message", {}).get("content", "")
                    if token:
                        respuesta += token
                        yield token
                    if msg.get("done"):
                        break
        finally:
            if respuesta:
                self.history.append({"role": "assistant", "content": respuesta})
            else:
                self.history.pop()
