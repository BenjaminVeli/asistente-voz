"""Control multimedia (teclas de medios de Windows), volumen, Spotify y búsquedas web. Sin APIs externas."""
import ctypes
import os
import threading
import time
import urllib.parse
import webbrowser

import psutil

_user32 = ctypes.windll.user32

VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_MEDIA_NEXT = 0xB0
VK_MEDIA_PREV = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3


def _press(vk: int, veces: int = 1):
    for _ in range(veces):
        _user32.keybd_event(vk, 0, 0, 0)
        _user32.keybd_event(vk, 0, 2, 0)
        time.sleep(0.01)


def play_pause():
    _press(VK_MEDIA_PLAY_PAUSE)


def siguiente():
    _press(VK_MEDIA_NEXT)


def anterior():
    _press(VK_MEDIA_PREV)


def silenciar():
    _press(VK_VOLUME_MUTE)


def subir_volumen(pasos: int = 5):
    _press(VK_VOLUME_UP, pasos)  # cada pulsación = 2 %


def bajar_volumen(pasos: int = 5):
    _press(VK_VOLUME_DOWN, pasos)


def fijar_volumen(porcentaje: int):
    porcentaje = max(0, min(100, porcentaje))
    _press(VK_VOLUME_DOWN, 50)
    _press(VK_VOLUME_UP, round(porcentaje / 2))


def spotify_buscar(consulta: str):
    """Abre Spotify en los resultados de búsqueda y reproduce el resultado principal.

    Spotify no reproduce nada al abrir una búsqueda, así que se pulsa su botón «Reproducir»
    mediante UI Automation (la accesibilidad de Windows), en segundo plano para no bloquear la voz.
    """
    previos = _spotify_botones_play()
    os.startfile("spotify:search:" + urllib.parse.quote(consulta))
    threading.Thread(target=_spotify_reproducir_primero, args=(previos,), daemon=True).start()


_hilo = threading.local()  # los objetos COM no se pueden compartir entre hilos


def _uia_init():
    if not hasattr(_hilo, "uia"):
        import comtypes
        import comtypes.client
        comtypes.CoInitialize()
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as U
        _hilo.uia = (U, comtypes.client.CreateObject(U.CUIAutomation, interface=U.IUIAutomation))
    return _hilo.uia


def _spotify_ventana(U, uia):
    pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "spotify.exe"}
    cond = uia.CreatePropertyCondition(U.UIA_ClassNamePropertyId, "Chrome_WidgetWin_1")
    ventanas = uia.GetRootElement().FindAll(U.TreeScope_Children, cond)
    for i in range(ventanas.Length):
        v = ventanas.GetElement(i)
        if v.CurrentProcessId in pids and v.CurrentName:
            return v
    return None


CONTROLES_BARRA = {"Anterior", "Siguiente", "Previous", "Next"}


def _spotify_botones_play(intentos: int = 3):
    """Botones «Reproducir …» / «Play …» de la vista actual de Spotify, en orden de pantalla.

    Se excluye el botón de la barra de reproducción de abajo: cuando la música está en pausa también se
    llama «Reproducir» y pulsarlo reanuda lo anterior en vez de poner la búsqueda.
    """
    for intento in range(intentos):
        try:
            U, uia = _uia_init()
            ventana = _spotify_ventana(U, uia)
            if ventana is None:
                return []
            cond = uia.CreatePropertyCondition(U.UIA_ControlTypePropertyId, U.UIA_ButtonControlTypeId)
            botones = ventana.FindAll(U.TreeScope_Descendants, cond)
            res, barra = [], None
            for i in range(botones.Length):
                b = botones.GetElement(i)
                nombre = b.CurrentName or ""
                if nombre in CONTROLES_BARRA:
                    r = b.CurrentBoundingRectangle
                    barra = r.top if barra is None else min(barra, r.top)
                elif nombre == "Reproducir" or nombre == "Play" or nombre.startswith(("Reproducir ", "Play ")):
                    if not b.CurrentIsOffscreen:
                        r = b.CurrentBoundingRectangle
                        res.append(((r.top, r.left), nombre, b))
            if barra is not None:
                res = [x for x in res if x[0][0] < barra - 20]
            # El árbol no sigue el orden visual: el resultado principal suele salir al final.
            res.sort(key=lambda x: (x[0][0] // 20, x[0][1]))
            return [(nombre, b) for _, nombre, b in res]
        except Exception as e:  # Chromium a veces falla de forma transitoria al recorrer el árbol
            if intento == intentos - 1:
                print(f"[spotify] UI Automation no disponible: {e}")
            time.sleep(0.2)
    return []


def _spotify_reproducir_primero(previos, espera: float = 15.0):
    """Espera a que cargue la búsqueda y pulsa el primer «Reproducir» (el del resultado principal)."""
    antes = [n for n, _ in previos[:6]]
    limite = time.time() + espera
    botones = []
    while time.time() < limite:
        time.sleep(0.5)
        botones = _spotify_botones_play()
        # Los nombres cambian cuando la vista ya muestra los resultados nuevos.
        if botones and [n for n, _ in botones[:6]] != antes:
            time.sleep(0.4)  # deja que termine de pintar la página
            botones = _spotify_botones_play() or botones
            break
    if not botones:
        print("[spotify] No se encontró el botón de reproducir.")
        return
    try:
        U, _ = _uia_init()
        patron = botones[0][1].GetCurrentPattern(U.UIA_InvokePatternId)
        patron.QueryInterface(U.IUIAutomationInvokePattern).Invoke()
    except Exception as e:
        print(f"[spotify] No se pudo reproducir: {e}")


def spotify_abrir():
    os.startfile("spotify:")


def buscar_web(consulta: str, sitio: str = "google"):
    q = urllib.parse.quote_plus(consulta)
    urls = {
        "google": f"https://www.google.com/search?q={q}",
        "youtube": f"https://www.youtube.com/results?search_query={q}",
        "wikipedia": f"https://es.wikipedia.org/w/index.php?search={q}",
    }
    webbrowser.open(urls.get(sitio, urls["google"]))


SITIOS = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "whatsapp": "https://web.whatsapp.com",
    "netflix": "https://www.netflix.com",
    "github": "https://github.com",
    "twitch": "https://www.twitch.tv",
    "chatgpt": "https://chat.openai.com",
    "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "wikipedia": "https://es.wikipedia.org",
}


def abrir_web(url: str):
    webbrowser.open(url)


def bloquear_equipo():
    _user32.LockWorkStation()


def suspender_equipo():
    """Suspende el equipo (no lo apaga ni lo hiberna)."""
    ctypes.windll.powrprof.SetSuspendState(False, False, False)
