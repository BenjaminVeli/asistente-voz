"use strict";

const $ = (s) => document.querySelector(s);
const api = () => window.pywebview && window.pywebview.api;

const STATES = {
  booting:        ["INICIALIZANDO",  "cargando módulos…"],
  idle:           ["EN ESPERA",      "pulsa el micrófono o escribe una orden"],
  listening:      ["ESCUCHANDO",     "te escucho…"],
  listening_wake: ["EN GUARDIA",     "di «Jarvis» seguido de tu orden"],
  transcribing:   ["PROCESANDO VOZ", "transcribiendo…"],
  thinking:       ["ANALIZANDO",     "consultando el núcleo…"],
  speaking:       ["RESPONDIENDO",   ""],
};

const PALETTE = {
  booting: [255, 182, 72], idle: [63, 224, 255], listening: [69, 255, 176], listening_wake: [63, 190, 230],
  transcribing: [255, 182, 72], thinking: [255, 182, 72], speaking: [143, 240, 255],
};

const ui = {
  state: "booting",
  level: 0,          // nivel de audio objetivo (0..1)
  smooth: 0,         // nivel suavizado para la animación
  color: [255, 182, 72],
  current: null,     // mensaje del asistente en streaming
  count: 0,
  wake: "Jarvis",
  destruct: false,   // cuenta atrás de autodestrucción en marcha
};

