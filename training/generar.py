"""Paso 1: genera clips sintéticos con Piper para entrenar la palabra de activación «Jarvis».

Positivos: «Jarvis» con varias pronunciaciones reales (xárbis, yárbis, dyárbis, la inglesa...) dichas
por más de mil hablantes sintéticos, a veces precedida o seguida de otras palabras («oye Jarvis»,
«Jarvis, abre Spotify»). Se guarda dónde termina la palabra, que es donde debe dispararse el detector.

Negativos: palabras que suenan parecido (Javier, jardín, Travis, Garbis...), trozos de la palabra
(«jar», «vis») y frases normales en español e inglés, incluidas las respuestas del propio JARVIS.

Uso:  python generar.py [--escala 1.0]
Salida: data/clips/<conjunto>/<voz>_<n>.npz  (audio int16 a 16 kHz)
"""
import argparse
import json
import multiprocessing as mp
import os
import random
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

DATA = Path(__file__).parent / "data"
VOCES = DATA / "voices"
SALIDA = DATA / "clips"
SR = 16000

# --- Pronunciaciones de «Jarvis» (fonemas IPA de espeak) -------------------------
# Cada voz solo dice sonidos de su idioma: con fonemas que no aprendió, Piper produce ruido.
JARVIS = {
    "es": ["xˈaɾβis", "xˈaɾbis", "xˈarβis", "hˈaɾβis", "ʝˈaɾβis", "ʝˈaɾbis", "dʒˈaɾβis", "dʒˈaɾbis",
           "ɟʝˈaɾβis", "jˈaɾβis", "ʃˈaɾβis", "ʒˈaɾβis", "xˈaɾβiz", "dʒˈaɾβiz", "ʝˈaɾβiz"],
    "en": ["dʒˈɑːɹvɪs", "dʒˈɑɹvɪs", "dʒˈɑːvɪs", "dʒˈɑːɹvɪz", "dʒˈaɹvis", "jˈɑːɹvɪs", "hˈɑːɹvɪs",
           "dʒˈɑːɹviːs", "hˈɑːɹviːs", "jˈɑːɹviːs", "dʒˈɑːɹbɪs"],
    "ca": ["dʒˈaɾβis", "ʒˈaɾβis", "ʃˈaɾβis", "jˈaɾβis", "dʒˈarβis"],
    "pt": ["dʒˈaɾvis", "ʒˈaɾvis", "ʒˈahvis", "dʒˈahvis", "ʃˈaɾvis"],
    "it": ["dʒˈarvis", "dʒˈaɾvis", "jˈarvis"],
}
CASI = {
    "es": ["xˈaɾ", "ˈaɾβis", "βˈis", "xˈaɾβa", "xˈaβis", "ɡˈaɾβis", "kˈaɾβis", "mˈaɾβis", "tɾˈaβis",
           "xˈaɾdin", "xaβjˈeɾ", "xˈaɾβo", "pˈaɾβis", "ʝˈaɾ", "dʒˈaɾ", "xˈoɾβis", "xˈeɾβis", "nˈaɾβis",
           "sˈaɾβis", "xˈaɾtis", "xˈaɾmis", "xˈaɾβas", "ɾˈaβis", "lˈaɾβis", "bˈaɾβis"],
    "en": ["dʒˈɑːɹ", "vˈɪs", "ɡˈɑːɹvɪs", "mˈɑːɹvɪs", "tɹˈævɪs", "dʒˈɜːvɪs", "dʒˈɑːɹvɪn", "dʒˈɔːɹvɪs",
           "kˈɑːɹvɪs", "dʒˈɑːɹtɪs", "dʒˈɑːɹmɪs", "pˈɑːɹvɪs", "dʒˈɑːɹvəl", "dʒˈɑːɹɡən"],
    "ca": ["ʒˈaɾ", "ɡˈaɾβis", "mˈaɾβis"],
    "pt": ["ʒˈaɾ", "ɡˈaɾvis", "mˈaɾvis"],
    "it": ["dʒˈar", "ɡˈarvis", "mˈarvis"],
}

