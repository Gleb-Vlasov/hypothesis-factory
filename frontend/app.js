"use strict";
const API = ""; // тот же origin (FastAPI отдаёт статику)

// Разграничение доступа (опционально, APP_ACCESS_KEY на сервере):
// при 401 однократно запрашиваем ключ и добавляем X-API-Key ко всем /api/-запросам
const _fetch = window.fetch.bind(window);
window.fetch = async (url, opts = {}) => {
  const isApi = String(url).includes("/api/");
  const key = localStorage.getItem("accessKey");
  if (isApi && key) opts.headers = { ...(opts.headers || {}), "X-API-Key": key };
  let r = await _fetch(url, opts);
  if (isApi && r.status === 401) {
    const k = window.prompt("Доступ к сервису защищён. Введите ключ доступа:");
    if (k) {
      localStorage.setItem("accessKey", k.trim());
      opts.headers = { ...(opts.headers || {}), "X-API-Key": k.trim() };
      r = await _fetch(url, opts);
    }
  }
  return r;
};
const GRINDING = new Set(["liberation", "coarse_liberation"]);

let selectedFile = null;
let selectedImages = [];   // приложенные схемы/регламенты (до 5)
let lastResult = null;
let chatHistory = [];
const MAX_IMAGES = 5;
const IMG_RE = /\.(png|jpe?g|webp|bmp)$/i;

const $ = (id) => document.getElementById(id);
const fmt = (x, d = 0) => (x == null ? "—" : Number(x).toLocaleString("ru-RU", { maximumFractionDigits: d }));
const money = (usd) => {
  if (usd == null) return "—";
  if (usd >= 1e9) return "$" + fmt(usd / 1e9, 2) + " млрд";
  if (usd >= 1e6) return "$" + fmt(usd / 1e6, 1) + " млн";
  if (usd >= 1e3) return "$" + fmt(usd / 1e3) + " тыс.";
  return "$" + fmt(usd);
};

// ---------- Статус ----------
async function loadStatus() {
  try {
    const r = await fetch(`${API}/api/health`);
    const s = await r.json();
    const b = $("statusBadge");
    if (s.llm_ready) {
      b.textContent = `LLM-режим · ${s.llm_model}`;
      b.className = "badge badge-llm";
    } else {
      b.textContent = "Базовый режим (без LLM) · диагноз + RAG";
      b.className = "badge badge-template";
    }
    b.title = `Корпус знаний: ${s.corpus_size} записей · семантика: ${s.semantic_rag ? "вкл" : "выкл"}`;
  } catch {
    $("statusBadge").textContent = "сервер недоступен";
  }
}

// ---------- Загрузка файла ----------
const dz = $("dropzone"), input = $("fileInput");
dz.addEventListener("click", () => input.click());
dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("drag"); });
dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
dz.addEventListener("drop", (e) => {
  e.preventDefault(); dz.classList.remove("drag");
  const files = [...e.dataTransfer.files];
  // изображения, брошенные в основную зону, уходят в приложенные схемы
  const imgs = files.filter((f) => IMG_RE.test(f.name));
  if (imgs.length) addImages(imgs);
  const xls = files.find((f) => !IMG_RE.test(f.name));
  if (xls) setFile(xls);
});
input.addEventListener("change", () => { if (input.files.length) setFile(input.files[0]); });

function setFile(f) {
  selectedFile = f;
  $("fileName").textContent = f.name;
  $("analyzeBtn").disabled = false;
}

// ---------- Приложенные схемы/регламенты ----------
$("imgAdd").addEventListener("click", () => $("imgInput").click());
$("imgInput").addEventListener("change", () => {
  addImages([...$("imgInput").files]);
  $("imgInput").value = "";
});

function addImages(files) {
  for (const f of files) {
    if (!IMG_RE.test(f.name)) continue;
    if (selectedImages.length >= MAX_IMAGES) break;
    if (!selectedImages.some((x) => x.name === f.name && x.size === f.size)) selectedImages.push(f);
  }
  renderImgChips();
}

function renderImgChips() {
  const box = $("imgChips");
  box.innerHTML = "";
  selectedImages.forEach((f, i) => {
    const chip = document.createElement("span");
    chip.className = "img-chip";
    chip.innerHTML = `🖼 ${escapeHtml(f.name)} <button class="img-x" title="Убрать">×</button>`;
    chip.querySelector(".img-x").addEventListener("click", () => {
      selectedImages.splice(i, 1);
      renderImgChips();
    });
    box.appendChild(chip);
  });
}

