"""Métricas del sistema: CPU, RAM, GPU (NVIDIA vía NVML), disco y red."""
import threading
import time

import psutil

try:
    import pynvml

    pynvml.nvmlInit()
    _GPU = pynvml.nvmlDeviceGetHandleByIndex(0)
    _GPU_NAME = pynvml.nvmlDeviceGetName(_GPU)
    if isinstance(_GPU_NAME, bytes):
        _GPU_NAME = _GPU_NAME.decode()
except Exception:
    _GPU = None
    _GPU_NAME = "Sin GPU NVIDIA"


class SystemStats:
    def __init__(self):
        self._net = psutil.net_io_counters()
        self._t = time.time()
        self._last = None
        self._ready = threading.Event()
        threading.Thread(target=self._sampler, daemon=True).start()

    def _sampler(self):
        # psutil guarda la referencia de cpu_percent() por hilo, y pywebview atiende cada llamada
        # desde JS en un hilo distinto (siempre daría 0 %). Por eso medimos siempre desde este hilo.
        psutil.cpu_percent(None)
        psutil.cpu_percent(None, percpu=True)
        while True:
            time.sleep(1)
            try:
                self._last = self._read()
                self._ready.set()
            except Exception as e:
                print(f"[stats] {e}")

    def snapshot(self) -> dict:
        self._ready.wait(timeout=3)
        return self._last

    def _read(self) -> dict:
        now = time.time()
        dt = max(now - self._t, 1e-3)
        net = psutil.net_io_counters()
        down = (net.bytes_recv - self._net.bytes_recv) / dt
        up = (net.bytes_sent - self._net.bytes_sent) / dt
        self._net, self._t = net, now

        vm = psutil.virtual_memory()
        freq = psutil.cpu_freq()
        disk = psutil.disk_usage("C:\\")

        data = {
            "cpu": psutil.cpu_percent(None),
            "cores": psutil.cpu_percent(None, percpu=True),
            "cpu_freq": round(freq.current / 1000, 2) if freq else 0,
            "ram": vm.percent,
            "ram_used": round(vm.used / 1024**3, 1),
            "ram_total": round(vm.total / 1024**3, 1),
            "disk": disk.percent,
            "disk_free": round(disk.free / 1024**3, 0),
            "net_down": down,
            "net_up": up,
            "uptime": int(now - psutil.boot_time()),
            "procs": len(psutil.pids()),
            "gpu_name": _GPU_NAME,
            "gpu": None,
            "vram": None,
            "vram_used": None,
            "vram_total": None,
            "gpu_temp": None,
        }
        if _GPU is not None:
            try:
                util = pynvml.nvmlDeviceGetUtilizationRates(_GPU)
                mem = pynvml.nvmlDeviceGetMemoryInfo(_GPU)
                data["gpu"] = util.gpu
                data["vram_used"] = round(mem.used / 1024**3, 2)
                data["vram_total"] = round(mem.total / 1024**3, 2)
                data["vram"] = round(mem.used / mem.total * 100, 1)
                data["gpu_temp"] = pynvml.nvmlDeviceGetTemperature(_GPU, pynvml.NVML_TEMPERATURE_GPU)
            except Exception:
                pass
        return data

    def resumen_hablado(self) -> str:
        s = self.snapshot()
        texto = (f"La CPU está al {s['cpu']:.0f} por ciento y la memoria RAM al {s['ram']:.0f} por ciento, "
                 f"{s['ram_used']} de {s['ram_total']} gigas.")
        if s["gpu"] is not None:
            texto += (f" La GPU trabaja al {s['gpu']} por ciento, con {s['vram_used']} gigas de memoria de vídeo "
                      f"y {s['gpu_temp']} grados.")
        return texto