# Lo que se dice antes o después de la palabra (texto: lo fonetiza la propia voz).
ANTES_ES = ["oye", "hola", "hey", "bueno", "vale", "eh", "ok", "a ver", "gracias", "perdona", "buenos días",
            "buenas noches", "oye oye", "por favor"]
DESPUES_ES = ["abre Spotify", "qué hora es", "pon música", "sube el volumen", "baja el volumen", "abre Chrome",
              "cómo está el sistema", "buenas noches", "estás ahí", "pausa", "siguiente canción", "busca en Google",
              "abre Discord", "qué tiempo hace", "apágate", "recuerda que", "pon despacito", "abre Steam"]
ANTES_EN = ["hey", "ok", "hello", "hi", "yo", "so", "well"]
DESPUES_EN = ["open spotify", "what time is it", "play some music", "turn it up", "are you there", "lights on"]

# --- Negativos ---------------------------------------------------------------------
PARECIDAS_ES = ["Javier", "jardín", "jarabe", "jarra", "jarras", "Garbis", "Travis", "Marvin", "Darwin", "Harvey",
                "Elvis", "Davis", "Chávez", "carbón", "servis", "yarda", "Yaris", "barbas", "larva", "garbo",
                "Jaime", "Javi", "Jarvi", "hervir", "arvejas", "árbol", "jabón", "Charly", "marfil", "avíos",
                "Harvard", "Jervis", "Ares", "Narvaez", "Carlos", "Paris", "Marcos", "Ramírez", "Andrés",
                "jarabe de arce", "Jorge", "Jesús", "Javiera", "Xavier", "hachís", "parvis", "Iris", "crisis",
                "virus", "tenis", "lápiz", "análisis", "Martes", "Jueves", "Viernes", "barniz", "matiz"]
PARECIDAS_EN = ["Travis", "Marvin", "Davis", "Harvest", "service", "carbs", "jar", "Jervis", "nervous", "Jarrett",
                "Garvey", "Harvey", "Javis", "Elvis", "artist", "starfish", "jarring", "Charles", "Darwin",
                "Jasper", "Jacob", "crisis", "jarvie", "army", "party", "Paris", "garbage", "Jarred"]
