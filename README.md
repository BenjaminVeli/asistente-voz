# J.A.R.V.I.S — asistente de voz local

Todo se ejecuta en tu PC, sin APIs externas:

| Pieza | Tecnología | Dónde corre |
|---|---|---|
| Cerebro (conversación) | Ollama + `gemma4:e2b` | GPU local |
| Oído (voz → texto) | faster-whisper `small` | CPU local |
| Palabra de activación «Jarvis» | openWakeWord + modelo propio (`models/wakeword/jarvis.onnx`) | CPU local |
| Voz (texto → voz) | Piper `es_ES-davefx-medium` (respaldo: voces de Windows) | CPU local |
| Interfaz | pywebview (HTML/CSS/JS en ventana nativa) | local |
| Telemetría | psutil + NVML (NVIDIA) | local |

## Arrancar

Doble clic en **`iniciar.bat`** (arranca Ollama si hace falta y abre la ventana).
Para ver los logs: `.venv\Scripts\python.exe main.py` (con `--debug` abre las DevTools).

## Cómo hablarle

- **Botón del micrófono**, **barra espaciadora** (con la ventana enfocada) o **Ctrl+Alt+J** desde cualquier sitio.
- **Escucha continua**: activa el interruptor y empieza las frases con «Jarvis…». Un detector local muy ligero
  espera a oír «Jarvis» y solo entonces pasa el audio a Whisper, que confirma la palabra antes de obedecer.
  El pitido suena cuando Whisper la confirma; si fue una falsa alarma (la tele, alguien hablando…), se descarta
  en silencio. Si el detector no está disponible, o la palabra de activación no es «Jarvis», se usa solo Whisper.
- **Esc** o el botón ■ cortan la voz.

## Órdenes

| Ejemplo | Acción |
|---|---|
| «Abre Spotify / Chrome / Discord / Steam / la calculadora» | Abre cualquier app del menú Inicio |
| «Cierra Discord» | Cierra la app |
| «Pon Bohemian Rhapsody» / «Pon música de Queen en Spotify» | Busca en Spotify y reproduce el resultado principal |
| «Pausa», «Continúa», «Siguiente canción», «Anterior» | Teclas multimedia (sirve con Spotify, YouTube…) |
| «Sube / baja el volumen», «Volumen al 40», «Silencio» | Volumen del sistema |
| «Busca gatos en YouTube», «Busca recetas de pasta» | Abre el navegador con la búsqueda |
| «Abre la carpeta de descargas» | Abre carpetas personales |
| «¿Qué hora es?», «¿Qué día es hoy?», «¿Cómo está el sistema?» | Responde al momento |
| «Bloquea el equipo», «Borra el historial», «Cállate» | Utilidades |
| «Autodestrúyete, código de comando 00destruyete0» | Cuenta atrás de 10 a 0 con la interfaz en rojo; al llegar a 0 **suspende** el equipo |
| «Función de comando código 10» | Aborta la autodestrucción; basta con «código diez» (durante la cuenta atrás se ignora cualquier otra orden) |
| «Recuerda que mi cumpleaños es el 3 de mayo», «Mi color favorito es el azul» | Lo guarda en `memoria.json` y lo usa en las conversaciones |
| «¿Qué sabes de mí?», «Olvida que mi perro se llama Toby» | Consulta o borra la memoria |
| Peticiones indirectas: «Ponme algo tranquilo para concentrarme», «Abre lo de programar», «¿Va lento el ordenador?» | Gemma elige y ejecuta la acción adecuada |
| Cualquier otra cosa | Conversa con Gemma (conoce lo que hay en la memoria) |

## Interfaz

- **Izquierda, telemetría**: CPU, RAM, GPU, VRAM, historial de 60 s, núcleos, temperatura, disco, red y micrófono.
- **Centro**:
  - el reactor, que cambia de color según el estado;
  - los botones de voz, micrófono y detener, y el interruptor de escucha continua;
  - la barra **DETECTOR**, con la puntuación de «Jarvis» en tiempo real: la línea naranja es el umbral, y si la
    barra la cruza, se activa;
  - las apps rápidas;
  - el **reproductor**, que muestra lo que suena en cualquier app (Spotify, YouTube en el navegador…) y tiene
    anterior, reproducir/pausa, siguiente, silencio y una barra con el volumen real del sistema.
- **Derecha**, tres pestañas:
  - **Comunicaciones**: la conversación y la caja para escribir órdenes.
  - **Memoria**: lo que Jarvis recuerda de ti. Puedes añadir, editar (doble clic o ✎) y borrar (✕) datos. Se
    sincroniza con «recuerda que…» y «olvida…».
  - **Activaciones**: cada vez que el detector oyó «Jarvis», con hora, puntuación, lo que entendió Whisper y si se
    **confirmó** o se **descartó**, más el porcentaje de acierto. Se guarda en `activaciones.jsonl`, que sirve
    para medir el detector en uso real y ajustar la sensibilidad.

## Personalizar

El botón del **engranaje ⚙** (arriba a la derecha) abre el panel de ajustes, que se aplican al momento y se
guardan en `config.json`:

- **Voz**: voces neuronales de Piper (todas las de `models/piper`) o voces de Windows, con botón de prueba.
- **Velocidad** y **volumen** de la voz.
- **Nombre** del asistente, **cómo te llama** y **palabra de activación**.
- **Sensibilidad al oír «Jarvis»**: si se activa sola con la tele o la música, bájala; si le cuesta oírte, súbela.
  Mira la barra DETECTOR y la pestaña Activaciones para afinarla.
- **Modelo de Ollama** (lista los modelos instalados).

### `config.json`

- `microfono`: parte del nombre del micrófono (también se elige en la interfaz).
- `aliases_apps`: apodos → nombre de la app en el menú Inicio (p. ej. `"juego": "Minecraft Launcher"`).
- `accesos_rapidos`: botones de la barra inferior.
- `activacion_local` (usar el detector local), `modelo_activacion` y `umbral_activacion` (0–1; lo cambia el
  control de sensibilidad).
- `modelo_llm`, `whisper_modelo` (`base` = más rápido, `medium` = más preciso), `velocidad_voz`, `tratamiento`, `atajo_teclado`.
- Otras voces de Piper: <https://huggingface.co/rhasspy/piper-voices> (descarga `.onnx` + `.onnx.json` en `models/piper` y elígela desde el engranaje).

## Estructura

```
main.py              ventana + puente Python ↔ interfaz
jarvis/assistant.py  orquestador (estados, voz, órdenes, LLM)
jarvis/commands.py   reglas de órdenes en español y herramientas para Gemma  ← añade aquí tus comandos
jarvis/apps.py       índice del menú Inicio, abrir/cerrar apps
jarvis/media.py      multimedia, volumen exacto (pycaw), lo que suena (Windows Media), Spotify, web
jarvis/stt.py        micrófono + Whisper
jarvis/wakeword.py   detector local de «Jarvis» (openWakeWord)
jarvis/tts.py        Piper / SAPI
jarvis/llm.py        cliente de Ollama: decide la herramienta y conversa
jarvis/memory.py     memoria a largo plazo (memoria.json)
jarvis/stats.py      CPU / RAM / GPU
web/                 interfaz (index.html, style.css, app.js)
models/wakeword/     modelos del detector (jarvis.onnx + los base de openWakeWord)
training/            scripts para entrenar el detector; estado y próximos pasos en training/CONTINUAR.md
```
