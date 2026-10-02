"""Índice de aplicaciones del menú Inicio (incluye apps de la Microsoft Store) para abrirlas y cerrarlas."""
import difflib
import json
import os
import re
import subprocess
import threading
import unicodedata

import psutil

CREATE_NO_WINDOW = 0x08000000

# Procesos que nunca se deben cerrar por voz.
PROTEGIDOS = {"explorer", "svchost", "system", "csrss", "winlogon", "lsass", "services", "python",
              "pythonw", "ollama", "dwm", "msedgewebview2", "smss", "wininit"}

DESCARTAR = re.compile(r"uninstall|desinstal|readme|help|ayuda|support|soporte|documentation|manual", re.I)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^a-z0-9ñ+ ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class AppIndex:
    def __init__(self, aliases: dict):
        self.aliases = {normalize(k): v for k, v in aliases.items()}
        self.apps: list[tuple[str, str, str]] = []  # (nombre, nombre_normalizado, appid)
        self._ready = threading.Event()
        threading.Thread(target=self.refresh, daemon=True).start()

    def refresh(self):
        cmd = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True,
                                 text=True, encoding="utf-8", creationflags=CREATE_NO_WINDOW, timeout=60).stdout
            items = json.loads(out) if out.strip() else []
            if isinstance(items, dict):
                items = [items]
            apps = []
            for it in items:
                name, appid = it.get("Name", ""), it.get("AppID", "")
                if not name or not appid or appid.lower().startswith("http") or DESCARTAR.search(name):
                    continue
                apps.append((name, normalize(name), appid))
            self.apps = apps
        except Exception as e:
            print(f"[apps] No se pudo leer el menú Inicio: {e}")
        finally:
            self._ready.set()

    def find(self, query: str):
        self._ready.wait(timeout=30)
        q = normalize(query)
        if not q:
            return None
        if q in self.aliases:
            q = normalize(self.aliases[q])
        by_norm = {n: (name, appid) for name, n, appid in self.apps}
        if q in by_norm:
            return by_norm[q]
        # Empieza por / contiene: se prefiere el nombre más corto (p.ej. "Spotify" antes que "Spotify Helper").
        candidatos = [a for a in self.apps if a[1].startswith(q)] or \
                     [a for a in self.apps if re.search(rf"\b{re.escape(q)}\b", a[1])] or \
                     [a for a in self.apps if q in a[1]]
        if candidatos:
            name, _, appid = min(candidatos, key=lambda a: len(a[1]))
            return name, appid
        close = difflib.get_close_matches(q, list(by_norm), n=1, cutoff=0.6)
        if close:
            return by_norm[close[0]]
        return None

    @staticmethod
    def launch(appid: str):
        if os.path.exists(appid):
            os.startfile(appid)
        else:
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{appid}"], creationflags=CREATE_NO_WINDOW)

    def close(self, query: str) -> int:
        """Cierra los procesos que coinciden con la app. Devuelve cuántos se cerraron."""
        q = normalize(query)
        found = self.find(query)
        nombres = set()
        if found:
            name, appid = found
            if appid.lower().endswith(".exe"):
                nombres.add(os.path.splitext(os.path.basename(appid))[0].lower())
            nombres.add(normalize(name).split(" ")[-1] if " " in normalize(name) else normalize(name))
        if len(q) >= 3:
            nombres.add(q.replace(" ", ""))
        nombres = {n for n in nombres if len(n) >= 3 and n not in PROTEGIDOS}
        if not nombres:
            return 0
        cerrados = 0
        for p in psutil.process_iter(["name"]):
            try:
                pname = os.path.splitext(p.info["name"] or "")[0].lower()
                if pname in PROTEGIDOS:
                    continue
                if any(n == pname or (len(n) >= 4 and n in pname) for n in nombres):
                    p.terminate()
                    cerrados += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return cerrados