FRASES_ES = [
    "Abriendo Spotify, señor.", "Son las diez y media de la noche.", "Reproduciendo despacito en Spotify.",
    "Buenas noches, señor. Que descanse.", "Todos los sistemas en línea.", "¿En qué puedo ayudarle?",
    "El procesador está al treinta por ciento.", "No encuentro ninguna aplicación con ese nombre.",
    "Subiendo volumen.", "Hoy es miércoles uno de octubre.", "Ha sido un placer, señor.",
    "Mañana vamos a ir al mercado a comprar fruta.", "¿Has visto el partido de anoche?",
    "Necesito terminar el informe antes del viernes.", "La reunión empieza a las tres de la tarde.",
    "Javier me dijo que vendría al jardín después de comer.", "¿Dónde dejaste las llaves del coche?",
    "El servidor se cayó otra vez esta mañana.", "Me encanta la música de los ochenta.",
    "Vamos a pedir una pizza para cenar.", "El gato está durmiendo en el sofá.",
    "Tengo que llamar a mi madre luego.", "¿Qué película quieres ver esta noche?",
    "Hace mucho calor para ser octubre.", "El tráfico estaba fatal en la avenida principal.",
    "Pásame el jarabe para la tos, por favor.", "Travis y Marvin llegaron tarde a la clase.",
    "Mi hermano juega al fútbol todos los sábados.", "Abre la ventana que hace calor.",
    "El análisis de datos tardará un par de horas.", "Compré un jarrón nuevo para la sala.",
    "¿Puedes bajar el volumen de la tele?", "Ese videojuego tiene unos gráficos increíbles.",
    "La señal del wifi es muy débil en mi cuarto.", "Estoy aprendiendo a programar en Python.",
    "El médico dijo que descanse unos días.", "Vamos a la playa el fin de semana.",
    "La cena está lista, ven a la mesa.", "Ya terminé la tarea de matemáticas.",
    "¿Sabes dónde queda la farmacia más cercana?", "El jardinero vendrá el jueves por la mañana.",
    "Garbis es un apellido armenio bastante común.", "Las arvejas con arroz me encantan.",
    "Hay que hervir el agua antes de beberla.", "Darwin escribió sobre el origen de las especies.",
    "Harvard es una universidad muy conocida.", "El virus se extendió muy rápido.",
    "Marcos y Andrés están jugando a las cartas.", "No sé si llover hoy o mañana.",
    "Pon la alarma a las siete.", "Sube un poco el volumen de la música.", "Qué hora es ahora mismo.",
    "Abre el navegador y busca recetas.", "Cierra todas las ventanas abiertas.", "Siguiente canción, por favor.",
    "Me voy a dormir, hasta mañana.", "Recuerda que mañana tengo dentista.", "Busca vuelos baratos a Madrid.",
    "Hola, ¿cómo estás?", "Bien, gracias, ¿y tú?", "Oye, ¿me prestas tu cargador?",
    "La batería del portátil está casi vacía.", "Voy a preparar un café, ¿quieres uno?",
    "El examen fue más fácil de lo que pensaba.", "Mi primo Jaime vive en Barcelona.",
    "Charly García es un músico argentino.", "Elvis Presley era el rey del rock.",
    "Chávez ganó la pelea por decisión unánime.", "Los martes y los jueves tengo inglés.",
]
FRASES_EN = [
    "Good evening, sir. All systems are online.", "The weather today is sunny with a light breeze.",
    "I think we should leave a little earlier tomorrow.", "Can you pass me the salt, please?",
    "The meeting has been moved to Thursday afternoon.", "Travis and Marvin are coming over tonight.",
    "I parked the car next to the old garage.", "This song reminds me of last summer.",
    "Harvey bought a new jar of peanut butter.", "My favorite artist is releasing a new album.",
    "The service at that restaurant was excellent.", "We need to buy more garbage bags.",
    "Davis scored the winning goal in the final minute.", "Elvis has left the building.",
    "Turn on the lights in the living room.", "What is the capital of France?",
    "I'll call you back in a few minutes.", "The kids are playing in the garden.",
]


def _cargar_voces():
    from piper import PiperVoice
    voces = []
    for onnx in sorted(VOCES.glob("*.onnx")):
        cfg = json.loads(Path(str(onnx) + ".json").read_text(encoding="utf-8"))
        idioma = cfg["espeak"]["voice"]
        voces.append({"nombre": onnx.stem, "ruta": str(onnx), "ingles": idioma.startswith("en"),
                      "idioma": idioma.split("-")[0],
                      "hablantes": cfg.get("num_speakers", 1), "sr": cfg["audio"]["sample_rate"]})
    return voces


# Hablantes reservados para la prueba (nunca se ven al entrenar).
def es_prueba(voz: str, hablante: int) -> bool:
    if voz in ("es_MX-claude-high",):
        return True
    if voz == "en_US-libritts_r-medium":
        return hablante >= 820
    if voz == "en_GB-vctk-medium":
        return hablante >= 96
    if voz == "en_US-l2arctic-medium":
        return hablante >= 21
    return False