// ---------- База знаний ----------
async function loadKnowledge() {
  try {
    const r = await fetch(`${API}/api/knowledge`);
    const kb = await r.json();
    $("kbCount").textContent = `${fmt(kb.corpus_size)} фрагментов`;
    const list = $("kbList"); list.innerHTML = "";
    list.insertAdjacentHTML("beforeend",
      `<div class="kb-item builtin"><span class="kb-name">Вшитый корпус (учебники, экспертные решения, гайд)</span>` +
      `<span class="kb-frag">${fmt(kb.builtin_fragments)} фрагм.</span></div>`);
    (kb.user_documents || []).forEach((d) => {
      const meta = [d.added_at ? "добавлен " + d.added_at : "", d.note].filter(Boolean).join(" · ");
      list.insertAdjacentHTML("beforeend",
        `<div class="kb-item" ${meta ? `title="${escapeHtml(meta)}"` : ""}>` +
        `<span class="kb-name">${escapeHtml(d.title || d.source)}</span>` +
        `<span class="kb-frag">${fmt(d.fragments)} фрагм.</span></div>`);
    });
  } catch { /* сервер ещё поднимается — статус обновится позже */ }
}

$("kbAdd").addEventListener("click", () => $("kbInput").click());
$("kbInput").addEventListener("change", async () => {
  const files = [...$("kbInput").files];
  $("kbInput").value = "";
  const st = $("kbStatus");
  for (const f of files) {
    st.textContent = IMG_RE.test(f.name)
      ? `Расшифровывается схема: ${f.name}… (~15–40 с)`
      : `Индексируется: ${f.name}…`;
    try {
      const fd = new FormData();
      fd.append("file", f);
      // метаданные источника (авторы/год/условия) — попадают в цитирование
      for (const [k, id] of [["author", "kbAuthor"], ["year", "kbYear"], ["note", "kbNote"]]) {
        const v = $(id).value.trim();
        if (v) fd.append(k, v);
      }
      const r = await fetch(`${API}/api/knowledge`, { method: "POST", body: fd });
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: r.statusText }));
        throw new Error(err.detail || "ошибка");
      }
      const res = await r.json();
      st.textContent = res.added > 0
        ? (res.transcript_preview
            ? `Схема расшифрована и добавлена: ${f.name} (${res.fragments} фрагм.)`
            : `Добавлено: ${f.name} (${res.fragments} фрагм.)`)
        : `Уже в базе: ${f.name}`;
    } catch (e) {
      st.textContent = `Ошибка «${f.name}»: ${e.message}`;
    }
  }
  loadKnowledge();
});

$("analyzeBtn").addEventListener("click", analyze);
$("exportBtn").addEventListener("click", exportJson);
document.querySelectorAll("[data-export]").forEach((b) =>
  b.addEventListener("click", () => exportFmt(b.dataset.export)));

const EXPORT_NAMES = { docx: "hypotheses.docx", pdf: "hypotheses.pdf", csv: "hypotheses.csv",
                       tasks: "tasks_jira_youtrack.csv", protocol: "protocols.docx" };