/* ================= Reloj ================= */
function tickClock() {
  const d = new Date();
  $("#clock-time").textContent = d.toLocaleTimeString("es-ES", { hour12: false });
  $("#clock-date").textContent = d.toLocaleDateString("es-ES", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
}
setInterval(tickClock, 1000); tickClock();

/* ================= Registro de mensajes ================= */
function now() { return new Date().toLocaleTimeString("es-ES", { hour12: false }); }

function addMsg(kind, text, extra = "") {
  const el = document.createElement("div");
  el.className = `msg ${kind} ${extra}`;
  const who = kind === "user" ? "TÚ" : kind === "error" ? "SISTEMA" : ui.name || "JARVIS";
  el.innerHTML = `<header><span></span><time></time></header><p></p>`;
  el.querySelector("span").textContent = who;
  el.querySelector("time").textContent = now();
  el.querySelector("p").textContent = text;
  const log = $("#log");
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  $("#msg-count").textContent = String(++ui.count).padStart(3, "0");
  return el;
}

/* ================= Eventos desde Python ================= */
function setState(s) {
  ui.state = s;
  document.body.className = s;
  if (ui.destruct) return;  // la etiqueta la controla la cuenta atrás
  const [t, sub] = STATES[s] || [s.toUpperCase(), ""];
  $("#state-text").textContent = t;
  if (s !== "speaking") $("#state-sub").textContent = sub;
}

let pendingAction = false;
const handlers = {
  state: setState,
  level: (v) => { ui.level = v; },
  user: (t) => { addMsg("user", t); if (!ui.destruct) $("#state-sub").textContent = `«${t}»`; },
  action: () => { pendingAction = true; },
  assistant: (t) => {
    if (!t) return;
    addMsg("bot", t, pendingAction ? "action" : "");
    pendingAction = false;
    if (!ui.destruct) $("#state-sub").textContent = t;
  },
  assistant_start: () => { ui.current = addMsg("bot", "", "typing"); },
  assistant_token: (tok) => {
    if (!ui.current) ui.current = addMsg("bot", "", "typing");
    const p = ui.current.querySelector("p");
    p.textContent += tok;
    if (!ui.destruct) $("#state-sub").textContent = p.textContent.slice(-90);
    $("#log").scrollTop = $("#log").scrollHeight;
  },
  assistant_end: () => { if (ui.current) ui.current.classList.remove("typing"); ui.current = null; },
  error: (t) => addMsg("error", t),
  clear: () => {
    $("#log").innerHTML = "";
    ui.current = null;
    ui.count = 0;
    $("#msg-count").textContent = "000";
  },
  continuous: (on) => { $("#chk-continuous").checked = !!on; },
  beep: () => beep(),
  selfdestruct: (d) => {
    const was = ui.destruct;
    ui.destruct = !!d.active;
    document.documentElement.classList.toggle("destruct", ui.destruct);
    if (ui.destruct) {
      $("#state-text").textContent = "AUTODESTRUCCIÓN";
      if (d.n == null) {
        $("#destruct-count").textContent = "10";
        $("#state-sub").textContent = "secuencia iniciada";
        alarm(-1);
        return;
      }
      const c = $("#destruct-count");
      c.textContent = d.n;
      c.classList.remove("tick"); void c.offsetWidth; c.classList.add("tick");
      $("#state-sub").textContent = `T-${String(d.n).padStart(2, "0")} s`;
      alarm(d.n);
    } else if (was) {
      setState(ui.state);
      if (d.aborted) $("#state-sub").textContent = "autodestrucción abortada";
    }
  },
  status: (s) => {
    const llm = $("#st-llm");
    llm.className = "pill " + (s.llm ? "on" : "");
    $("#st-model").textContent = s.model || "—";
    $("#st-stt").className = "pill " + (s.stt ? "on" : "wait");
  },
};

window.jarvis = {
  onEvents(batch) {
    for (const ev of batch) {
      try { (handlers[ev.type] || (() => {}))(ev.data); } catch (e) { console.error(e); }
    }
  },
};

/* ================= Sonido de activación ================= */
let actx;
function beep() {
  try {
    actx = actx || new AudioContext();
    const o = actx.createOscillator(), g = actx.createGain();
    o.type = "sine";
    o.frequency.setValueAtTime(880, actx.currentTime);
    o.frequency.exponentialRampToValueAtTime(1760, actx.currentTime + 0.09);
    g.gain.setValueAtTime(0.0001, actx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.12, actx.currentTime + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, actx.currentTime + 0.18);
    o.connect(g).connect(actx.destination);
    o.start(); o.stop(actx.currentTime + 0.2);
  } catch (e) { /* sin audio */ }
}

// Alarma de la cuenta atrás: n = -1 al iniciar, 0 al final.
function alarm(n) {
  try {
    actx = actx || new AudioContext();
    const t = actx.currentTime;
    const tones = n === -1 ? [[660, 0], [440, 0.18], [660, 0.36], [440, 0.54]]
      : n === 0 ? [[180, 0]] : [[n <= 3 ? 1320 : 990, 0]];
    const len = n === 0 ? 1.2 : 0.14;
    for (const [f, d] of tones) {
      const o = actx.createOscillator(), g = actx.createGain();
      o.type = "square";
      o.frequency.setValueAtTime(f, t + d);
      g.gain.setValueAtTime(0.0001, t + d);
      g.gain.exponentialRampToValueAtTime(0.06, t + d + 0.01);
      g.gain.exponentialRampToValueAtTime(0.0001, t + d + len);
      o.connect(g).connect(actx.destination);
      o.start(t + d); o.stop(t + d + len + 0.02);
    }
  } catch (e) { /* sin audio */ }
}

/* ================= Telemetría ================= */
const hist = { cpu: [], ram: [], gpu: [] };
const HIST_LEN = 60;

function setGauge(id, value, sub) {
  const g = $(id);
  const v = value == null ? 0 : value;
  g.querySelector(".bar").style.strokeDashoffset = 314.16 * (1 - Math.min(v, 100) / 100);
  g.querySelector("b").textContent = value == null ? "--" : Math.round(v);
  g.classList.toggle("warn", v >= 70 && v < 90);
  g.classList.toggle("crit", v >= 90);
  if (sub != null) g.querySelector("sub").textContent = sub;
}

function fmtRate(b) {
  if (b > 1048576) return (b / 1048576).toFixed(1) + " MB/s";
  if (b > 1024) return (b / 1024).toFixed(0) + " KB/s";
  return b.toFixed(0) + " B/s";
}
function fmtUptime(s) {
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return (d ? d + "d " : "") + String(h).padStart(2, "0") + "h " + String(m).padStart(2, "0") + "m";
}

function renderStats(s) {
  if (!s) return;
  setGauge("#g-cpu", s.cpu, `${s.cpu_freq} GHz · ${s.cores.length} hilos`);
  setGauge("#g-ram", s.ram, `${s.ram_used} / ${s.ram_total} GB`);
  setGauge("#g-gpu", s.gpu, s.gpu == null ? "no disponible" : `${s.gpu_temp} °C`);
  setGauge("#g-vram", s.vram, s.vram == null ? "—" : `${s.vram_used} / ${s.vram_total} GB`);

  const cores = $("#cores");
  if (cores.children.length !== s.cores.length) {
    cores.innerHTML = s.cores.map(() => "<div><i></i></div>").join("");
  }
  s.cores.forEach((c, i) => { cores.children[i].firstChild.style.height = c + "%"; });

  $("#i-temp").textContent = s.gpu_temp == null ? "—" : s.gpu_temp + " °C";
  $("#i-disk").textContent = `${s.disk}% · ${s.disk_free} GB libres`;
  $("#i-down").textContent = fmtRate(s.net_down);
  $("#i-up").textContent = fmtRate(s.net_up);
  $("#i-procs").textContent = s.procs;
  $("#i-gpuname").textContent = (s.gpu_name || "—").replace("NVIDIA GeForce ", "");
  $("#uptime").textContent = "ACTIVO " + fmtUptime(s.uptime);

  for (const k of ["cpu", "ram", "gpu"]) {
    hist[k].push(s[k] || 0);
    if (hist[k].length > HIST_LEN) hist[k].shift();
  }
  drawHistory();
}

function drawHistory() {
  const c = $("#history"), dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth, h = c.clientHeight;
  if (c.width !== w * dpr) { c.width = w * dpr; c.height = h * dpr; }
  const x = c.getContext("2d");
  x.setTransform(dpr, 0, 0, dpr, 0, 0);
  x.clearRect(0, 0, w, h);
  x.strokeStyle = "rgba(63,224,255,0.08)";
  x.lineWidth = 1;
  for (let i = 1; i < 4; i++) { x.beginPath(); x.moveTo(0, h * i / 4); x.lineTo(w, h * i / 4); x.stroke(); }
  const series = [["ram", "69,255,176"], ["gpu", "255,182,72"], ["cpu", "63,224,255"]];
  for (const [k, col] of series) {
    const d = hist[k];
    if (d.length < 2) continue;
    x.beginPath();
    d.forEach((v, i) => {
      const px = w - (d.length - 1 - i) * (w / (HIST_LEN - 1)), py = h - (v / 100) * (h - 4) - 2;
      i ? x.lineTo(px, py) : x.moveTo(px, py);
    });
    x.strokeStyle = `rgb(${col})`; x.lineWidth = 1.5; x.shadowColor = `rgb(${col})`; x.shadowBlur = 6;
    x.stroke();
    x.shadowBlur = 0;
    x.lineTo(w, h); x.lineTo(w - (d.length - 1) * (w / (HIST_LEN - 1)), h); x.closePath();
    x.fillStyle = `rgba(${col},0.06)`; x.fill();
  }
}

async function pollStats() {
  try { renderStats(await api().get_stats()); } catch (e) { /* aún arrancando */ }
  setTimeout(pollStats, 1000);
}

/* ================= Reactor (animación central) ================= */
const rc = $("#reactor");
const rx = rc.getContext("2d");
const BARS = 96;
const barVals = new Float32Array(BARS);
let t0 = performance.now();

function resizeReactor() {
  const dpr = window.devicePixelRatio || 1;
  rc.width = rc.clientWidth * dpr; rc.height = rc.clientHeight * dpr;
  rx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
window.addEventListener("resize", resizeReactor);

function lerp(a, b, k) { return a + (b - a) * k; }

function arc(r, a0, a1, width, alpha, col) {
  rx.beginPath();
  rx.arc(0, 0, r, a0, a1);
  rx.lineWidth = width;
  rx.strokeStyle = `rgba(${col},${alpha})`;
  rx.stroke();
}

function drawReactor(ts) {
  const t = (ts - t0) / 1000;
  const w = rc.clientWidth, h = rc.clientHeight;
  rx.clearRect(0, 0, w, h);
  const R = Math.min(w, h * 0.92) * 0.40;
  if (R <= 10) return requestAnimationFrame(drawReactor);

  // Transición suave de color y nivel
  const target = ui.destruct ? [255, 36, 56] : PALETTE[ui.state] || PALETTE.idle;
  ui.color = ui.color.map((c, i) => lerp(c, target[i], 0.06));
  const col = ui.color.map(Math.round).join(",");
  ui.smooth = lerp(ui.smooth, ui.level, ui.level > ui.smooth ? 0.35 : 0.08);
  const busy = ui.state === "thinking" || ui.state === "transcribing" || ui.state === "booting";
  const speed = ui.destruct ? 5 : busy ? 3 : 1;
  const lv = ui.smooth;

  rx.save();
  rx.translate(w / 2, h * 0.46);
  rx.shadowColor = `rgb(${col})`;

  // Halo exterior
  const halo = rx.createRadialGradient(0, 0, R * 0.2, 0, 0, R * 1.35);
  halo.addColorStop(0, `rgba(${col},${0.10 + lv * 0.25})`);
  halo.addColorStop(1, "rgba(0,0,0,0)");
  rx.fillStyle = halo;
  rx.beginPath(); rx.arc(0, 0, R * 1.35, 0, Math.PI * 2); rx.fill();

  // Anillo exterior con marcas
  rx.save();
  rx.rotate(t * 0.05 * speed);
  for (let i = 0; i < 180; i++) {
    const a = (i / 180) * Math.PI * 2, long = i % 15 === 0;
    rx.beginPath();
    rx.moveTo(Math.cos(a) * R * 1.12, Math.sin(a) * R * 1.12);
    rx.lineTo(Math.cos(a) * R * (long ? 1.19 : 1.15), Math.sin(a) * R * (long ? 1.19 : 1.15));
    rx.strokeStyle = `rgba(${col},${long ? 0.8 : 0.3})`;
    rx.lineWidth = long ? 2 : 1;
    rx.stroke();
  }
  rx.restore();
  arc(R * 1.08, 0, Math.PI * 2, 1, 0.35, col);

  // Texto orbital
  rx.save();
  rx.rotate(-t * 0.08 * speed);
  rx.font = `${Math.max(9, R * 0.045)}px Bahnschrift, Segoe UI`;
  rx.fillStyle = `rgba(${col},0.55)`;
  const txt = ui.destruct
    ? ` ⚠ AUTODESTRUCCIÓN · NÚCLEO INESTABLE · ${ui.name || "JARVIS"} · CÓDIGO 00 ·`.repeat(2)
    : ` ${ui.name || "JARVIS"} · NÚCLEO LOCAL · ${(STATES[ui.state] || [ui.state])[0]} · SISTEMAS NOMINALES ·`.repeat(2);
  const step = (Math.PI * 2) / txt.length;
  for (let i = 0; i < txt.length; i++) {
    rx.save(); rx.rotate(i * step); rx.translate(0, -R * 1.0); rx.fillText(txt[i], 0, 0); rx.restore();
  }
  rx.restore();

  // Arcos segmentados que giran en sentidos opuestos
  rx.shadowBlur = 12;
  rx.save(); rx.rotate(t * 0.6 * speed);
  for (let i = 0; i < 3; i++) arc(R * 0.9, i * 2.094, i * 2.094 + 1.3, 4, 0.85, col);
  rx.restore();
  rx.save(); rx.rotate(-t * 0.9 * speed);
  for (let i = 0; i < 6; i++) arc(R * 0.83, i * 1.047, i * 1.047 + 0.6, 2, 0.6, col);
  rx.restore();
  rx.save(); rx.rotate(t * 0.25 * speed);
  for (let i = 0; i < 24; i++) arc(R * 0.76, i * 0.2618, i * 0.2618 + 0.18, 6, 0.25 + 0.2 * (i % 2), col);
  rx.restore();
  rx.shadowBlur = 0;

  // Barras de audio radiales
  for (let i = 0; i < BARS; i++) {
    const n = Math.sin(i * 0.7 + t * 6) * 0.5 + Math.sin(i * 1.9 - t * 4.3) * 0.5;
    const tgt = lv * (0.45 + 0.55 * Math.abs(n)) + (busy ? 0.08 * Math.abs(Math.sin(i * 0.3 + t * 5)) : 0.02);
    barVals[i] = lerp(barVals[i], tgt, 0.3);
    const a = (i / BARS) * Math.PI * 2 - Math.PI / 2;
    const r0 = R * 0.56, r1 = r0 + R * 0.16 * barVals[i] + 2;
    rx.beginPath();
    rx.moveTo(Math.cos(a) * r0, Math.sin(a) * r0);
    rx.lineTo(Math.cos(a) * r1, Math.sin(a) * r1);
    rx.strokeStyle = `rgba(${col},${0.35 + barVals[i] * 0.65})`;
    rx.lineWidth = 2;
    rx.stroke();
  }

  // Anillo interior
  arc(R * 0.54, 0, Math.PI * 2, 1.5, 0.7, col);
  rx.save(); rx.rotate(-t * 1.5 * speed);
  rx.setLineDash([3, 6]); arc(R * 0.48, 0, Math.PI * 2, 2, 0.5, col); rx.setLineDash([]);
  rx.restore();

  // Núcleo (reactor arc)
  const pulse = 1 + Math.sin(t * 2.2) * 0.03 + lv * 0.18;
  const coreR = R * 0.34 * pulse;
  const g = rx.createRadialGradient(0, 0, 0, 0, 0, coreR);
  g.addColorStop(0, "rgba(255,255,255,0.95)");
  g.addColorStop(0.25, `rgba(${col},0.9)`);
  g.addColorStop(0.6, `rgba(${col},0.25)`);
  g.addColorStop(1, "rgba(0,0,0,0)");
  rx.fillStyle = g;
  rx.beginPath(); rx.arc(0, 0, coreR, 0, Math.PI * 2); rx.fill();

  // Triángulo interior al estilo del reactor de Stark
  rx.save(); rx.rotate(t * 0.2 * speed);
  rx.beginPath();
  for (let i = 0; i < 3; i++) {
    const a = i * 2.094 - Math.PI / 2;
    rx.lineTo(Math.cos(a) * R * 0.27, Math.sin(a) * R * 0.27);
  }
  rx.closePath();
  rx.strokeStyle = `rgba(${col},0.6)`; rx.lineWidth = 1.5; rx.shadowBlur = 10; rx.stroke();
  rx.restore();

  rx.restore();
  requestAnimationFrame(drawReactor);
}

/* ================= Controles ================= */
$("#btn-mic").addEventListener("click", () => api() && api().listen());
$("#btn-stop").addEventListener("click", () => api() && api().stop());
$("#btn-voice").addEventListener("click", () => {
  if (ui.destruct) return;
  const b = $("#btn-voice");
  b.classList.toggle("off");
  api() && api().set_voice(!b.classList.contains("off"));
});
$("#chk-continuous").addEventListener("change", (e) => api() && api().set_continuous(e.target.checked));
$("#mic-select").addEventListener("change", (e) => api() && api().set_microfono(e.target.value));
document.querySelectorAll("[data-media]").forEach((b) =>
  b.addEventListener("click", () => api() && api().media(b.dataset.media)));

$("#input-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const v = $("#input").value.trim();
  if (!v || !api()) return;
  $("#input").value = "";
  api().send_text(v);
});

// Barra espaciadora = hablar (cuando no se está escribiendo)
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && document.activeElement !== $("#input") && document.activeElement.tagName !== "SELECT") {
    e.preventDefault();
    api() && api().listen();
  }
  if (e.code === "Escape") api() && api().stop();
});