class Sintetizador:
    def __init__(self):
        from piper import PiperVoice
        self.PiperVoice = PiperVoice
        self.voces = _cargar_voces()
        self._cache = {}

    def voz(self, nombre):
        if nombre not in self._cache:
            if len(self._cache) > 3:
                self._cache.pop(next(iter(self._cache)))
            v = next(v for v in self.voces if v["nombre"] == nombre)
            # Un hilo por proceso: con varios procesos a la vez, más hilos solo se estorban.
            import onnxruntime
            from piper.config import PiperConfig
            opciones = onnxruntime.SessionOptions()
            opciones.intra_op_num_threads = opciones.inter_op_num_threads = 1
            sesion = onnxruntime.InferenceSession(v["ruta"], sess_options=opciones, providers=["CPUExecutionProvider"])
            config = PiperConfig.from_dict(json.loads(Path(v["ruta"] + ".json").read_text(encoding="utf-8")))
            self._cache[nombre] = self.PiperVoice(config=config, session=sesion)
        return self._cache[nombre]

    @staticmethod
    def _config(hablante, rng):
        from piper import SynthesisConfig
        return SynthesisConfig(speaker_id=hablante, length_scale=rng.uniform(0.75, 1.35),
                               noise_scale=rng.uniform(0.35, 0.95), noise_w_scale=rng.uniform(0.4, 1.2))

    def _a16k(self, audio, sr):
        audio = np.asarray(audio, dtype=np.float32)
        if sr != SR:
            audio = resample_poly(audio, SR, sr).astype(np.float32)
        # Recorta el silencio que Piper deja al principio y al final.
        umbral = max(0.01, np.abs(audio).max() * 0.03) if len(audio) else 0
        idx = np.where(np.abs(audio) > umbral)[0]
        if len(idx) == 0:
            return np.zeros(0, np.float32)
        return audio[max(0, idx[0] - 160): idx[-1] + 160]

    def fonemas(self, nombre, sr, fonemas, hablante, rng):
        v = self.voz(nombre)
        mapa = v.config.phoneme_id_map
        if any(f not in mapa for f in fonemas):
            return None
        ids = v.phonemes_to_ids(list(fonemas))
        audio = v.phoneme_ids_to_audio(ids, self._config(hablante, rng))
        return self._a16k(audio, sr)

    def texto(self, nombre, sr, texto, hablante, rng):
        v = self.voz(nombre)
        partes = [c.audio_float_array for c in v.synthesize(texto, self._config(hablante, rng))]
        if not partes:
            return None
        return self._a16k(np.concatenate(partes), sr)


def _pausa(rng, a, b):
    return np.zeros(int(SR * rng.uniform(a, b)), np.float32)