async function exportFmt(fmt) {
  if (!lastResult) return;
  const r = await fetch(`${API}/api/export/${fmt}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(lastResult),
  });
  if (!r.ok) { alert("Ошибка экспорта"); return; }
  const blob = await r.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = EXPORT_NAMES[fmt] || `hypotheses.${fmt}`;
  a.click();
}

// Этапы анализа: первые три проходят быстро, генерация — долгая (до ~3 мин в LLM-режиме)
const STAGES = [
  [0, "Разбираю Excel-баланс…"],
  [2, "Считаю количественный диагноз по классам крупности…"],
  [6, "Ищу подтверждения в литературе (гибридный поиск)…"],
  [12, "Формулирую и обосновываю гипотезы…"],
];
// со схемами добавляется этап расшифровки (идёт параллельно, ~15–40 с)
const STAGES_IMG = [
  [0, "Разбираю Excel-баланс…"],
  [2, "Расшифровываю приложенные схемы и регламенты…"],
  [40, "Считаю количественный диагноз по классам крупности…"],
  [44, "Ищу подтверждения в литературе (гибридный поиск)…"],
  [50, "Формулирую и обосновываю гипотезы…"],
];
let loaderTimer = null;

function startLoader(withImages = false) {
  const stages = withImages ? STAGES_IMG : STAGES;
  const t0 = Date.now();
  const upd = () => {
    const sec = Math.floor((Date.now() - t0) / 1000);
    let txt = stages[0][1];
    for (const [at, label] of stages) if (sec >= at) txt = label;
    $("loaderText").textContent = txt;
    $("loaderElapsed").textContent = sec >= 15 ? `${sec} с` : "";
  };
  upd();
  loaderTimer = setInterval(upd, 1000);
  $("loader").classList.remove("hidden");
}

function stopLoader() {
  clearInterval(loaderTimer);
  loaderTimer = null;
  $("loader").classList.add("hidden");
}

async function analyze() {
  if (!selectedFile) return;
  $("errorBox").classList.add("hidden");
  $("results").classList.add("hidden");
  startLoader(selectedImages.length > 0);
  try {
    const fd = new FormData();
    fd.append("file", selectedFile);
    const goal = ($("goalInput").value || "").trim();
    if (goal) fd.append("goal", goal);
    for (const img of selectedImages) fd.append("images", img);
    const r = await fetch(`${API}/api/analyze`, { method: "POST", body: fd });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || "Ошибка анализа");
    }
    lastResult = await r.json();
    // новый анализ — новый диалог
    chatHistory = [];
    const log = $("chatLog");
    log.querySelectorAll(".msg").forEach((m, i) => { if (i > 0) m.remove(); });
    render(lastResult);
  } catch (e) {
    $("errorBox").textContent = "Ошибка: " + e.message;
    $("errorBox").classList.remove("hidden");
  } finally {
    stopLoader();
  }
}

// ---------- Диаграмма потерь по классам крупности ----------
const CHART_CLASS_ORDER = ["+125", "-125+71", "-71+45", "-45+20", "-20+10", "-10"];
// Валидированная пара для тёмной темы (те же смысловые оттенки, что и рамки карточек)
const CHART_COLORS = { grind: "#0284c7", flot: "#b45309" };

function niceCeil(v) {
  const p = Math.pow(10, Math.floor(Math.log10(v || 1)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

function renderLossChart(data) {
  const wrap = $("lossChart");
  const signals = (data.diagnosis && data.diagnosis.signals) || [];
  if (!signals.length) { wrap.classList.add("hidden"); wrap.innerHTML = ""; return; }

  // Агрегация: класс → тонны по типу решения + разбивка по элементам
  const agg = new Map();
  for (const s of signals) {
    const c = s.size_class;
    if (!agg.has(c)) agg.set(c, { grind: 0, flot: 0, els: {} });
    const a = agg.get(c);
    const t = s.recoverable_t || 0;
    const key = GRINDING.has(s.mechanism) ? "grind" : "flot";
    a[key] += t;
    a.els[s.element] = (a.els[s.element] || 0) + t;
  }
  const classes = CHART_CLASS_ORDER.filter((c) => agg.has(c));
  if (!classes.length) { wrap.classList.add("hidden"); return; }

  const W = 720, H = 240, m = { l: 56, r: 10, t: 18, b: 30 };
  const plotW = W - m.l - m.r, plotH = H - m.t - m.b;
  const maxV = niceCeil(Math.max(...classes.map((c) => agg.get(c).grind + agg.get(c).flot)));
  const y = (v) => m.t + plotH * (1 - v / maxV);
  const step = plotW / classes.length;
  const barW = Math.min(56, step * 0.62);
  const GAP = 2, R = 4;

  // сегмент с закруглённым верхом — только у верхнего сегмента стека
  const rTop = (x, yTop, w, h) => {
    const r = Math.min(R, h / 2, w / 2);
    return `M${x},${yTop + h} L${x},${yTop + r} Q${x},${yTop} ${x + r},${yTop} ` +
           `L${x + w - r},${yTop} Q${x + w},${yTop} ${x + w},${yTop + r} L${x + w},${yTop + h} Z`;
  };

  let grid = "", bars = "", labels = "";
  for (let i = 1; i <= 4; i++) {
    const gy = y((maxV * i) / 4);
    grid += `<line x1="${m.l}" y1="${gy}" x2="${W - m.r}" y2="${gy}" stroke="#273140" stroke-width="1"/>` +
            `<text x="${m.l - 8}" y="${gy + 4}" text-anchor="end" font-size="11" fill="#8b98a9">${fmt((maxV * i) / 4)}</text>`;
  }
  grid += `<line x1="${m.l}" y1="${y(0)}" x2="${W - m.r}" y2="${y(0)}" stroke="#3a4657" stroke-width="1"/>`;

  classes.forEach((c, i) => {
    const a = agg.get(c);
    const cx = m.l + step * i + step / 2, x = cx - barW / 2;
    const hG = plotH * (a.grind / maxV), hF = plotH * (a.flot / maxV);
    const both = hG > 0.5 && hF > 0.5;
    const gTopY = y(a.grind);
    if (hG > 0.5) {
      bars += both
        ? `<rect x="${x}" y="${gTopY}" width="${barW}" height="${hG}" fill="${CHART_COLORS.grind}"/>`
        : `<path d="${rTop(x, gTopY, barW, hG)}" fill="${CHART_COLORS.grind}"/>`;
    }
    if (hF > 0.5) {
      const fTopY = gTopY - (both ? GAP : 0) - hF;
      bars += `<path d="${rTop(x, fTopY, barW, hF)}" fill="${CHART_COLORS.flot}"/>`;
    }
    const total = a.grind + a.flot;
    const topY = y(total) - (both ? GAP : 0);
    labels += `<text x="${cx}" y="${topY - 6}" text-anchor="middle" font-size="11.5" font-weight="600" fill="#e6edf3">${fmt(total)}</text>` +
              `<text x="${cx}" y="${H - 10}" text-anchor="middle" font-size="11" fill="#8b98a9">${escapeHtml(c)}</text>`;
    // широкая невидимая зона наведения — на весь столбец
    bars += `<rect class="lc-hit" data-class="${escapeHtml(c)}" x="${m.l + step * i}" y="${m.t}" width="${step}" height="${plotH}" fill="transparent"/>`;
  });

  wrap.innerHTML = `
    <div class="lc-head">
      <div class="lc-title">Извлекаемые потери по классам крупности, т</div>
      <div class="lc-legend">
        <span><span class="lc-dot" style="background:${CHART_COLORS.grind}"></span>Измельчение / классификация</span>
        <span><span class="lc-dot" style="background:${CHART_COLORS.flot}"></span>Флотация</span>
      </div>
    </div>
    <svg viewBox="0 0 ${W} ${H}" width="100%" role="img"
         aria-label="Извлекаемые потери по классам крупности">${grid}${bars}${labels}</svg>
    <div class="lc-tip hidden"></div>`;

  const tip = wrap.querySelector(".lc-tip");
  wrap.querySelectorAll(".lc-hit").forEach((hit) => {
    hit.addEventListener("mousemove", (e) => {
      const a = agg.get(hit.dataset.class);
      const els = Object.entries(a.els).map(([el, t]) => `${el}: ${fmt(t)} т`).join(" · ");
      tip.innerHTML = `<b>${escapeHtml(hit.dataset.class)} мкм</b> — ${fmt(a.grind + a.flot)} т извлекаемых потерь<br>
        <span class="lc-dot" style="background:${CHART_COLORS.grind}"></span>Измельчение/классификация: ${fmt(a.grind)} т<br>
        <span class="lc-dot" style="background:${CHART_COLORS.flot}"></span>Флотация: ${fmt(a.flot)} т
        ${els ? `<br><span class="lc-tip-els">${els}</span>` : ""}`;
      const r = wrap.getBoundingClientRect();
      tip.classList.remove("hidden");
      const tw = tip.offsetWidth, left = Math.min(Math.max(e.clientX - r.left + 14, 4), r.width - tw - 4);
      tip.style.left = left + "px";
      tip.style.top = (e.clientY - r.top - tip.offsetHeight - 12) + "px";
    });
    hit.addEventListener("mouseleave", () => tip.classList.add("hidden"));
  });
  wrap.classList.remove("hidden");
}

// ---------- Граф связей: поток → класс крупности → гипотеза ----------
function renderInfluenceGraph(data) {
  const block = $("graphBlock"), wrap = $("linkGraph");
  const signals = (data.diagnosis && data.diagnosis.signals) || [];
  const hyps = data.hypotheses || [];
  if (!signals.length || !hyps.length) { block.classList.add("hidden"); wrap.innerHTML = ""; return; }

  const streams = [...new Set(signals.map((s) => s.stream_kind))];
  const classes = CHART_CLASS_ORDER.filter((c) => signals.some((s) => s.size_class === c));

  // рёбра поток→класс из сигналов диагноза
  const scE = new Map();
  for (const s of signals) {
    const k = s.stream_kind + "|" + s.size_class;
    const e = scE.get(k) || { s: s.stream_kind, c: s.size_class, t: 0, g: 0 };
    e.t += s.recoverable_t || 0;
    if (GRINDING.has(s.mechanism)) e.g += s.recoverable_t || 0;
    scE.set(k, e);
  }
  // рёбра класс→гипотеза из адресации гипотез
  const chE = [];
  hyps.forEach((h, hi) => {
    const cs = ((h.target || {}).size_classes || []).filter((c) => classes.includes(c));
    const t = ((h.expected_value || {}).addressable_recoverable_t || 0) / (cs.length || 1);
    cs.forEach((c) => chE.push({ c, hi, t, grind: GRINDING.has((h.target || {}).mechanism) }));
  });
  const maxT = Math.max(...[...scE.values()].map((e) => e.t), ...chE.map((e) => e.t), 1);
  const width = (t) => 1.5 + 9 * Math.sqrt(t / maxT);
  const color = (isG) => (isG ? CHART_COLORS.grind : CHART_COLORS.flot);

  const W = 900, rowH = 58, top = 16;
  const n = Math.max(streams.length, classes.length, hyps.length);
  const H = top * 2 + n * rowH;
  const yOf = (i, count) => top + i * rowH + (H - 2 * top - count * rowH) / 2 + rowH / 2;
  const S = { x: 8, w: 170 }, C = { x: 385, w: 130 }, Hn = { x: 610, w: 282 };

  const sY = new Map(streams.map((s, i) => [s, yOf(i, streams.length)]));
  const cY = new Map(classes.map((c, i) => [c, yOf(i, classes.length)]));
  const hY = hyps.map((_, i) => yOf(i, hyps.length));

  const bez = (x1, y1, x2, y2) =>
    `M${x1},${y1} C${(x1 + x2) / 2},${y1} ${(x1 + x2) / 2},${y2} ${x2},${y2}`;

  let edges = "", nodes = "";
  for (const e of scE.values()) {
    edges += `<path class="lg-edge" data-n="s:${escapeHtml(e.s)} c:${escapeHtml(e.c)}"
      d="${bez(S.x + S.w, sY.get(e.s), C.x, cY.get(e.c))}" fill="none"
      stroke="${color(e.g >= e.t / 2)}" stroke-width="${width(e.t)}" stroke-opacity=".45"/>`;
  }
  for (const e of chE) {
    edges += `<path class="lg-edge" data-n="c:${escapeHtml(e.c)} h:${e.hi}"
      d="${bez(C.x + C.w, cY.get(e.c), Hn.x, hY[e.hi])}" fill="none"
      stroke="${color(e.grind)}" stroke-width="${width(e.t)}" stroke-opacity=".45"/>`;
  }
  const nodeRect = (x, y, w, key, label, sub, accent) => `
    <g class="lg-node" data-k="${key}">
      <rect x="${x}" y="${y - 21}" width="${w}" height="42" rx="9"
        fill="#1c232d" stroke="#273140"/>
      ${accent ? `<rect x="${x}" y="${y - 21}" width="3.5" height="42" rx="1.5" fill="${accent}"/>` : ""}
      <text x="${x + 12}" y="${y - 3}" font-size="12" font-weight="600" fill="#e6edf3">${label}</text>
      <text x="${x + 12}" y="${y + 13}" font-size="10.5" fill="#8b98a9">${sub}</text>
    </g>`;
  streams.forEach((s) => {
    const t = [...scE.values()].filter((e) => e.s === s).reduce((a, e) => a + e.t, 0);
    nodes += nodeRect(S.x, sY.get(s), S.w, `s:${escapeHtml(s)}`, escapeHtml(s), `${fmt(t)} т извлекаемых`, "");
  });
  classes.forEach((c) => {
    const t = [...scE.values()].filter((e) => e.c === c).reduce((a, e) => a + e.t, 0);
    nodes += nodeRect(C.x, cY.get(c), C.w, `c:${escapeHtml(c)}`, escapeHtml(c) + " мкм", `${fmt(t)} т`, "");
  });
  hyps.forEach((h, i) => {
    const short = h.title.length > 34 ? h.title.slice(0, 33) + "…" : h.title;
    nodes += nodeRect(Hn.x, hY[i], Hn.w, `h:${i}`,
      `${escapeHtml(h.id)} · ${escapeHtml(short)}`,
      `${fmt((h.expected_value || {}).addressable_recoverable_t)} т · приоритет ${h.priority_score != null ? h.priority_score : "—"}`,
      color(GRINDING.has((h.target || {}).mechanism)));
  });

  wrap.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img"
    aria-label="Граф связей: потоки, классы крупности, гипотезы">${edges}${nodes}</svg>`;

  // подсветка связей при наведении на узел
  const svg = wrap.querySelector("svg");
  wrap.querySelectorAll(".lg-node").forEach((node) => {
    node.addEventListener("mouseenter", () => {
      const k = node.dataset.k;
      svg.querySelectorAll(".lg-edge").forEach((e) => {
        e.setAttribute("stroke-opacity", e.dataset.n.includes(k) ? ".9" : ".08");
      });
    });
    node.addEventListener("mouseleave", () => {
      svg.querySelectorAll(".lg-edge").forEach((e) => e.setAttribute("stroke-opacity", ".45"));
    });
  });
  block.classList.remove("hidden");
}

// ---------- Рендер ----------
function render(data) {
  const rep = data.report || {};
  $("sumFile").textContent = rep.source_file || "Результат анализа";

  // Метрики
  const m = $("sumMetrics"); m.innerHTML = "";
  const streams = (rep.streams || []).map((s) => `<span class="chip">${s.kind} · ${fmt(s.smt)} т</span>`).join("");
  const cards = [
    ["Переработка, СМТ", fmt(rep.feed_smt)],
    ["Хвосты, СМТ", fmt(rep.tails_smt)],
    ["Потери Элемент 28", fmt(rep.tails_loss_t_28) + " т"],
    ["Потери Элемент 29", fmt(rep.tails_loss_t_29) + " т"],
  ];
  m.innerHTML = cards.map(([k, v]) => `<div class="metric"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("")
    + `<div class="metric" style="flex:1"><div class="k">Потоки хвостов</div><div class="v" style="font-size:14px"><div class="chips">${streams}</div></div></div>`;

  // Стратегия
  $("strategyBanner").textContent = data.dominant_strategy || "";

  // Целевая задача пользователя (если задавалась)
  const ug = data.meta && data.meta.user_goal;
  const ge = $("goalEcho");
  if (ug) {
    ge.textContent = "Целевая задача: " + ug;
    ge.classList.remove("hidden");
  } else {
    ge.classList.add("hidden");
  }

  // Баланс потерь: извлекаемо vs неизвлекаемо
  const totals = (data.diagnosis && data.diagnosis.totals) || {};
  const lb = $("lossBars"); lb.innerHTML = "";
  Object.entries(totals).forEach(([el, t]) => {
    const rec = t.recoverable_t || 0, non = t.nonrecoverable_t || 0, tot = rec + non || 1;
    const pr = (100 * rec / tot).toFixed(0);
    lb.insertAdjacentHTML("beforeend", `
      <div class="lossrow">
        <div class="lbl">${el}</div>
        <div class="bar"><div class="rec" style="width:${100 * rec / tot}%"></div><div class="non" style="width:${100 * non / tot}%"></div></div>
        <div class="val">извлек. ${fmt(rec)} т (${pr}%) · неизвл. ${fmt(non)} т</div>
      </div>`);
  });

  // Предупреждения парсера (неполные/нестандартные данные обработаны, но с оговорками)
  const warns = rep.warnings || [];
  const wb = $("warnBox");
  if (warns.length) {
    wb.innerHTML = `<b>Данные обработаны с ${warns.length} предупрежд.</b> (файл неполный или нестандартный — анализ выполнен по доступной части):` +
      `<ul>${warns.slice(0, 6).map((w) => `<li>${escapeHtml(w)}</li>`).join("")}</ul>` +
      (warns.length > 6 ? `<span class="warn-more">…и ещё ${warns.length - 6}</span>` : "");
    wb.classList.remove("hidden");
  } else {
    wb.classList.add("hidden");
  }

  // Расшифровки приложенных схем/регламентов (учтены в генерации)
  const imgs = ((data.meta || {}).images || []).filter((m) => m.transcript);
  const it = $("imgTranscripts");
  if (imgs.length) {
    it.innerHTML = imgs.map((m) =>
      `<details class="img-tr"><summary>🖼 Схема учтена: <b>${escapeHtml(m.name)}</b>` +
      `<span class="img-tr-hint"> — расшифровка модели (клик, чтобы раскрыть)</span></summary>` +
      `<pre class="img-tr-text">${escapeHtml(m.transcript)}</pre></details>`).join("");
    it.classList.remove("hidden");
  } else {
    it.innerHTML = "";
    it.classList.add("hidden");
  }

  // Диаграмма потерь по классам крупности
  renderLossChart(data);

  // Гипотезы
  const hyps = data.hypotheses || [];
  $("hypCount").textContent = `${hyps.length} · ${data.meta && data.meta.mode === "llm" ? "LLM" : "базовый режим"} · карточки раскрываются по клику`;
  const list = $("hypList"); list.innerHTML = "";
  hyps.forEach((h) => list.appendChild(hypCard(h)));
  // первая (самая ценная) гипотеза раскрыта сразу — видны все поля: обоснование,
  // механизм, ценность, новизна, источники, риски, дорожная карта
  if (list.firstChild) list.firstChild.classList.add("open");

  // граф связей: поток → класс крупности → гипотеза
  renderInfluenceGraph(data);

  $("results").classList.remove("hidden");
  $("results").scrollIntoView({ behavior: "smooth", block: "start" });
}

function econAssumptions() {
  const p = (lastResult && lastResult.meta && lastResult.meta.prices) || null;
  if (!p) return "";
  const parts = ["Элемент 28", "Элемент 29"]
    .filter((el) => p[el])
    .map((el) => `${el} — $${fmt(p[el])}/т`);
  return `Допущения: референсные цены ${parts.join(", ")} (настраиваются); ` +
         "оценка валового эффекта без учёта затрат на переработку и внедрение.";
}

function hypCard(h) {
  const el = document.createElement("div");
  const isGrind = GRINDING.has(h.target && h.target.mechanism);
  el.className = "hyp" + (isGrind ? "" : " flot");
  const classes = (h.target && h.target.size_classes || []).join(", ");
  const nov = h.novelty || { label: "—", score: "" };
  const val = h.expected_value || {};
  const src = (h.sources || []).map((s) =>
    `<div class="src">${escapeHtml(s.title)}${s.page ? ", с." + s.page : ""}${s.quote ? `<div class="q">«${escapeHtml(s.quote)}»</div>` : ""}</div>`).join("") || "<p>—</p>";
  const road = (h.verification_roadmap || []).map((v) =>
    `<div class="step"><b>${escapeHtml(v.step)}</b>${v.resources ? "<br>Ресурсы: " + escapeHtml(v.resources) : ""}${v.success_criteria ? `<br><span class="sc">Критерий: ${escapeHtml(v.success_criteria)}</span>` : ""}</div>`).join("") || "<p>—</p>";
  const tech = (h.risks && h.risks.technical || []).map((x) => `<li>${escapeHtml(x)}</li>`).join("");
  const econ = (h.risks && h.risks.economic || []).map((x) => `<li>${escapeHtml(x)}</li>`).join("");

  el.innerHTML = `
    <div class="hyp-head">
      <div class="hyp-num">${h.id}</div>
      <div class="hyp-title">
        <h3>${escapeHtml(h.title)}</h3>
        <div class="stmt">${escapeHtml(h.statement)}</div>
        <div class="hyp-meta">
          <span class="tag">${escapeHtml(h.target ? h.target.stream : "")}</span>
          <span class="tag">классы: ${escapeHtml(classes)}</span>
          <span class="tag">${escapeHtml(h.target ? h.target.category : "")}</span>
          <span class="tag value">${fmt(val.addressable_recoverable_t)} т извлекаемых потерь</span>
          <span class="nov ${nov.label}">новизна: ${nov.label}${nov.score !== "" ? " · " + nov.score : ""}</span>
          ${h.priority_score != null ? `<span class="tag prio" title="Композитный ранг: 55% эффект (тонны) + 20% новизна + 25% реализуемость/уверенность">приоритет ${h.priority_score}</span>` : ""}
          <span class="tag conf" title="Оценка неопределённости: уверенность системы в гипотезе (0–1), учитывает вердикты эксперта">уверенность ${h.confidence != null ? h.confidence : "—"}</span>
          ${h.generated_by === "llm-refined" ? `<span class="tag refined">✎ переработана</span>` : ""}
          ${h.feedback_note ? `<span class="tag fbnote" title="${escapeHtml(h.feedback_note)}">учтён фидбэк</span>` : ""}
        </div>
      </div>
      <div class="hyp-chev" title="Развернуть">▾</div>
    </div>
    <div class="hyp-body">
      <div class="field"><div class="flabel">Обоснование</div><p>${escapeHtml(h.rationale)}</p></div>
      <div class="field"><div class="flabel">Механизм влияния</div><p>${escapeHtml(h.mechanism_of_influence)}</p></div>
      <div class="field"><div class="flabel">Ожидаемая ценность (влияние на KPI)</div><p>${escapeHtml(val.kpi_text || "")}</p></div>
      ${val.value_usd != null ? `<div class="field"><div class="flabel">Экономическая оценка</div>
        <div class="econ">
          <div class="econ-row"><span class="econ-k">Стоимость адресуемых извлекаемых потерь</span><span class="econ-v">${money(val.value_usd)}/год</span></div>
          <div class="econ-row"><span class="econ-k">Эффект при возврате 10 % адресуемого металла</span><span class="econ-v">≈ ${money(val.value_usd * 0.10)}/год</span></div>
          <div class="econ-row"><span class="econ-k">Эффект при возврате 25 % адресуемого металла</span><span class="econ-v">≈ ${money(val.value_usd * 0.25)}/год</span></div>
          <div class="econ-note">${econAssumptions()}</div>
        </div></div>` : ""}
      <div class="field"><div class="flabel">Оценка новизны</div><p>${escapeHtml(nov.label || "—")}${nov.score !== "" && nov.score != null ? ` (${nov.score} из 1)` : ""}${nov.explanation ? ". " + escapeHtml(nov.explanation) : ""}${nov.closest_known ? `<br><span class="closest">Ближайшее известное решение: «${escapeHtml(nov.closest_known)}»</span>` : ""}</p></div>
      <div class="field"><div class="flabel">Источники</div>${src}</div>
      <div class="field"><div class="flabel">Риски</div>
        <div class="risks">
          <div><b>Технические</b><ul>${tech || "<li>—</li>"}</ul></div>
          <div><b>Экономические</b><ul>${econ || "<li>—</li>"}</ul></div>
        </div>
      </div>
      <div class="field"><div class="flabel">Дорожная карта проверки</div><div class="road">${road}</div></div>
      ${h.feedback_note ? `<div class="fb-note">${escapeHtml(h.feedback_note)}</div>` : ""}
      <div class="fb" data-id="${h.id}">
        <span style="color:var(--muted);font-size:13px">Оценка эксперта:</span>
        <button class="btn" data-v="confirmed">✓ Подтвердить</button>
        <button class="btn" data-v="rejected">✗ Отклонить</button>
        <button class="btn" data-refine="1">✎ Переработать</button>
        <button class="btn btn-discuss" data-discuss="1">💬 Обсудить</button>
      </div>
      <div class="refine hidden">
        <textarea class="refine-inp" rows="1"
          placeholder="Что изменить? Например: без замены оборудования, только режимные меры"></textarea>
        <button class="btn btn-primary refine-go">Применить</button>
        <span class="refine-status"></span>
      </div>
    </div>`;

  el.querySelector(".hyp-head").addEventListener("click", () => el.classList.toggle("open"));
  el.querySelectorAll(".fb .btn[data-v]").forEach((btn) =>
    btn.addEventListener("click", (e) => { e.stopPropagation(); sendFeedback(h, btn.dataset.v, btn.closest(".fb")); }));
  const disc = el.querySelector("[data-discuss]");
  if (disc) disc.addEventListener("click", (e) => { e.stopPropagation(); discussHypothesis(h); });

  // Переработка гипотезы моделью по указанию эксперта
  const refineBox = el.querySelector(".refine");
  el.querySelector("[data-refine]").addEventListener("click", (e) => {
    e.stopPropagation();
    refineBox.classList.toggle("hidden");
    if (!refineBox.classList.contains("hidden")) refineBox.querySelector(".refine-inp").focus();
  });
  const rInp = refineBox.querySelector(".refine-inp");
  rInp.addEventListener("input", () => autoGrow(rInp));
  refineBox.querySelector(".refine-go").addEventListener("click", async (e) => {
    e.stopPropagation();
    const instruction = rInp.value.trim();
    if (!instruction) return;
    const go = refineBox.querySelector(".refine-go"), st = refineBox.querySelector(".refine-status");
    go.disabled = true;
    st.textContent = "Модель перерабатывает гипотезу (до минуты)…";
    try {
      const r = await fetch(`${API}/api/refine`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hypothesis: h, instruction, analysis: lastResult }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: r.statusText }));
        throw new Error(err.detail || "Ошибка");
      }
      const upd = (await r.json()).hypothesis;
      // обновляем состояние и карточку на месте
      const idx = (lastResult.hypotheses || []).findIndex((x) => x.id === h.id);
      if (idx >= 0) lastResult.hypotheses[idx] = upd;
      const fresh = hypCard(upd);
      fresh.classList.add("open");
      el.replaceWith(fresh);
      fetch(`${API}/api/feedback`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hypothesis_id: upd.id, verdict: "edited", title: upd.title,
                               instruction, file: lastResult.report && lastResult.report.source_file }),
      }).catch(() => {});
    } catch (err) {
      st.textContent = "Ошибка: " + err.message;
      go.disabled = false;
    }
  });
  return el;
}

// ---------- Диалог ----------
function discussHypothesis(h) {
  const inp = $("chatInput");
  inp.value = `По гипотезе ${h.id} «${h.title}»: `;
  inp.focus();
  inp.setSelectionRange(inp.value.length, inp.value.length);
  inp.scrollIntoView({ behavior: "smooth", block: "center" });
}

// Прокручиваем только ленту чата — страница остаётся на месте,
// поле ввода всегда в зоне видимости
function scrollChatLog() {
  const log = $("chatLog");
  log.scrollTo({ top: log.scrollHeight, behavior: "smooth" });
}

function chatBubble(role, text, sources) {
  const div = document.createElement("div");
  div.className = "msg " + role;
  let srcHtml = "";
  if (sources && sources.length) {
    const uniq = [...new Map(sources.map((s) => [s.title + "|" + s.page, s])).values()];
    srcHtml = `<div class="msg-src">Литература: ${uniq.map((s) =>
      escapeHtml(s.title) + (s.page ? ", с." + s.page : "")).join(" · ")}</div>`;
  }
  div.innerHTML = `<div class="bubble">${mdLite(text)}${srcHtml}</div>`;
  $("chatLog").appendChild(div);
  scrollChatLog();
  return div;
}

// Минимальный markdown: **жирный**, маркерные списки, переносы строк (после экранирования)
function mdLite(text) {
  const esc = escapeHtml(text);
  const lines = esc.split(/\r?\n/);
  let html = "", inList = false;
  for (const ln of lines) {
    const m = ln.match(/^\s*[-*•]\s+(.*)/);
    if (m) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${m[1]}</li>`;
    } else {
      if (inList) { html += "</ul>"; inList = false; }
      if (ln.trim()) html += `<p>${ln}</p>`;
    }
  }
  if (inList) html += "</ul>";
  return html.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/###\s*(.+?)<\/p>/g, "<b>$1</b></p>");
}

