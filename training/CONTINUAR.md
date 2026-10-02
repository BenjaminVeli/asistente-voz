# Palabra de activación «Jarvis» — estado y cómo continuar

Pausado el 2 de octubre de 2026. El asistente **funciona como antes** (activación con Whisper) mientras no
exista `models/wakeword/jarvis.onnx`: `jarvis/wakeword.py` → `disponible()` lo comprueba.

## Qué está hecho

- **Integración en el asistente** (lista, sin probar con micrófono real):
  - `jarvis/wakeword.py`: detector con openWakeWord.
  - `jarvis/assistant.py`: `_esperar_palabra` / `_tras_palabra`. Graba la orden sin cerrar el micrófono y
    pasa a Whisper también los 1,6 s anteriores.
  - `jarvis/stt.py`: `record(stream=...)`.
  - Control «Sensibilidad al oír «Jarvis»» en el engranaje. Claves de `config.json`: `activacion_local`,
    `modelo_activacion`, `umbral_activacion`.
- **Modelos base** de openWakeWord en `models/wakeword/` (`melspectrogram.onnx`, `embedding_model.onnx`).
- **Scripts** en `training/`:
  - `generar.py`: voces Piper.
  - `prueba_windows.py`: prueba con voces de Windows.
  - `aumentar.py`: ruido, eco y características.
  - `entrenar.py`: entrenamiento y exportación.
  - `grabar.py`: graba tu voz.
- **Datos ya generados** en `training/data/` (~40 GB, fuera de git): 54k positivos, 36k negativos sintéticos,
  características aumentadas y 2.000 h de negativos reales (ACAV).

## Resultados hasta ahora

Medido con voces nunca vistas al entrenar, con umbral 0,5. FA/h = falsas activaciones por hora en ~11 h de
audio variado.

| Modelo | Detecta «Jarvis» (voces Windows) | FA/h |
|---|---|---|
| `hey_jarvis` preentrenado (sin decir «hey») | 7,7 % | 0,5 |
| `modelos/jarvis_v1.onnx` (peso negativos 20) | ~84–88 % | ~80–120 |
| `modelos/jarvis_peso400.onnx` (peso 400, mejor paso 25k) | ~63–69 % | ~7,7 |
| `modelos/jarvis.onnx` (peso 100, mejor paso 10k) — **instalado** | ~72 % | ~21 |

Ninguno sirve aún como detector único: o se pierde «Jarvis» o salta demasiado.

## Sesión del 2 de octubre (2.ª parte)

- Hechos los pasos 1–4 de abajo. Modelo de peso 100 instalado en `models/wakeword/jarvis.onnx`
  (`data/entrenar4.log`). `prueba_e2e.py` está ahora en `training/` y pasa 3/3.
- No se llegó al objetivo. Con peso 100, la curva es de ~72 % a 21 FA/h y de ~84 % a 73 FA/h (paso 60k,
  umbral 0,3). Probablemente es un límite de las voces sintéticas: lo que más puede mejorar es grabar tu voz
  (paso 6).
- Pendiente: la prueba real con micrófono.

## Plan original (pasos 1–4 ya hechos)

1. En `entrenar.py`, cambia la nota de selección a
   `nota = recall - 0.01 * r["fa_hora"] - 0.5 * confusion`.
   Así prima no perderse ningún «Jarvis», porque Whisper confirmará cada activación.
2. Entrena:

   ```
   cd training
   .venv\Scripts\python entrenar.py --pasos 60000 --peso-neg 100
   ```

   Objetivo: ≥ 85 % detección con FA/h ≤ ~20. Si no se llega, prueba con `--peso-neg 50` y `--peso-neg 200`.
3. En `jarvis/assistant.py`, haz que Whisper confirme siempre y que no haya pitido hasta confirmar:
   - pon `MUY_SEGURO = 1.01`;
   - mueve `self.emit("beep", "start")` y `set_state("listening")` de `_esperar_palabra` a `_tras_palabra`,
     tras confirmar;
   - baja `max_seconds` de la grabación tras detectar a ~6 s.
4. Copia el modelo: `copy training\data\modelos\jarvis.onnx models\wakeword\jarvis.onnx`.
5. Prueba de punta a punta con micrófono falso: el script `prueba_e2e.py` (estaba en el scratchpad de la sesión;
   simula «Jarvis, abre Spotify», «solo Jarvis» y una frase sin Jarvis). Después, prueba real con escucha
   continua.
6. Opcional, y lo que más mejora: graba tu voz y reentrena.

   ```
   .venv\Scripts\python grabar.py jarvis
   .venv\Scripts\python grabar.py ambiente 5
   .venv\Scripts\python aumentar.py
   .venv\Scripts\python entrenar.py ...
   ```

## Espacio en disco

**Ya liberado** el 2 de octubre: se borraron `training/data/` y `training/.venv/` (~45 GB). Los modelos entrenados y
los logs se guardaron en `training/modelos/` (~8 MB). Para cualquier reentrenamiento (incluido el paso 6) hay que
rehacerlo todo desde cero, como se explica abajo (varias horas de descargas y generación).

## Medir el modelo en uso real

Cada activación del detector se guarda en `activaciones.jsonl` (raíz del proyecto, fuera de git): hora,
puntuación, umbral, resultado (`confirmada` o `descartada` por Whisper) y lo transcrito. También se ve en la pestaña
ACTIVACIONES de la interfaz, y la barra DETECTOR bajo el micrófono muestra la puntuación en vivo.

Para reentrenar desde cero:

1. Crea el entorno:
   - `py -3.12 -m venv training\.venv`
   - instala `torch` (cu121), `numpy`, `scipy`, `soundfile`, `tqdm`, `scikit-learn`, `onnx`, `piper-tts`,
     `openwakeword`, `sounddevice` y `onnxruntime-gpu[cuda,cudnn]`.
2. Descarga:
   - las características ACAV y la validación de `huggingface.co/datasets/davidscripka/openwakeword_features`;
   - las respuestas al impulso de `davidscripka/MIT_environmental_impulse_responses` (`16khz/`);
   - MUSAN (openslr 17) y ESC-50;
   - las voces Piper de la lista en `generar.py`.
3. Ejecuta `generar.py --escala 1.5`, `prueba_windows.py` (con el Python del asistente), `aumentar.py` y
   `entrenar.py`.