def _trabajo(args):
    """Genera n ejemplos de un tipo para una voz. Devuelve lista de (audio int16, fin_palabra)."""
    tipo, voz, n, semilla, prueba = args
    rng = random.Random(semilla)
    s = Sintetizador()
    hablantes = [h for h in range(voz["hablantes"]) if es_prueba(voz["nombre"], h) == prueba]
    if not hablantes:
        return tipo, prueba, []
    ingles = voz["ingles"]
    out = []
    intentos = 0
    while len(out) < n and intentos < n * 3:
        intentos += 1
        h = rng.choice(hablantes)
        try:
            if tipo == "pos":
                # Las voces inglesas también dicen las pronunciaciones en español (otro timbre, mismo sonido).
                palabra = s.fonemas(voz["nombre"], voz["sr"], rng.choice(JARVIS[voz["idioma"]]), h, rng)
                # Una sola palabra: si dura mucho más, Piper ha balbuceado.
                if palabra is None or not SR * 0.25 < len(palabra) < SR * 1.2:
                    continue
                trozos, fin = [], None
                if rng.random() < 0.4:
                    antes = s.texto(voz["nombre"], voz["sr"], rng.choice(ANTES_EN if ingles else ANTES_ES), h, rng)
                    if antes is not None:
                        trozos += [antes, _pausa(rng, 0.03, 0.35)]
                trozos.append(palabra)
                fin = sum(len(t) for t in trozos)
                if rng.random() < 0.5:
                    despues = s.texto(voz["nombre"], voz["sr"], rng.choice(DESPUES_EN if ingles else DESPUES_ES), h, rng)
                    if despues is not None:
                        trozos += [_pausa(rng, 0.0, 0.3), despues]
                audio = np.concatenate(trozos)
            elif tipo == "casi":
                audio = s.fonemas(voz["nombre"], voz["sr"], rng.choice(CASI[voz["idioma"]]), h, rng)
                if audio is not None and len(audio) > SR * 1.2:
                    continue
                fin = None
            elif tipo == "parecida":
                lista = PARECIDAS_EN if ingles else PARECIDAS_ES
                audio = s.texto(voz["nombre"], voz["sr"], rng.choice(lista), h, rng)
                fin = None
            else:  # frase
                lista = FRASES_EN if ingles else FRASES_ES
                audio = s.texto(voz["nombre"], voz["sr"], rng.choice(lista), h, rng)
                fin = None
            if audio is None or len(audio) < SR * 0.15:
                continue
            pico = np.abs(audio).max()
            audio = audio / pico * 0.9 if pico > 0 else audio
            out.append(((audio * 32767).astype(np.int16), -1 if fin is None else int(fin)))
        except Exception as e:  # una pronunciación rara no debe parar todo
            print(f"[{voz['nombre']}] {e}")
    return tipo, prueba, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--escala", type=float, default=1.0, help="multiplica el número de clips")
    ap.add_argument("--tipos", default="pos,casi,parecida,frase", help="qué generar (separado por comas)")
    ap.add_argument("--procesos", type=int, default=max(1, os.cpu_count() - 1))
    a = ap.parse_args()
    SALIDA.mkdir(parents=True, exist_ok=True)
    voces = _cargar_voces()
    # Clips por voz: las voces con muchos hablantes reciben más, pero las españolas no quedan atrás.
    cuantos = {"pos": 1600, "casi": 500, "parecida": 400, "frase": 150}
    tareas = []
    semilla = 0
    for v in voces:
        peso = 3.0 if v["hablantes"] > 100 else 1.0 if v["ingles"] else 1.5
        for tipo, base in cuantos.items():
            if tipo not in a.tipos.split(","):
                continue
            for prueba in (False, True):
                n = int(base * peso * a.escala * (0.15 if prueba else 1.0))
                # trozos de 100 para repartir bien entre procesos
                while n > 0:
                    semilla += 1
                    tareas.append((tipo, v, min(100, n), semilla, prueba))
                    n -= 100
    # Positivos primero: con ellos y unos pocos negativos ya se puede probar el resto del proceso.
    orden = {"pos": 0, "casi": 1, "parecida": 2, "frase": 3}
    random.Random(0).shuffle(tareas)
    tareas.sort(key=lambda t: (orden[t[0]], t[1]["nombre"]))  # agrupa por voz: cada proceso carga menos modelos
    # Cada tarea se guarda al terminar: se puede usar lo ya generado y reanudar si se interrumpe.
    pendientes = [t for t in tareas if not _ruta_trozo(t).exists()]
    print(f"{len(tareas)} tareas, {len(pendientes)} pendientes", flush=True)
    with mp.Pool(a.procesos) as pool:
        for i, ((tipo, prueba, out), t) in enumerate(pool.imap_unordered(_trabajo_y_tarea, pendientes)):
            if i % 20 == 0:
                print(f"{i + 1}/{len(pendientes)}", flush=True)


def _ruta_trozo(tarea):
    tipo, voz, n, semilla, prueba = tarea
    return SALIDA / f"{tipo}_{'test' if prueba else 'train'}" / f"{voz['nombre']}_{semilla}.npz"


def _trabajo_y_tarea(tarea):
    tipo, prueba, out = _trabajo(tarea)
    ruta = _ruta_trozo(tarea)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    audios = np.empty(len(out), dtype=object)
    audios[:] = [o[0] for o in out]
    np.savez(ruta, audio=audios, fin=np.array([o[1] for o in out], dtype=np.int64))
    return (tipo, prueba, len(out)), tarea


def cargar_conjunto(nombre):
    """Junta todos los trozos guardados de un conjunto (p.ej. «pos_train»)."""
    audios, fines = [], []
    for p in sorted((SALIDA / nombre).glob("*.npz")):
        d = np.load(p, allow_pickle=True)
        audios += list(d["audio"])
        fines += list(d["fin"])
    arr = np.empty(len(audios), dtype=object)
    arr[:] = audios
    return arr, np.array(fines, dtype=np.int64)


if __name__ == "__main__":
    main()
