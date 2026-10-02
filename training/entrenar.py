"""Paso 3: entrena el detector de «Jarvis» y lo exporta a ONNX para openWakeWord.

- Positivos: ventanas con «Jarvis» al final (data/features/pos_train.npy).
- Negativos: palabras parecidas, trozos, frases y ruido (sintéticos) + 2.000 h de audio real (ACAV100M),
  que se leen por bloques porque no caben en memoria.
- Minería de negativos difíciles: cada cierto tiempo se buscan en el audio real las ventanas que más
  confunden al modelo y se refuerzan.
- Evaluación: aciertos con hablantes y voces que nunca vio (Piper reservadas + voces de Windows) y
  falsas activaciones por hora en ~11 h de audio de validación.

Uso:  python entrenar.py [--pasos 60000]
Salida: data/modelos/jarvis.onnx (+ informe en data/modelos/informe.json)
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn

DATA = Path(__file__).parent / "data"
FEATS = DATA / "features"          # características de ACAV y validación (descargadas)
FEATS_SINT = DATA / "features"     # las que genera aumentar.py
SALIDA = DATA / "modelos"
DEV = "cuda" if torch.cuda.is_available() else "cpu"
HORAS_VALIDACION = 481345 * 0.08 / 3600


class Detector(nn.Module):
    def __init__(self, dim=256, drop=0.3):
        super().__init__()
        self.red = nn.Sequential(
            nn.Flatten(),
            nn.Linear(16 * 96, dim), nn.LayerNorm(dim), nn.ReLU(), nn.Dropout(drop),
            nn.Linear(dim, dim), nn.LayerNorm(dim), nn.ReLU(), nn.Dropout(drop),
            nn.Linear(dim, dim // 2), nn.LayerNorm(dim // 2), nn.ReLU(),
            nn.Linear(dim // 2, 1),
        )

    def forward(self, x):
        return self.red(x)


class ConSigmoide(nn.Module):
    """Lo que se exporta: devuelve directamente la probabilidad (0-1), como espera openWakeWord."""

    def __init__(self, m):
        super().__init__()
        self.m = m

    def forward(self, x):
        return torch.sigmoid(self.m(x))


def cargar(nombre):
    p = FEATS_SINT / f"{nombre}.npy"
    return np.load(p).astype(np.float32) if p.exists() else np.zeros((0, 16, 96), np.float32)


class Acav:
    """2.000 h de audio real como negativos, leídas por bloques contiguos (el archivo pesa 17 GB)."""

    def __init__(self, bloque=400_000, semilla=0):
        self.a = np.load(FEATS / "openwakeword_features_ACAV100M_2000_hrs_16bit.npy", mmap_mode="r")
        self.bloque = bloque
        self.rng = np.random.default_rng(semilla)
        self.siguiente()

    def siguiente(self):
        i = int(self.rng.integers(0, len(self.a) - self.bloque))
        self.datos = torch.from_numpy(np.asarray(self.a[i:i + self.bloque], dtype=np.float16)).to(DEV)

    def muestra(self, n):
        idx = torch.randint(0, len(self.datos), (n,), device=DEV)
        return self.datos[idx].float()


def ventanas_validacion():
    v = np.load(FEATS / "validation_set_features.npy", mmap_mode="r")
    v = torch.from_numpy(np.asarray(v, dtype=np.float32))
    return v.unfold(0, 16, 1).permute(0, 2, 1)  # (N-15, 16, 96), un paso = 80 ms


@torch.no_grad()
def puntuar(m, x, lote=8192):
    m.eval()
    out = []
    for i in range(0, len(x), lote):
        xb = x[i:i + lote]
        xb = (xb if torch.is_tensor(xb) else torch.from_numpy(np.asarray(xb))).to(DEV).float()
        out.append(torch.sigmoid(m(xb)).squeeze(1).cpu())
    m.train()
    return torch.cat(out).numpy() if out else np.zeros(0)


def activaciones(p, umbral, refractario=25):
    """Cuenta disparos separados (tras uno, se ignoran ~2 s, como hace el asistente)."""
    n, i = 0, 0
    idx = np.where(p >= umbral)[0]
    ultimo = -10 ** 9
    for j in idx:
        if j - ultimo > refractario:
            n += 1
            ultimo = j
    return n


def evaluar(m, pruebas, val):
    res = {}
    for nombre, (x, y) in pruebas.items():
        p = puntuar(m, x)
        res[nombre] = p
    pv = puntuar(m, val)
    informe = {}
    for u in (0.3, 0.5, 0.7, 0.8, 0.9):
        fila = {"fa_hora": activaciones(pv, u) / HORAS_VALIDACION}
        for nombre, (x, y) in pruebas.items():
            p = res[nombre]
            fila[nombre] = float((p >= u).mean())  # positivos: aciertos; negativos: falsas alarmas
        informe[u] = fila
    return informe


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pasos", type=int, default=60000)
    ap.add_argument("--dim", type=int, default=256)
    ap.add_argument("--peso-neg", type=float, default=400, help="peso máximo de los negativos")
    ap.add_argument("--prueba", action="store_true", help="usa data/features_prueba (de aumentar.py --rapido)")
    a = ap.parse_args()
    global FEATS_SINT
    if a.prueba:
        FEATS_SINT = DATA / "features_prueba"
    SALIDA.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(0)

    pos = torch.from_numpy(np.concatenate([cargar("pos_train"), cargar("personal_pos")])).to(DEV)
    neg_sint = torch.from_numpy(np.concatenate([cargar(n) for n in
                                                ("casi_train", "parecida_train", "frase_train", "fondo", "personal_neg")])).to(DEV)
    print(f"positivos {len(pos)}, negativos sintéticos {len(neg_sint)}, dispositivo {DEV}", flush=True)
    pruebas = {  # nombre: (características, etiqueta)
        "piper_limpio": (cargar("pos_test_limpio"), 1),
        "piper_ruido": (cargar("pos_test_normal"), 1),
        "windows_limpio": (cargar("win_pos_limpio"), 1),
        "windows_ruido": (cargar("win_pos_normal"), 1),
        "neg_casi": (cargar("casi_test_normal"), 0),
        "neg_parecidas": (cargar("parecida_test_normal"), 0),
        "neg_frases": (cargar("frase_test_normal"), 0),
        "neg_windows": (cargar("win_neg_normal"), 0),
    }
    pruebas = {k: v for k, v in pruebas.items() if len(v[0])}
    val = ventanas_validacion()
    acav = Acav()
    duras = torch.zeros((0, 16, 96), device=DEV)

    m = Detector(a.dim).to(DEV)
    opt = torch.optim.AdamW(m.parameters(), lr=1e-3, weight_decay=1e-2)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=1e-3, total_steps=a.pasos, pct_start=0.05)
    bce = nn.BCEWithLogitsLoss(reduction="none")
    mejor, historial = None, []
    t0 = time.time()
    for paso in range(1, a.pasos + 1):
        if paso % 3000 == 0:
            acav.siguiente()
        # Minería: las ventanas reales que el modelo más confunde con «Jarvis».
        if paso % 4000 == 0:
            p = puntuar(m, acav.datos)
            top = torch.from_numpy(np.argsort(-p)[:4000].copy()).to(DEV)
            duras = torch.cat([duras, acav.datos[top].float()])[-40000:]
        nb_pos, nb_sint, nb_acav, nb_duras = 256, 256, 768, 128 if len(duras) else 0
        xp = pos[torch.randint(0, len(pos), (nb_pos,), device=DEV)]
        xs = neg_sint[torch.randint(0, len(neg_sint), (nb_sint,), device=DEV)]
        xa = acav.muestra(nb_acav)
        partes = [xp, xs, xa]
        if nb_duras:
            partes.append(duras[torch.randint(0, len(duras), (nb_duras,), device=DEV)])
        x = torch.cat(partes)
        y = torch.cat([torch.ones(nb_pos, device=DEV), torch.zeros(len(x) - nb_pos, device=DEV)])
        # Un poco de ruido en las características: generaliza mejor a micrófonos reales.
        x = x + torch.randn_like(x) * 0.05 * x.std()
        # El peso de los negativos sube con el entrenamiento: primero aprende la palabra, luego a no confundirse.
        w_neg = 1 + (a.peso_neg - 1) * min(1.0, paso / (a.pasos * 0.6))
        w = torch.where(y == 1, torch.ones_like(y), torch.full_like(y, w_neg))
        perdida = (bce(m(x).squeeze(1), y) * w).mean()
        opt.zero_grad()
        perdida.backward()
        opt.step()
        sched.step()

        if paso % 5000 == 0 or paso == a.pasos:
            inf = evaluar(m, pruebas, val)
            r = inf[0.5]
            recall = np.mean([r[k] for k in ("piper_ruido", "windows_limpio", "windows_ruido") if k in r])
            confusion = np.mean([r[k] for k in ("neg_casi", "neg_parecidas", "neg_windows") if k in r])
            # Puntuación: aciertos, penalizando mucho cada falsa activación por hora.
            # Whisper confirma cada activación, así que importa más no perderse ninguna que alguna falsa alarma.
            nota = recall - 0.01 * r["fa_hora"] - 0.5 * confusion
            historial.append({"paso": paso, "nota": nota, "informe": {str(k): v for k, v in inf.items()}})
            print(f"[{paso}] pérdida {perdida.item():.4f}  {time.time() - t0:.0f}s  nota {nota:.3f}", flush=True)
            for u, f in inf.items():
                print(f"   umbral {u}: " + "  ".join(f"{k}={v:.3f}" for k, v in f.items()), flush=True)
            if mejor is None or nota > mejor[0]:
                mejor = (nota, paso, {k: v.detach().clone() for k, v in m.state_dict().items()})
    print(f"mejor paso {mejor[1]} (nota {mejor[0]:.3f})")
    m.load_state_dict(mejor[2])
    m.eval()
    torch.save(m.state_dict(), SALIDA / "jarvis.pt")
    final = ConSigmoide(m).cpu().eval()
    torch.onnx.export(final, torch.zeros(1, 16, 96), str(SALIDA / "jarvis.onnx"), input_names=["features"],
                      output_names=["jarvis"], opset_version=13, dynamic_axes={"features": {0: "batch"}, "jarvis": {0: "batch"}})
    (SALIDA / "informe.json").write_text(json.dumps({"mejor_paso": mejor[1], "historial": historial}, indent=1))
    print("exportado", SALIDA / "jarvis.onnx")


if __name__ == "__main__":
    main()