let started = false;
async function init() {
  if (started) return;
  started = true;
  const cfg = await api().get_init();
  ui.name = cfg.nombre;
  $("#brand-name").textContent = cfg.nombre.split("").join(".");
  $("#st-model").textContent = cfg.modelo;
  $("#hotkey").textContent = cfg.atajo.replace(/\b\w/g, (c) => c.toUpperCase());
  ui.wake = cfg.palabra.charAt(0).toUpperCase() + cfg.palabra.slice(1);
  $("#wake-word").textContent = ui.wake;
  STATES.listening_wake[1] = `di «${ui.wake}» seguido de tu orden`;
  $("#chk-continuous").checked = cfg.continua;
  $("#btn-voice").classList.toggle("off", !cfg.voz);

  const sel = $("#mic-select");
  sel.innerHTML = "";
  for (const m of cfg.microfonos) {
    const o = document.createElement("option");
    o.value = o.textContent = m;
    if (cfg.microfono && m.toLowerCase().includes(cfg.microfono.toLowerCase())) o.selected = true;
    sel.appendChild(o);
  }

  const qa = $("#quick-apps");
  qa.innerHTML = "";
  for (const a of cfg.accesos) {
    const b = document.createElement("button");
    b.textContent = a.replace("Visual Studio Code", "VS Code").replace("Google ", "").toUpperCase();
    b.title = "Abrir " + a;
    b.addEventListener("click", () => api().open_app(a));
    qa.appendChild(b);
  }
  pollStats();
}

resizeReactor();
setState("booting");
requestAnimationFrame(drawReactor);
window.addEventListener("pywebviewready", init);