function typingBubble() {
  const div = document.createElement("div");
  div.className = "msg assistant";
  div.innerHTML = `<div class="bubble typing" title="Модель формулирует ответ">
    <span class="tdot"></span><span class="tdot"></span><span class="tdot"></span>
  </div>`;
  $("chatLog").appendChild(div);
  scrollChatLog();
  return div;
}

async function sendChat() {
  const inp = $("chatInput");
  const message = inp.value.trim();
  if (!message) return;
  inp.value = "";
  autoGrow(inp);
  chatBubble("user", message);
  const pending = typingBubble();
  $("chatSend").disabled = true;
  try {
    const r = await fetch(`${API}/api/chat`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, history: chatHistory, analysis: lastResult }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || "Ошибка");
    }
    const data = await r.json();
    pending.remove();
    chatBubble("assistant", data.reply, data.sources);
    chatHistory.push({ role: "user", content: message }, { role: "assistant", content: data.reply });
    if (chatHistory.length > 20) chatHistory = chatHistory.slice(-20);
  } catch (e) {
    pending.remove();
    chatBubble("assistant", "Не удалось получить ответ: " + e.message);
  } finally {
    $("chatSend").disabled = false;
    inp.focus();
  }
}

$("chatSend").addEventListener("click", sendChat);
$("chatInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); }
});

// Автовысота полей ввода: растут с текстом до max-height, дальше — внутренний скролл
function autoGrow(el) {
  el.style.height = "auto";
  el.style.height = el.scrollHeight + 2 + "px";
}
for (const id of ["chatInput", "goalInput"]) {
  const el = $(id);
  el.addEventListener("input", () => autoGrow(el));
}

async function sendFeedback(h, verdict, fbEl) {
  try {
    await fetch(`${API}/api/feedback`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hypothesis_id: h.id, verdict, title: h.title, file: lastResult.report && lastResult.report.source_file }),
    });
    fbEl.innerHTML = `<span class="done">Спасибо! Оценка «${verdict === "confirmed" ? "подтверждено" : "отклонено"}» сохранена.</span>`;
  } catch {
    fbEl.insertAdjacentHTML("beforeend", `<span style="color:var(--danger)">не сохранено</span>`);
  }
}

function exportJson() {
  if (!lastResult) return;
  const blob = new Blob([JSON.stringify(lastResult, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "hypotheses.json";
  a.click();
}

function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

loadStatus();
loadKnowledge();
