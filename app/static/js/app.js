const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = { user: null, period: "30d", poll: null, live: null, page: 1, files: [], passwordNudge: false, unmaskedCall: null };
const OPERATOR_BLOCKED = ["upload", "operators", "export", "dataset", "admin"];

function token() {
  return sessionStorage.getItem("rk_token");
}

function toast(msg, err = false) {
  const el = document.createElement("div");
  el.className = "toast" + (err ? " err" : "");
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

function networkError() {
  const host = location.hostname;
  if (location.protocol === "file:") {
    return "Файл открыт с диска. В папке проекта выполните python run.py и откройте http://127.0.0.1:8080";
  }
  if (host !== "127.0.0.1" && host !== "localhost" && host !== "::1") {
    return "Страница открыта не с этой машины. Кабинет доступен по адресу http://127.0.0.1:8080";
  }
  return "Программа не ответила. В папке проекта выполните python run.py и обновите http://127.0.0.1:8080";
}

async function api(path, opts = {}) {
  const headers = opts.headers ? { ...opts.headers } : {};
  const isLogin = path.startsWith("/auth/login");
  if (token() && !isLogin) headers.Authorization = `Bearer ${token()}`;
  let body;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  } else if (opts.body !== undefined) {
    body = opts.body;
  }
  let res;
  try {
    res = await fetch("/api" + path, { method: opts.method || "GET", headers, body });
  } catch {
    throw new Error(networkError());
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = Array.isArray(j.detail) ? "Проверьте введённые данные" : (j.detail || JSON.stringify(j));
    } catch {}
    if (res.status === 401 && !isLogin) {
      sessionStorage.removeItem("rk_token");
      state.user = null;
      showLogin();
      if (typeof detail === "string" && detail.includes("просто")) {
        const err = $("#login-error");
        if (err) {
          err.hidden = false;
          err.textContent = detail;
        }
      }
    }
    if (res.status === 403 && typeof detail === "string" && detail.includes("Смените стартовый пароль")) {
      openAccount();
    }
    throw new Error(typeof detail === "string" ? detail : "Ошибка запроса");
  }
  const ct = res.headers.get("content-type") || "";
  if (opts.raw) return res;
  if (ct.includes("application/json")) return res.json();
  return res;
}

function showLogin() {
  $("#login").hidden = false;
  $("#shell").hidden = true;
  document.body.classList.add("is-login");
  document.body.classList.remove("is-app", "role-operator", "role-admin");
  refreshGate();
}

async function refreshGate() {
  const status = $("#login-status");
  if (!status || $("#login").hidden) return;
  try {
    const res = await fetch("/api/health", { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error("down");
    status.textContent = "Программа на этой машине отвечает. Можно входить.";
  } catch {
    status.textContent = networkError();
  }
}

function initials(name) {
  const parts = String(name || "A").trim().split(/\s+/);
  return ((parts[0] && parts[0][0]) + (parts[1] ? parts[1][0] : "")).toUpperCase();
}

function setLive(fn, ms = 2000) {
  clearInterval(state.live);
  state.live = null;
  if (fn) state.live = setInterval(fn, ms);
}

function closeNav() {
  document.body.classList.remove("nav-open");
  const btn = $("#nav-toggle");
  if (btn) btn.setAttribute("aria-expanded", "false");
}

function openAccount() {
  const dlg = $("#account-dialog");
  showPwdError("");
  if (dlg && typeof dlg.showModal === "function" && !dlg.open) dlg.showModal();
  const field = $("#pwd-self [name=old_password]");
  if (field) field.focus();
}

function showBanner() {
  const el = $("#banner");
  if (!el) return;
  if (state.user?.must_change_password) {
    el.hidden = false;
    el.innerHTML = `<span>Вы вошли со стартовым паролем. Смените его, чтобы открыть кабинет.</span><button type="button" class="btn gold row" id="banner-pwd">Сменить пароль</button>`;
    $("#banner-pwd").onclick = openAccount;
  } else {
    el.hidden = true;
    el.innerHTML = "";
  }
}

function roleLabel(role) {
  if (role === "admin") return "администратор";
  if (role === "operator") return "оператор";
  return "аналитик";
}

function applyRoleNav() {
  const role = state.user?.role || "";
  document.body.classList.toggle("role-operator", role === "operator");
  document.body.classList.toggle("role-admin", role === "admin");
  let n = 0;
  $$(".nav a").forEach((link) => {
    const name = link.dataset.nav;
    if (role === "operator") link.hidden = name !== "dashboard" && name !== "calls" && name !== "settings";
    else if (name === "admin") link.hidden = role !== "admin";
    else link.hidden = false;
    if (!link.hidden) {
      n += 1;
      const em = link.querySelector("em");
      if (em) em.textContent = String(n).padStart(2, "0");
    }
  });
  const side = $("#side-role");
  if (side) side.textContent = role === "operator" ? "кабинет оператора" : role === "admin" ? "кабинет администратора" : "локальный контур";
}

function syncPasswordDialog() {
  const cancel = $("#pwd-cancel");
  if (cancel) cancel.hidden = !!state.user?.must_change_password;
}

function showShell() {
  $("#login").hidden = true;
  $("#shell").hidden = false;
  document.body.classList.remove("is-login");
  document.body.classList.add("is-app");
  const name = state.user?.username || "";
  const role = state.user?.role || "";
  $("#who-name").textContent = name ? `${name} · ${roleLabel(role)}` : "";
  $("#who-av").textContent = initials(name || "•");
  applyRoleNav();
  showBanner();
  syncPasswordDialog();
  if (state.user?.must_change_password && !state.passwordNudge) {
    state.passwordNudge = true;
    openAccount();
  }
}

function fmtDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}
function fmtSec(v) {
  if (v == null || Number.isNaN(v)) return "—";
  if (v < 60) return `${Number(v).toFixed(1)} с`;
  return `${Math.floor(v / 60)} мин ${Math.round(v % 60)} с`;
}
function scoreClass(v) {
  if (v == null) return "";
  if (v >= 80) return "score-hi";
  if (v >= 55) return "score-mid";
  return "score-lo";
}
function pill(v) {
  if (v == null) return "—";
  const cls = v >= 80 ? "ok" : v >= 55 ? "mid" : "bad";
  return `<span class="pill ${cls}">${Number(v).toFixed(0)}</span>`;
}
function tonalityRu(t) {
  return { positive: "позитивная", negative: "негативная", neutral: "нейтральная" }[t] || t || "—";
}
const STATUSES = { queued: "в очереди", processing: "обрабатывается", done: "готово", error: "ошибка" };
function statusKey(s) {
  return Object.prototype.hasOwnProperty.call(STATUSES, s) ? s : "";
}
function statusRu(s) {
  return STATUSES[s] || "неизвестный статус";
}
function dossierLine(call) {
  const live = call.status === "queued" || call.status === "processing";
  const bits = [fmtDate(call.call_at), call.operator_name || "без оператора"];
  if (call.operator_external_id) bits.push("ID " + call.operator_external_id);
  bits.push(fmtSec(call.duration_sec));
  if (call.processed_sec) bits.push("обработка " + fmtSec(call.processed_sec));
  if (live) bits.push(`${statusRu(call.status)} ${call.progress || 0}%`);
  return bits.join(" · ");
}
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function mmss(sec) {
  const n = Math.max(0, Math.floor(sec || 0));
  return `${String(Math.floor(n / 60)).padStart(2, "0")}:${String(n % 60).padStart(2, "0")}`;
}
function slaText(duration, processed) {
  if (duration == null) return "";
  const budget = duration * 0.5;
  if (processed == null) return `норматив до ${fmtSec(budget)}`;
  const ok = processed <= budget;
  return `<span class="${ok ? "sla-ok" : "sla-bad"}">${ok ? "в нормативе" : "дольше норматива"}: ${fmtSec(processed)} из ${fmtSec(budget)}</span>`;
}
function highlight(text, q) {
  const safe = escapeHtml(text);
  const query = (q || "").trim();
  if (query.length < 2) return safe;
  const needle = escapeHtml(query).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return safe.replace(new RegExp(needle, "ig"), (m) => `<mark>${m}</mark>`);
}
function meter(value, cls = "meter", attrs = "") {
  const v = Math.max(0, Math.min(100, Number(value) || 0));
  return `<progress class="${cls}" max="100" value="${v.toFixed(1)}" ${attrs}></progress>`;
}

function passwordChecks(value) {
  const text = value || "";
  return {
    len: text.length >= 10 && text.length <= 128,
    letter: /[A-Za-zА-Яа-яЁё]/.test(text),
    digit: /\d/.test(text),
    symbol: /[^A-Za-zА-Яа-яЁё0-9]/.test(text),
    edge: text.length > 0 && !/^\s/.test(text),
  };
}

function passwordProblem(value) {
  const checks = passwordChecks(value);
  if (!value) return "Введите новый пароль";
  if (!checks.len) return "Пароль должен быть от 10 до 128 символов";
  if (!checks.edge) return "Пароль не должен начинаться с пробела";
  if (!checks.letter) return "В пароле нужна хотя бы одна буква";
  if (!checks.digit) return "В пароле нужна хотя бы одна цифра";
  if (!checks.symbol) return "В пароле нужен знак, который не является буквой или цифрой";
  return "";
}

function paintPasswordRules(input, list) {
  if (!input || !list) return;
  const checks = passwordChecks(input.value || "");
  list.querySelectorAll("[data-rule]").forEach((item) => {
    item.classList.toggle("ok", !!checks[item.dataset.rule]);
  });
}

function showPwdError(message) {
  const el = $("#pwd-error");
  if (!el) return;
  el.hidden = !message;
  el.textContent = message || "";
}

function metricBars(op) {
  const rows = [
    ["Вежливость", op.avg_politeness],
    ["Полнота", op.avg_completeness],
    ["Скрипт", op.avg_script],
    ["Решение", op.avg_resolution],
  ].filter((pair) => pair[1] != null);
  if (!rows.length) return "";
  return `<div class="bars mt">${rows.map(([label, v]) => `
    <div class="bar-row"><span>${label}</span>${meter(v)}<b>${Number(v).toFixed(0)}</b></div>
  `).join("")}</div>`;
}

function setNav(name, title, sub, kicker = "Кабинет качества") {
  $$(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === name));
  $("#page-title").textContent = title;
  $("#page-sub").textContent = sub;
  $("#page-kicker").textContent = kicker;
}

function hashParts() {
  return (location.hash || "#/").replace(/^#/, "").split("/").filter(Boolean);
}

function scoreGauge(value, label) {
  const v = Math.max(0, Math.min(100, Number(value) || 0));
  const r = 34;
  const c = 2 * Math.PI * r;
  const off = c * (1 - v / 100);
  return `<div class="gauge ${scoreClass(v)}">
    <svg viewBox="0 0 90 90" width="92" height="92">
      <circle cx="45" cy="45" r="${r}" fill="none" stroke="#ece4d4" stroke-width="6"/>
      <circle cx="45" cy="45" r="${r}" fill="none" stroke="currentColor" stroke-width="6"
        stroke-linecap="round" stroke-dasharray="${c.toFixed(2)}" stroke-dashoffset="${off.toFixed(2)}"
        transform="rotate(-90 45 45)"/>
      <text x="45" y="51" text-anchor="middle" font-size="18">${v.toFixed(0)}</text>
    </svg>
    <span>${label}</span>
  </div>`;
}

async function route() {
  setLive(null);
  state.callsToken = null;
  state.callToken = null;
  state.audioToken = null;
  closeNav();
  if (!token()) { showLogin(); return; }
  if (!state.user) {
    try { state.user = await api("/auth/me"); }
    catch { showLogin(); return; }
  }
  showShell();
  const parts = hashParts();
  const view = $("#view");
  if (state.user?.must_change_password) {
    view.innerHTML = `<div class="card featured"><h2>Смените пароль</h2><p class="muted">Кабинет откроется после смены стартового пароля. Пока он действует, звонки и отчёты недоступны.</p><button class="btn gold row" id="force-pwd" type="button">Сменить пароль</button></div>`;
    $("#force-pwd").onclick = openAccount;
    openAccount();
    return;
  }
  if (state.user?.role === "operator" && OPERATOR_BLOCKED.includes(parts[0])) {
    location.hash = "#/";
    return;
  }
  view.innerHTML = `<div class="card"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div>`;
  try {
    if (parts[0] === "calls" && parts[1]) await renderCall(view, parts[1]);
    else if (parts[0] === "calls") await renderCalls(view);
    else if (parts[0] === "upload") await renderUpload(view);
    else if (parts[0] === "operators") await renderOperators(view);
    else if (parts[0] === "export") await renderExport(view);
    else if (parts[0] === "dataset") await renderDataset(view);
    else if (parts[0] === "settings") {
      if (state.user?.role === "operator") await renderOperatorScript(view);
      else await renderSettings(view);
    }
    else if (parts[0] === "admin") await renderAdmin(view);
    else await renderDashboard(view);
    refreshQueueChip();
  } catch (e) {
    view.innerHTML = `<div class="card empty">${escapeHtml(e.message)}</div>`;
  }
}

async function refreshQueueChip() {
  const chip = $("#queue-chip");
  // Пока не сменён стартовый пароль, сервер отвечает 403 на всё, кроме смены пароля.
  // Оператор звонки не загружает, очередь ему не нужна.
  const skip = !state.user || state.user.must_change_password || state.user.role === "operator";
  chip.hidden = skip;
  if (skip) return;
  try {
    const q = await api("/queue");
    const n = q.queued + (q.processing?.length || 0);
    chip.textContent = n ? `очередь ${n}` : "очередь пуста";
    chip.classList.toggle("busy", n > 0);
  } catch {}
}

const TREND_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];

function trendDateParts(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
  if (!match) return null;
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  return { year: match[1], month, day };
}

function trendTickLabel(iso) {
  const parts = trendDateParts(iso);
  if (!parts) return "";
  return `${String(parts.day).padStart(2, "0")}.${String(parts.month).padStart(2, "0")}`;
}

function trendFullLabel(iso) {
  const parts = trendDateParts(iso);
  if (!parts) return String(iso || "");
  return `${parts.day} ${TREND_MONTHS[parts.month - 1]} ${parts.year}`;
}

function trendLabelIndexes(count) {
  if (count <= 1) return [0];
  const want = Math.min(count, count <= 7 ? count : 6);
  const indexes = [];
  for (let step = 0; step < want; step += 1) {
    const index = Math.round((step * (count - 1)) / (want - 1));
    if (!indexes.includes(index)) indexes.push(index);
  }
  return indexes;
}

function areaChart(el, points, key, color) {
  const w = 640;
  const h = 268;
  const plotL = 64;
  const plotR = w - 18;
  const plotT = 18;
  const plotB = h - 52;
  const plotW = plotR - plotL;
  const plotH = plotB - plotT;
  if (!points.length) {
    el.innerHTML = `<p class="muted">Пока нет оценённых звонков, поэтому тренд не из чего построить.</p>`;
    return;
  }
  const scores = points.map((point) => Number(point[key]) || 0);
  const xs = (index) => (points.length === 1 ? plotL + plotW / 2 : plotL + (index * plotW) / (points.length - 1));
  const ys = (value) => plotB - (Math.max(0, Math.min(100, value)) / 100) * plotH;
  const yTicks = [0, 25, 50, 75, 100].map((tick) => {
    const y = ys(tick).toFixed(1);
    return `<line x1="${plotL}" y1="${y}" x2="${plotR}" y2="${y}" stroke="#142119" stroke-opacity="0.08"/>
      <text x="${plotL - 8}" y="${Number(y) + 4}" text-anchor="end" font-size="11" fill="#6a766d">${tick}</text>`;
  }).join("");
  const thresholdY = ys(60).toFixed(1);
  const line = points.map((point, index) => `${index ? "L" : "M"} ${xs(index).toFixed(1)} ${ys(scores[index]).toFixed(1)}`).join(" ");
  const area = `${line} L ${xs(points.length - 1).toFixed(1)} ${plotB} L ${xs(0).toFixed(1)} ${plotB} Z`;
  const xLabels = trendLabelIndexes(points.length).map((index) => {
    const x = xs(index).toFixed(1);
    return `<line x1="${x}" y1="${plotB}" x2="${x}" y2="${plotB + 5}" stroke="#6a766d"/>
      <text x="${x}" y="${plotB + 18}" text-anchor="middle" font-size="11" fill="#6a766d">${escapeHtml(trendTickLabel(points[index].date))}</text>`;
  }).join("");
  const dots = points.map((point, index) => {
    const calls = Number(point.count) || 0;
    const callMod = calls % 100;
    const callLast = callMod % 10;
    const callWord = callMod > 10 && callMod < 20 ? "звонков" : callLast === 1 ? "звонок" : callLast >= 2 && callLast <= 4 ? "звонка" : "звонков";
    const title = `${trendFullLabel(point.date)}: ${scores[index].toFixed(1)} балла, ${calls} ${callWord}`;
    return `<circle cx="${xs(index).toFixed(1)}" cy="${ys(scores[index]).toFixed(1)}" r="3.6" fill="${color}"><title>${escapeHtml(title)}</title></circle>`;
  }).join("");
  const low = Math.min(...scores);
  const high = Math.max(...scores);
  const from = trendFullLabel(points[0].date);
  const to = trendFullLabel(points[points.length - 1].date);
  const span = points.length === 1
    ? `${from}: ${scores[0].toFixed(0)} баллов.`
    : `С ${from} по ${to} балл за день от ${low.toFixed(0)} до ${high.toFixed(0)}.`;
  const caption = `${span} Вертикальная ось — оценка от 0 до 100. Горизонтальная ось — дата. Пунктир — порог 60: ниже него звонок попадает в «Нужно внимание».`;
  el.innerHTML = `<figure class="chart-frame">
    <div class="chart-key">
      <span><span class="swatch pos"></span>средний балл за день</span>
      <span><span class="swatch neu"></span>порог внимания 60</span>
    </div>
    <svg viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="${escapeHtml(caption)}" font-family="Segoe UI, sans-serif">
      ${yTicks}
      <line x1="${plotL}" y1="${thresholdY}" x2="${plotR}" y2="${thresholdY}" stroke="#c9a44a" stroke-dasharray="4 4" stroke-width="1.3"/>
      <path d="${area}" fill="${color}" opacity="0.14"/>
      <path d="${line}" fill="none" stroke="${color}" stroke-width="2.2"/>
      ${dots}
      <line x1="${plotL}" y1="${plotT}" x2="${plotL}" y2="${plotB}" stroke="#142119" stroke-opacity="0.55"/>
      <line x1="${plotL}" y1="${plotB}" x2="${plotR}" y2="${plotB}" stroke="#142119" stroke-opacity="0.55"/>
      ${xLabels}
      <text x="15" y="${((plotT + plotB) / 2).toFixed(1)}" text-anchor="middle" font-size="12" fill="#142119" transform="rotate(-90 15 ${((plotT + plotB) / 2).toFixed(1)})">Оценка</text>
      <text x="${((plotL + plotR) / 2).toFixed(1)}" y="${h - 10}" text-anchor="middle" font-size="12" fill="#142119">Дата</text>
    </svg>
    <figcaption class="chart-caption">${escapeHtml(caption)}</figcaption>
  </figure>`;
}

function donut(el, parts) {
  const map = [
    { k: "positive", c: "#2d7a51", cls: "pos", l: "позитивная" },
    { k: "neutral", c: "#c9a44a", cls: "neu", l: "нейтральная" },
    { k: "negative", c: "#9c3b32", cls: "neg", l: "негативная" },
  ];
  if (!map.some((item) => Number(parts[item.k]))) {
    el.innerHTML = `<p class="muted">Нет оценённых звонков, тональность пока не из чего собрать.</p>`;
    return;
  }
  const count = (key) => Number(parts[key]) || 0;
  const total = map.reduce((s, x) => s + count(x.k), 0) || 1;
  let acc = 0;
  const r = 42, c = 2 * Math.PI * r;
  const rings = map.map((x) => {
    const v = count(x.k);
    const dash = (v / total) * c;
    const rot = acc;
    acc += (v / total) * 360;
    return `<circle r="${r}" cx="50" cy="50" fill="none" stroke="${x.c}" stroke-width="11"
      stroke-dasharray="${dash} ${c - dash}" transform="rotate(${rot - 90} 50 50)"/>`;
  }).join("");
  el.innerHTML = `<div class="ring-wrap">
    <svg viewBox="0 0 100 100" width="132" height="132">${rings}</svg>
    <div class="legend">${map.map((x) => `<div><span class="swatch ${x.cls}"></span>${x.l} — ${count(x.k)}</div>`).join("")}</div>
  </div>`;
}

async function renderDashboard(view) {
  const mine = state.user?.role === "operator";
  setNav(
    "dashboard",
    mine ? "Мои разговоры" : "Обзор смены",
    mine ? "Оценки только по вашим звонкам. Админки в этом кабинете нет." : "Четыре метрики качества, тренд и очередь обработки",
    mine ? "Кабинет оператора" : "Дашборд",
  );
  const data = await api(`/dashboard?period=${state.period}`);
  const k = data.kpis;
  const sla = k.processed_sec == null ? "—" : `${(k.processed_sec / 60).toFixed(1)} мин`;
  view.innerHTML = `
    <div class="toolbar">
      <div class="seg" id="period">
        <button data-p="7d" type="button">7 дней</button>
        <button data-p="30d" type="button">30 дней</button>
        <button data-p="all" type="button">всё время</button>
      </div>
    </div>
    <div class="card featured hero">
      <div class="hero-score">
        <div class="value ${scoreClass(k.overall)}">${k.overall.toFixed(0)}</div>
        <div class="label">среднее качество</div>
      </div>
      <div class="gauges">
        ${scoreGauge(data.metrics_avg.politeness, "Вежливость")}
        ${scoreGauge(data.metrics_avg.completeness, "Полнота")}
        ${scoreGauge(data.metrics_avg.script_compliance, "Скрипт")}
        ${scoreGauge(data.metrics_avg.resolution, "Решение")}
      </div>
    </div>
    ${mine || state.user?.role !== "admin" ? "" : `<details class="card mt case-req">
      <summary><h2 class="inline-title">О системе: требования кейса</h2></summary>
      <ul class="case-list">
        <li>Расшифровка Whisper на этой машине${data.offline ? " · модель уже на диске, сеть для звонков не нужна" : " · модель скачается один раз, сами записи наружу не уходят"}</li>
        <li>Оценка: вежливость, полнота ответа, скрипт, решение проблемы и рекомендации оператору</li>
        <li>Форматы WAV, MP3 и OGG. Загрузка, карточка звонка, дашборд и экспорт отчётов</li>
        <li>KPI в CSV и JSON: процент нарушений, время ответа, тональность, дата и время, идентификатор оператора</li>
        <li>Профиль обработки звонка до 10 минут — 3–5 минут. Это расчёт, не замер на этой машине. Сейчас в работе ${data.queue.processing}, ждут ${data.queue.queued}</li>
      </ul>
    </details>`}
    <div class="grid kpis mt">
      <div class="card kpi"><div class="label">Нарушения</div><div class="value ${scoreClass(100 - k.violation_pct)}">${k.violation_pct.toFixed(0)}%</div><div class="hint">доля непройденных проверок</div></div>
      <div class="card kpi"><div class="label">Время ответа</div><div class="value">${k.avg_response_sec == null ? "—" : k.avg_response_sec.toFixed(1)}</div><div class="hint">секунды после реплики клиента</div></div>
      <div class="card kpi"><div class="label">Обработка</div><div class="value">${sla}</div><div class="hint">среднее время одной записи</div></div>
      <div class="card kpi"><div class="label">Оценено</div><div class="value">${k.calls_done}</div><div class="hint">из ${k.calls_total} записей в периоде</div></div>
    </div>
    <div class="grid two mt">
      <div class="card">
        <h2>Тренд качества</h2>
        <p class="chart-lead">Каждая точка — средний балл качества за один день.</p>
        <div id="trend"></div>
      </div>
      <div class="card">
        <h2>Тональность</h2>
        <p class="chart-lead">Сколько разговоров прозвучали позитивно, нейтрально и негативно.</p>
        <div id="donut"></div>
      </div>
    </div>
    <div class="grid two mt">
      <div class="card">
        <h2>Очередь контура</h2>
        <p>В обработке <b>${data.queue.processing}</b> · ждут <b>${data.queue.queued}</b></p>
        <p>Готово сегодня <b>${data.queue.done_today}</b> · ошибки <b>${data.queue.failed}</b></p>
        <p class="muted">Whisper и оценка качества выполняются на этой машине. Аудио на диске зашифровано.</p>
      </div>
      <div class="card">
        <h2>${mine ? "Моя оценка" : "Рейтинг операторов"}</h2>
        ${operatorRank(data.operators)}
      </div>
    </div>
    ${(data.attention || []).length ? `<div class="card mt featured">
      <h2>Нужно внимание</h2>
      <div class="alert-list">${data.attention.map((c) => `
        <div class="alert-row" data-go="#/calls/${c.id}">
          <div><span class="muted">№${c.id}</span> ${escapeHtml(c.orig_filename)}<div class="muted">${escapeHtml(c.operator_name || "без оператора")} · нарушения ${c.violation_pct == null ? "—" : Number(c.violation_pct).toFixed(0) + "%"}</div></div>
          ${pill(c.overall)}
        </div>`).join("")}</div>
    </div>` : ""}
    <div class="card mt">
      <h2>${mine ? "Мои последние звонки" : "Последние звонки"}</h2>
      ${callsMini(data.recent)}
    </div>`;
  $$("#period button").forEach((b) => {
    b.classList.toggle("on", b.dataset.p === state.period);
    b.onclick = () => { state.period = b.dataset.p; renderDashboard(view); };
  });
  areaChart($("#trend"), data.trend, "overall", "#2d7a51");
  donut($("#donut"), data.tonality);
  bindRowLinks();
  if (typeof mountOperatorCompare === "function") await mountOperatorCompare(view);
}

function operatorRank(ops) {
  if (!ops.length) return `<p class="empty">Нет оценённых звонков</p>`;
  return `<div class="op-rank">${ops.map((o, i) => `
    <div class="op-row">
      <span class="op-ava">${escapeHtml(initials(o.name))}</span>
      <div>${escapeHtml(o.name)}<div class="muted">${escapeHtml(o.external_id || "—")} · ${o.calls_count} зв.</div>${metricBars(o)}</div>
      ${pill(o.avg_overall)}
    </div>`).join("")}</div>`;
}

function callsMini(items) {
  if (!items.length) return `<p class="empty">Пока нет звонков</p>`;
  return `<table><thead><tr><th>Когда</th><th>Файл</th><th>Оператор</th><th>Оценка</th></tr></thead><tbody>
    ${items.map((c) => `<tr class="link" data-go="#/calls/${c.id}">
      <td>${fmtDate(c.call_at)}</td><td><span class="muted">№${c.id}</span> ${escapeHtml(c.orig_filename)}</td>
      <td>${escapeHtml(c.operator_name || "—")}${c.operator_external_id ? ` · ${escapeHtml(c.operator_external_id)}` : ""}</td><td>${c.status === "done" ? pill(c.overall) : escapeHtml(statusRu(c.status))}</td>
    </tr>`).join("")}
  </tbody></table>`;
}

async function renderCalls(view) {
  setNav("calls", "Журнал звонков", "Поиск по файлу и тексту, очередь обновляется сама", "Коммуникации");
  const ops = await api("/operators");
  view.innerHTML = `
    <div class="toolbar">
      <label>Статус
        <select id="f-status">
          <option value="">все</option>
          <option value="queued">очередь</option>
          <option value="processing">обработка</option>
          <option value="done">готово</option>
          <option value="error">ошибка</option>
        </select>
      </label>
      ${state.user?.role === "operator" ? "" : `<label>Оператор
        <select id="f-op"><option value="">все</option>${ops.map((o) => `<option value="${o.id}">${escapeHtml(o.name)}</option>`).join("")}</select>
      </label>`}
      <label class="grow">Поиск <input id="f-q" placeholder="файл или фраза из расшифровки" /></label>
      <label class="flex-check"><input id="f-flag" type="checkbox" /> только с пометкой</label>
      <button class="btn" id="f-apply" type="button">Показать</button>
      ${state.user?.role === "admin" ? `<button class="btn ghost" id="retry-failed" type="button">Повторить ошибки</button>` : ""}
    </div>
    <div class="card" id="queue-box" hidden>
      <h2>Очередь обработки</h2>
      <div id="queue-list"></div>
      <p class="muted" id="queue-meta"></p>
    </div>
    <div class="card mt table-scroll" id="table-wrap"></div>
    <div class="pager" id="pager"></div>`;
  const tokenId = {};
  state.callsToken = tokenId;
  const load = async () => {
    if (state.callsToken !== tokenId || !$("#table-wrap")) return;
    try {
    const qs = new URLSearchParams({ page_size: "25", page: String(state.page) });
    if ($("#f-status").value) qs.set("status", $("#f-status").value);
    if ($("#f-op") && $("#f-op").value) qs.set("operator_id", $("#f-op").value);
    if ($("#f-q").value.trim()) qs.set("q", $("#f-q").value.trim());
    if ($("#f-flag").checked) qs.set("flagged", "true");
    const [data, queue] = await Promise.all([api("/calls?" + qs.toString()), api("/queue")]);
    const box = $("#queue-box");
    const busy = queue.queued || (queue.processing || []).length;
    box.hidden = !busy;
    $("#queue-list").innerHTML = (queue.processing || []).map((j) => `<div><a href="#/calls/${j.id}">${escapeHtml(j.filename)}</a>${meter(j.progress, "meter gold")}</div>`).join("");
    $("#queue-meta").textContent = `Ждут: ${queue.queued}. Ошибок: ${queue.failed}. Слотов: ${queue.max_parallel_jobs}.`;
    $("#table-wrap").innerHTML = callsTable(data.items);
    const pages = Math.max(1, Math.ceil((data.total || 0) / data.page_size));
    $("#pager").innerHTML = pages > 1 ? `<button class="btn ghost" type="button" id="page-prev" ${state.page <= 1 ? "disabled" : ""}>Назад</button><span>${state.page} / ${pages}</span><button class="btn ghost" type="button" id="page-next" ${state.page >= pages ? "disabled" : ""}>Дальше</button>` : "";
    if (state.callsToken !== tokenId) return;
    bindRowLinks();
    const prev = $("#page-prev");
    const next = $("#page-next");
    if (prev) prev.onclick = () => { state.page = Math.max(1, state.page - 1); load(); };
    if (next) next.onclick = () => { state.page += 1; load(); };
    setLive(busy ? load : null, 2000);
    } catch (err) {
      if (state.callsToken === tokenId && $("#table-wrap")) {
        $("#table-wrap").innerHTML = `<p class="empty">${escapeHtml(err.message)}</p>`;
      }
    }
  };
  const apply = () => { state.page = 1; load(); };
  $("#f-apply").onclick = apply;
  $("#f-status").onchange = apply;
  if ($("#f-op")) $("#f-op").onchange = apply;
  $("#f-flag").onchange = apply;
  let timer = null;
  $("#f-q").oninput = () => { clearTimeout(timer); timer = setTimeout(apply, 350); };
  const retry = $("#retry-failed");
  if (retry) retry.onclick = async () => {
    try {
      const out = await api("/calls/retry-failed", { method: "POST" });
      toast(out.retried ? `Снова в очереди: ${out.retried}` : "Ошибок с аудио нет");
      load();
    } catch (err) {
      toast(err.message, true);
    }
  };
  load();
}

function callsTable(items) {
  if (!items.length) {
    const hint = state.user?.role === "operator"
      ? "Нет звонков по этому фильтру."
      : "Нет звонков по фильтру. Загрузите запись на вкладке «Загрузить».";
    return `<p class="empty">${hint}</p>`;
  }
  return `<table><thead><tr>
    <th>Дата</th><th>Файл</th><th>Оператор</th><th>Длительность</th><th>Качество</th>
    <th>Вежливость</th><th>Полнота</th><th>Скрипт</th><th>Решение</th><th>Нарушения</th><th>Тональность</th><th>Статус</th>
  </tr></thead><tbody>
    ${items.map((c) => `<tr class="link" data-go="#/calls/${c.id}">
      <td>${fmtDate(c.call_at)}</td>
      <td><span class="muted">№${c.id}</span> ${escapeHtml(c.orig_filename)}${c.is_demo ? ' <span class="tag">демо</span>' : ' <span class="tag">загружен</span>'}${c.flagged ? ' <span class="tag">пометка</span>' : ""}</td>
      <td>${escapeHtml(c.operator_name || "—")}${c.operator_external_id ? `<div class="muted">${escapeHtml(c.operator_external_id)}</div>` : ""}</td>
      <td>${fmtSec(c.duration_sec)}</td>
      <td>${c.overall == null ? "—" : pill(c.overall)}</td>
      <td>${c.politeness == null ? "—" : Number(c.politeness).toFixed(0)}</td>
      <td>${c.completeness == null ? "—" : Number(c.completeness).toFixed(0)}</td>
      <td>${c.script_compliance == null ? "—" : Number(c.script_compliance).toFixed(0)}</td>
      <td>${c.resolution == null ? "—" : Number(c.resolution).toFixed(0)}</td>
      <td>${c.violation_pct == null ? "—" : Number(c.violation_pct).toFixed(0) + "%"}</td>
      <td>${tonalityRu(c.tonality)}</td>
      <td><span class="status-dot ${statusKey(c.status)}"></span>${escapeHtml(statusRu(c.status))}${c.status === "processing" ? ` ${Number(c.progress) || 0}%` : ""}</td>
    </tr>`).join("")}
  </tbody></table>`;
}

function bindRowLinks() {
  $$("[data-go]").forEach((el) => { el.onclick = () => { location.hash = el.dataset.go; }; });
}

async function renderCall(view, id) {
  const reviewer = state.user?.role !== "operator";
  setNav(
    "calls",
    "Досье звонка",
    reviewer ? "Прослушивание, поиск по репликам, чек-лист и заметка рецензента" : "Прослушивание и оценка вашего разговора",
    "Разбор",
  );
  const isAdmin = state.user?.role === "admin";
  if (state.unmaskedCall !== id) state.unmaskedCall = null;
  const unmasked = isAdmin && state.unmaskedCall === id;
  const callUrl = `/calls/${id}${unmasked ? "?unmasked=true" : ""}`;
  const call = await api(callUrl);
  const q = call.quality;
  const live = call.status === "queued" || call.status === "processing";
  view.innerHTML = `
    <div class="toolbar">
      <a class="btn ghost" href="#/calls">← к журналу</a>
      ${reviewer ? `<button class="btn ghost" id="flag" type="button">${call.flagged ? "Снять пометку" : "Пометить"}</button>` : ""}
      <button class="btn ghost" id="copy-text" type="button">Копировать текст</button>
      ${isAdmin ? `<button class="btn ghost" id="toggle-pii" type="button">${unmasked ? "Скрыть персональные данные" : "Показать без маскирования"}</button>` : ""}
      ${reviewer && call.has_audio ? `<button class="btn" id="reprocess" type="button">Повторить обработку</button>` : ""}
      ${state.user?.role === "admin" ? `<button class="btn danger" id="del" type="button">Удалить</button>` : ""}
    </div>
    <div class="dossier">
      <div class="card featured">
        <p class="eyebrow">${escapeHtml(statusRu(call.status))}</p>
        <h2><span class="muted">№${call.id}</span> ${escapeHtml(call.orig_filename)}</h2>
        <p class="muted" id="live-label"></p>
        ${live ? meter(call.progress || 4, "meter gold", 'id="live-progress"') : `<p class="muted">${slaText(call.duration_sec, call.processed_sec)}</p>`}
        <h3 class="player-title">Прослушивание</h3>
        <div id="player-box">${call.has_audio ? "<p class='muted'>Загрузка записи…</p>" : call.is_demo ? "<p class='muted'>У демо-звонка пока нет записи. Озвучка включается настройкой DEMO_VOICE_ENABLED (docs/RUN.md), тогда плеер появится сам.</p>" : "<p class='muted'>У этого звонка ещё нет аудиофайла.</p>"}</div>
        ${q ? `<p class="lead">${escapeHtml(q.summary)}</p>` : call.error_message ? `<p class="form-error">${escapeHtml(call.error_message)}</p>` : `<p class="muted">Оценка появится после обработки.</p>`}
        ${q ? `<h2>Рекомендации оператору</h2><ul class="recs">${(q.recommendations || []).map((r) => `<li>${escapeHtml(r)}</li>`).join("")}</ul>` : ""}
      </div>
      <div class="card">
        ${q ? `
          <div class="gauges gauges-2">
            ${scoreGauge(q.overall, "Итог")}
            ${scoreGauge(100 - q.violation_pct, "Без нарушений")}
          </div>
          <div class="gauges mt">
            ${scoreGauge(q.politeness, "Вежливость")}
            ${scoreGauge(q.completeness, "Полнота")}
            ${scoreGauge(q.script_compliance, "Скрипт")}
            ${scoreGauge(q.resolution, "Решение")}
          </div>
          <p class="muted mt">Тональность: ${tonalityRu(q.tonality)} · ответ: ${fmtSec(q.avg_response_sec)} · речь оператора: ${Math.round((q.talk_ratio_operator || 0) * 100)}%</p>
        ` : `<p class="muted">Метрик ещё нет.</p>`}
      </div>
    </div>
    <div class="grid two mt">
      <div class="card">
        <h2>Расшифровка</h2>
        <label>Найти в репликах <input id="turn-q" placeholder="фраза" /></label>
        <div class="turns" id="turns"></div>
      </div>
      <div class="card">
        <h2>Чек-лист качества</h2>
        <label class="flex-check"><input id="only-fail" type="checkbox" /> только проваленные</label>
        <div class="checks">
          ${q ? (q.checks || []).map((c) => `
            <div class="check ${c.passed ? "pass" : "fail"}">
              <div class="mark">${c.passed ? "✓" : "!"}</div>
              <div>
                <div>${escapeHtml(c.title)}</div>
                <div class="ev">${escapeHtml(c.detail)}${c.evidence ? " — «" + escapeHtml(c.evidence) + "»" : ""}</div>
              </div>
              <b class="${scoreClass(c.score)}">${Number(c.score || 0).toFixed(0)}</b>
            </div>`).join("") : "<p class='muted'>Чек-лист недоступен</p>"}
        </div>
        <h2 class="mt">Заметка рецензента</h2>
        ${reviewer ? `<form id="note-form">
          <textarea class="note-box" id="note" maxlength="2000" placeholder="Что разобрать на планёрке">${escapeHtml(call.review_note || "")}</textarea>
          <button class="btn row mt" type="submit">Сохранить заметку</button>
        </form>` : `<p class="muted">${escapeHtml(call.review_note || "Заметку ставит руководитель.")}</p>`}
      </div>
    </div>`;
  const paintTurns = (query) => {
    const box = $("#turns");
    if (!box) return;
    const needle = (query || "").trim().toLowerCase();
    const rows = (call.segments || []).filter((s) => !needle || String(s.text || "").toLowerCase().includes(needle));
    box.innerHTML = rows.length ? rows.map((s) => `
      <button type="button" class="turn ${s.role === "operator" ? "operator" : "client"}" data-start="${Number(s.start) || 0}" data-end="${Number(s.end) || 0}">
        <div class="meta">${s.role === "operator" ? "Оператор" : "Клиент"} · ${mmss(s.start)}</div>
        <div>${typeof turnText === "function" ? turnText(s, query) : highlight(s.text, query)}</div>
      </button>`).join("") : `<p class="empty">${escapeHtml(call.full_text || "Текст появится после расшифровки")}</p>`;
    $$("#turns .turn").forEach((el) => {
      el.onclick = () => {
        const audio = $("audio.player");
        if (!audio) { toast("У этой записи нет аудио", true); return; }
        audio.currentTime = Number(el.dataset.start) || 0;
        audio.play().catch(() => {});
      };
    });
  };
  const liveLabel = $("#live-label");
  if (liveLabel) liveLabel.textContent = dossierLine(call);
  paintTurns("");
  if (typeof mountTurnLegend === "function") mountTurnLegend(call);
  if (typeof mountOperatorAssign === "function") mountOperatorAssign(call);
  $("#turn-q").oninput = () => paintTurns($("#turn-q").value);
  const onlyFail = $("#only-fail");
  if (onlyFail) onlyFail.onchange = () => {
    $$(".check.pass").forEach((el) => { el.hidden = onlyFail.checked; });
  };
  const bindAudio = () => {
    const audioEl = $("audio.player");
    if (!audioEl) return;
    audioEl.ontimeupdate = () => {
      const t = audioEl.currentTime;
      $$("#turns .turn").forEach((el) => {
        const start = Number(el.dataset.start);
        const end = Number(el.dataset.end);
        el.classList.toggle("is-playing", t >= start && t < end + 0.15);
      });
    };
  };
  const audioToken = {};
  state.audioToken = audioToken;
  const mountPlayer = async () => {
    const box = $("#player-box");
    if (!box || state.audioToken !== audioToken) return false;
    try {
      const res = await api(`/calls/${id}/audio`, { raw: true });
      const blob = await res.blob();
      if (state.audioToken !== audioToken || !$("#player-box")) return false;
      if (state.audioUrl) URL.revokeObjectURL(state.audioUrl);
      state.audioUrl = URL.createObjectURL(blob);
      $("#player-box").innerHTML = `<div class="player-wrap"><audio class="player" controls src="${state.audioUrl}"></audio></div>`;
      bindAudio();
      return true;
    } catch {
      return false;
    }
  };
  if (call.has_audio) {
    const opened = await mountPlayer();
    if (!opened && $("#player-box")) $("#player-box").innerHTML = `<p class="muted">Не удалось открыть запись.</p>`;
  } else if (call.is_demo) {
    let tries = 0;
    const wait = setInterval(async () => {
      tries += 1;
      if (state.audioToken !== audioToken || !$("#player-box") || tries > 90) {
        clearInterval(wait);
        return;
      }
      try {
        const fresh = await api(callUrl);
        if (!fresh.has_audio || state.audioToken !== audioToken) return;
        clearInterval(wait);
        call.segments = fresh.segments || call.segments;
        call.has_audio = true;
        const opened = await mountPlayer();
        if (opened) paintTurns($("#turn-q") ? $("#turn-q").value : "");
      } catch {
        /* следующий тик повторит запрос */
      }
    }, 4000);
  }
  if ($("#toggle-pii")) {
    $("#toggle-pii").onclick = () => {
      state.unmaskedCall = unmasked ? null : id;
      renderCall(view, id);
    };
  }
  $("#copy-text").onclick = async () => {
    const text = call.full_text || (call.segments || []).map((s) => s.text).join("\n");
    try {
      await navigator.clipboard.writeText(text);
      toast("Текст скопирован");
    } catch {
      toast("Не удалось скопировать", true);
    }
  };
  const flag = $("#flag");
  if (flag) flag.onclick = async () => {
    const next = !call.flagged;
    try {
      await api(`/calls/${id}`, { method: "PATCH", json: { flagged: next } });
      call.flagged = next;
      $("#flag").textContent = call.flagged ? "Снять пометку" : "Пометить";
      toast(call.flagged ? "Звонок помечен" : "Пометка снята");
    } catch (err) {
      toast(err.message, true);
    }
  };
  const noteForm = $("#note-form");
  if (noteForm) noteForm.onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api(`/calls/${id}`, { method: "PATCH", json: { review_note: $("#note").value } });
      toast("Заметка сохранена");
    } catch (err) {
      toast(err.message, true);
    }
  };
  const del = $("#del");
  if (del) del.onclick = async () => {
    if (!confirm("Удалить звонок и связанную запись?")) return;
    try {
      await api(`/calls/${id}`, { method: "DELETE" });
      toast("Звонок удалён");
      location.hash = "#/calls";
    } catch (err) {
      toast(err.message, true);
    }
  };
  const rp = $("#reprocess");
  if (rp) rp.onclick = async () => {
    try {
      await api(`/calls/${id}/reprocess`, { method: "POST" });
      toast("Отправлено в очередь");
      route();
    } catch (err) {
      toast(err.message, true);
    }
  };
  if (live) {
    const tokenId = {};
    state.callToken = tokenId;
    setLive(async () => {
      if (state.callToken !== tokenId) return;
      try {
        const next = await api(callUrl);
        if (state.callToken !== tokenId) return;
        if (next.status === "done" || next.status === "error") {
          setLive(null);
          renderCall(view, id);
          return;
        }
        const bar = $("#live-progress");
        if (bar) bar.value = next.progress || 0;
        const label = $("#live-label");
        if (label) label.textContent = dossierLine(next);
      } catch {
        /* следующий тик повторит запрос */
      }
    }, 1500);
  }
}

async function renderUpload(view) {
  setNav("upload", "Загрузить записи", "WAV, MP3, OGG, FLAC и M4A — локальная очередь, без передачи вовне", "Контур");
  const [ops, s] = await Promise.all([api("/operators"), api("/settings")]);
  view.innerHTML = `
    <div class="grid two">
      <div class="card featured">
        <div class="drop" id="drop"><b>Перетащите записи сюда</b>
          <span class="muted">или нажмите, чтобы выбрать · WAV · MP3 · OGG · FLAC · M4A · до ${s.max_upload_mb} МБ</span>
          <input id="files" type="file" accept=".wav,.mp3,.ogg,.oga,.flac,.m4a,audio/*" multiple hidden />
        </div>
        <form id="up" class="stack">
          <label>Оператор
            <select name="operator_id">
              <option value="">не указан</option>
              ${ops.map((o) => `<option value="${o.id}">${escapeHtml(o.name)}</option>`).join("")}
            </select>
          </label>
          <label>Или новый оператор <input name="operator_name" maxlength="120" placeholder="Фамилия И.О." /></label>
          <label>Дата и время звонка <input name="call_at" type="datetime-local" /></label>
          <label class="flex-check"><input type="checkbox" name="operator_first" checked /> Оператор говорит первым</label>
          <div class="file-list" id="file-list"></div>
          <button class="btn primary row" type="submit">Поставить в очередь</button>
        </form>
        <p id="up-status" class="muted"></p>
      </div>
      <div class="card">
        <h2>Как устроен контур</h2>
        <div class="steps">
          <p>Файл проверяется по сигнатуре и шифруется на диске этой машины.</p>
          <p>Очередь берёт следующую запись, когда свободен слот.</p>
          <p>Whisper расшифровывает речь локально. Движок считает вежливость, полноту, скрипт и решение.</p>
          <p>Метрики появляются на дашборде. Во внешние сети ничего не уходит.</p>
        </div>
        <p class="muted mt">Модель ${s.local_offline ? "уже на диске, режим офлайн" : "скачается при первой обработке"} · лимит ${Math.round(s.max_duration_sec / 60)} мин. Тишина отсекается, длинный файл идёт пакетом. Профиль для звонка в 10 минут — 3–5 минут, это расчёт, не замер на этой машине.</p>
      </div>
    </div>`;
  const drop = $("#drop");
  const input = $("#files");
  const allowed = [".wav", ".mp3", ".ogg", ".oga", ".flac", ".m4a"];
  const paintFiles = () => {
    const box = $("#file-list");
    box.innerHTML = state.files.map((f, i) => `<div class="file-row"><span>${escapeHtml(f.name)} · ${(f.size / 1024 / 1024).toFixed(1)} МБ</span><button type="button" class="btn ghost" data-rm="${i}">убрать</button></div>`).join("");
    $$("[data-rm]").forEach((b) => { b.onclick = () => { state.files.splice(Number(b.dataset.rm), 1); paintFiles(); }; });
    drop.querySelector("span").textContent = state.files.length ? `${state.files.length} файл(ов) готовы к загрузке` : `или нажмите, чтобы выбрать · WAV · MP3 · OGG · до ${s.max_upload_mb} МБ`;
  };
  const take = (list) => {
    const next = [];
    for (const file of list) {
      const ext = (file.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
      if (!allowed.includes(ext)) { toast(`${file.name}: нужен WAV, MP3, OGG, FLAC или M4A`, true); continue; }
      if (file.size > s.max_upload_mb * 1024 * 1024) { toast(`${file.name}: больше ${s.max_upload_mb} МБ`, true); continue; }
      next.push(file);
    }
    state.files = state.files.concat(next).slice(0, 20);
    paintFiles();
  };
  drop.onclick = () => input.click();
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => {
    e.preventDefault();
    drop.classList.remove("over");
    take(e.dataTransfer.files);
  };
  input.onchange = () => { take(input.files); input.value = ""; };
  $("#up").onsubmit = async (e) => {
    e.preventDefault();
    if (!state.files.length) { toast("Выберите аудио", true); return; }
    const fd = new FormData();
    for (const f of state.files) fd.append("files", f);
    const form = e.target;
    if (form.operator_id.value) fd.append("operator_id", form.operator_id.value);
    if (form.operator_name.value.trim()) fd.append("operator_name", form.operator_name.value.trim());
    if (form.call_at.value) fd.append("call_at", form.call_at.value);
    fd.append("operator_first", form.operator_first.checked ? "true" : "false");
    $("#up-status").textContent = "Загрузка…";
    try {
      const created = await api("/calls/upload", { method: "POST", body: fd });
      state.files = [];
      const warned = created.filter((c) => c.warning);
      if (warned.length) {
        const text = warned.map((c) => `${c.orig_filename}: ${c.warning}`).join("; ");
        toast(`В очереди: ${created.length}. ${text}. Назначьте оператора в карточке звонка`, true);
      } else {
        toast(`В очереди: ${created.length}`);
      }
      location.hash = "#/calls";
    } catch (err) {
      $("#up-status").textContent = err.message;
      toast(err.message, true);
    }
  };
}

async function renderOperators(view) {
  setNav("operators", "Операторы", "Сравнение качества по сотрудникам", "Команда");
  const ops = await api("/operators");
  const canAdd = state.user?.role === "admin";
  view.innerHTML = `
    <div class="grid two">
      <div class="card featured"><h2>Рейтинг смены</h2>${operatorRank(ops)}</div>
      ${canAdd ? `<div class="card">
        <h2>Добавить оператора</h2>
        <form id="op-form">
          <label>Имя <input name="name" required maxlength="120" /></label>
          <label>Внешний ID <input name="external_id" maxlength="64" placeholder="OP-001" /></label>
          <button class="btn primary row" type="submit">Сохранить</button>
        </form>
      </div>` : `<div class="card"><h2>Карточки сотрудников</h2><p class="muted">Новых операторов добавляет администратор.</p></div>`}
    </div>`;
  if (!canAdd) return;
  $("#op-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      await api("/operators", { method: "POST", json: { name: f.name.value, external_id: f.external_id.value || null } });
      toast("Оператор добавлен");
      renderOperators(view);
    } catch (err) {
      toast(err.message, true);
    }
  };
}

async function renderExport(view) {
  setNav("export", "Экспорт KPI", "Excel, CSV и JSON: нарушения, время ответа, тональность, дата, оператор", "Отчётность");
  const ops = await api("/operators");
  view.innerHTML = `
    <div class="card featured">
      <h2>Выгрузка для предпринимателя</h2>
      <p class="lead">Колонки совпадают с кейсом: идентификатор оператора, дата и время, процент нарушений, среднее время ответа, тональность.</p>
      <form id="ex" class="toolbar">
        <label>Оператор
          <select name="operator_id"><option value="">все</option>${ops.map((o) => `<option value="${o.id}">${escapeHtml(o.name)}</option>`).join("")}</select>
        </label>
        <label>С <input type="date" name="date_from" /></label>
        <label>По <input type="date" name="date_to" /></label>
        <button class="btn gold row" type="submit" title="Числа и даты - настоящие числа, открывается в Excel и LibreOffice при любом языке системы">Скачать Excel (XLSX)</button>
        <button class="btn row" name="csv" type="button" title="Разделитель «;», дроби через запятую - для Excel с русским языком">CSV для русского Excel</button>
        <button class="btn ghost row" name="csvstd" type="button" title="Разделитель «,», дроби через точку - для LibreOffice с английским языком, Python, BI">CSV стандартный</button>
        <button class="btn row" name="json" type="button">Скачать JSON</button>
        ${state.user?.role === "admin" ? `<button class="btn ghost row" name="full" type="button">JSON с расшифровками</button>` : ""}
      </form>
    </div>`;
  const qs = () => {
    const f = $("#ex");
    const p = new URLSearchParams();
    if (f.operator_id.value) p.set("operator_id", f.operator_id.value);
    if (f.date_from.value) p.set("date_from", f.date_from.value + "T00:00:00");
    if (f.date_to.value) p.set("date_to", f.date_to.value + "T23:59:59");
    return p.toString();
  };
  const download = async (path) => {
    try {
      const res = await api(path, { raw: true });
      const blob = await res.blob();
      const a = document.createElement("a");
      const dispo = res.headers.get("content-disposition") || "";
      const m = /filename="?([^"]+)"?/.exec(dispo);
      a.href = URL.createObjectURL(blob);
      a.download = m ? m[1] : "export";
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    } catch (err) {
      toast(err.message, true);
    }
  };
  $("#ex").onsubmit = async (e) => { e.preventDefault(); await download("/export/xlsx?" + qs()); };
  $("#ex [name=csv]").onclick = () => download("/export/csv?" + qs());
  $("#ex [name=csvstd]").onclick = () => download("/export/csv?style=standard&" + qs());
  $("#ex [name=json]").onclick = () => download("/export/json?" + qs());
  const fullExport = $("#ex [name=full]");
  if (fullExport) fullExport.onclick = () => download("/export/json?full=true&" + qs());
  if (typeof mountPdfReport === "function") mountPdfReport(view, ops);
}

function datasetStatusRu(status) {
  return {
    idle: "ожидание",
    ready: "готово",
    downloading: "скачивание",
    evaluating: "проверка Whisper",
    error: "ошибка",
  }[status] || status || "ожидание";
}

async function renderDataset(view) {
  setNav("dataset", "Речевой датасет", "PolyAI MInDS-14 — банковские запросы с эталонной расшифровкой", "Проверка STT");
  const s = await api("/dataset");
  const intents = (s.intents || []).filter(Boolean).join(", ") || "—";
  view.innerHTML = `
    <div class="grid two">
      <div class="card featured">
        <p class="eyebrow">Hugging Face · локальная копия</p>
        <h2>MInDS-14 · русский банк</h2>
        <p class="lead">Клипы и эталонный текст лежат в <code>data/datasets/</code>. После скачивания контур работает офлайн.</p>
        <p>Статус: <b id="ds-status">${escapeHtml(datasetStatusRu(s.status))}</b> · на диске: <b id="ds-n">${s.stored_clips || 0}</b></p>
        <p class="muted" id="ds-msg">${escapeHtml(s.message || "")}</p>
        <p class="muted">Интенты: ${escapeHtml(intents)}</p>
        <div class="toolbar">
          <button class="btn gold row" id="ds-dl" type="button">Скачать 24 клипа</button>
          <button class="btn row" id="ds-eval" type="button" ${s.ready ? "" : "disabled"}>Проверить Whisper</button>
          <button class="btn row" id="ds-imp" type="button" ${s.ready ? "" : "disabled"}>Импорт в очередь</button>
        </div>
        <p class="muted" id="ds-role-note" hidden></p>
        ${meter(s.progress, "meter gold", `id="ds-bar"${s.progress ? "" : " hidden"}`)}
      </div>
      <div class="card">
        <h2>Зачем это</h2>
        <div class="steps">
          <p>Скачивается один раз, дальше контур офлайн.</p>
          <p>WER сравнивает распознавание Whisper с эталоном датасета.</p>
          <p>Импорт ставит клипы в ту же очередь, что и ваши звонки.</p>
        </div>
        <div id="ds-report" class="mt"></div>
      </div>
    </div>`;
  const bar = $("#ds-bar");
  if (s.progress) bar.value = s.progress;
  const poll = async () => {
    let now;
    try {
      now = await api("/dataset");
    } catch {
      return;
    }
    if (!$("#ds-status")) return;
    $("#ds-status").textContent = datasetStatusRu(now.status);
    $("#ds-n").textContent = now.stored_clips || 0;
    $("#ds-msg").textContent = now.message || "";
    if (now.progress) {
      bar.hidden = false;
      bar.value = now.progress;
    }
    const busy = now.status === "downloading" || now.status === "evaluating";
    $("#ds-dl").disabled = busy;
    $("#ds-eval").disabled = busy || !now.ready;
    $("#ds-imp").disabled = busy || !now.ready;
    if (busy) setTimeout(poll, 1500);
  };
  $("#ds-dl").onclick = async () => {
    try {
      await api("/dataset/download", { method: "POST", json: { limit: 24 } });
      toast("Скачивание датасета началось");
      poll();
    } catch (err) {
      toast(err.message, true);
    }
  };
  $("#ds-eval").onclick = async () => {
    $("#ds-msg").textContent = "Считаю WER…";
    try {
      const rep = await api("/dataset/evaluate?limit=12", { method: "POST" });
      $("#ds-report").innerHTML = `<p>WER <b>${(rep.wer * 100).toFixed(1)}%</b> · CER ${(rep.cer * 100).toFixed(1)}% · точность <b>${rep.accuracy}%</b> по ${rep.clips} клипам.</p>`;
      toast("Оценка Whisper готова");
    } catch (e) {
      toast(e.message, true);
    }
    poll();
  };
  $("#ds-imp").onclick = async () => {
    try {
      const out = await api("/dataset/import?limit=12", { method: "POST" });
      toast(`В очередь: ${out.imported}`);
      location.hash = "#/calls";
    } catch (e) {
      toast(e.message, true);
    }
  };
  if (state.user?.role !== "admin") {
    $("#ds-dl").hidden = true;
    $("#ds-imp").hidden = true;
    const note = $("#ds-role-note");
    note.hidden = false;
    note.textContent = "Скачивание и импорт в очередь доступны администратору. Проверку Whisper может запустить и аналитик.";
  }
  if (s.status === "downloading" || s.status === "evaluating") poll();
}

function scriptTicks() {
  try {
    const raw = JSON.parse(sessionStorage.getItem("rk_script_ticks") || "[]");
    return new Set(Array.isArray(raw) ? raw : []);
  } catch {
    return new Set();
  }
}

function phraseLabel(phrase) {
  const text = String(phrase || "");
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
}

function scriptItem(group, phrase, ticks) {
  const id = `${group}:${phrase}`;
  return `<li><label class="script-item"><input type="checkbox" data-tick="${escapeHtml(id)}"${ticks.has(id) ? " checked" : ""}><span>${escapeHtml(phraseLabel(phrase))}</span></label></li>`;
}

function bindScriptTicks() {
  $$(".script-item input").forEach((box) => {
    box.onchange = () => {
      const ticks = scriptTicks();
      if (box.checked) ticks.add(box.dataset.tick);
      else ticks.delete(box.dataset.tick);
      sessionStorage.setItem("rk_script_ticks", JSON.stringify([...ticks]));
    };
  });
}

async function renderOperatorScript(view) {
  setNav("settings", "Скрипт разговора", "Держите фразы перед глазами, пока говорите с клиентом", "Памятка");
  const sheet = await api("/script");
  const ticks = scriptTicks();
  const steps = (sheet.steps || []).map((step) => {
    const examples = step.examples || [];
    const items = examples.length
      ? examples.map((phrase) => scriptItem(step.id, phrase, ticks)).join("")
      : `<li class="muted">Администратор ещё не задал формулировки.</li>`;
    return `<section class="card script-step"><h2>${escapeHtml(step.title)}</h2><p class="muted">${escapeHtml(step.hint)}</p><ul class="script-list">${items}</ul></section>`;
  }).join("");
  const extra = sheet.extra_script_phrases || [];
  const extraItems = extra.length
    ? extra.map((phrase) => scriptItem("extra", phrase, ticks)).join("")
    : `<li class="muted">Дополнительных фраз пока нет. Их добавляет администратор.</li>`;
  view.innerHTML = `
    <p class="page-lead">Это памятка, по ней считается оценка «Скрипт». Отметьте фразу, чтобы не потерять её в разговоре. Отметки остаются только в этом окне браузера и на оценку не влияют.</p>
    <div class="script-sheet">${steps}</div>
    <section class="card script-step mt">
      <h2>Дополнительные фразы</h2>
      <p class="muted">Их нужно произнести так, как задал администратор.</p>
      <ul class="script-list">${extraItems}</ul>
    </section>`;
  bindScriptTicks();
}

async function renderSettings(view) {
  setNav("settings", "Скрипт и контур", "Фразы компании и обязательные реплики скрипта", "Настройки");
  const s = await api("/settings");
  view.innerHTML = `
    <div class="grid two">
      <div class="card">
        <h2>Скрипт</h2>
        <form id="sc">
          <label>Названия компании (по строке)
            <textarea name="company">${escapeHtml((s.company_phrases || []).join("\n"))}</textarea>
          </label>
          <label>Дополнительные обязательные фразы
            <textarea name="extra">${escapeHtml((s.extra_script_phrases || []).join("\n"))}</textarea>
          </label>
          <button class="btn primary row" type="submit">Сохранить скрипт</button>
        </form>
      </div>
      <div class="card featured">
        <h2>Локальный контур</h2>
        <p>Модель Whisper: <code>${escapeHtml(s.whisper_model)}</code></p>
        <p>Устройство: <code>${escapeHtml(s.whisper_device)}</code></p>
        <p>Офлайн-модель на диске: <b>${s.local_offline ? "да" : "нет"}</b></p>
        <p>Параллельных задач: <code>${s.max_parallel_jobs}</code></p>
        <p>Луч: <code>${s.whisper_beam_size}</code> · пакет длинных записей: <code>${s.whisper_batch_size}</code></p>
        <p>Максимум файла: <code>${s.max_upload_mb} МБ</code></p>
        <p>Максимум длительности: <code>${Math.round(s.max_duration_sec / 60)} мин</code></p>
        <p class="muted">Параметры модели задаются в <code>.env</code>. Аудио шифруется ключом в <code>data/master.key</code>.</p>
      </div>
    </div>`;
  $("#sc").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    const split = (t) => t.split(/\n+/).map((x) => x.trim()).filter(Boolean);
    try {
      await api("/settings", { method: "PUT", json: { company_phrases: split(f.company.value), extra_script_phrases: split(f.extra.value) } });
      toast("Скрипт сохранён");
    } catch (err) {
      toast(err.message, true);
    }
  };
  if (state.user?.role !== "admin") {
    $("#sc").querySelectorAll("textarea, button").forEach((el) => { el.disabled = true; });
    $("#sc button").hidden = true;
    $("#sc").insertAdjacentHTML("beforeend", `<p class="muted">Скрипт меняет только администратор.</p>`);
  }
  if (typeof mountChecklistEditor === "function") await mountChecklistEditor(view);
}

const AUDIT_RU = {
  login: "вход",
  login_fail: "неудачный вход",
  password_change: "смена пароля",
  "user.create": "создан пользователь",
  "user.role": "смена роли",
  "user.disable": "учётная запись отключена",
  "user.enable": "учётная запись включена",
  "user.password": "сброс пароля",
  "user.sessions": "сессии закрыты",
  "user.unlock": "снята блокировка пароля",
  "user.operator": "карточка сотрудника",
  "login.unlock": "снята блокировка входа",
  password_fail: "неудачная смена пароля",
  "dataset.download": "скачивание датасета",
  "dataset.import": "импорт датасета",
  logout: "выход",
  "call.upload": "загрузка звонков",
  "call.delete": "удаление звонка",
  "call.reprocess": "повтор обработки",
  "call.retry_failed": "повтор ошибок",
  "call.review": "заметка по звонку",
  "settings.script": "изменение скрипта",
  "export.csv": "экспорт CSV",
  "export.json": "экспорт JSON",
  "call.audio": "доступ к аудиозаписи",
  "retention.purge": "удаление по сроку хранения",
};

function postureItems(posture) {
  const db = posture?.db || {};
  const healthy = db.quick_check === "ok";
  return [
    posture.loopback_only
      ? ["ok", "Контур", "Кабинет слушает только эту машину."]
      : ["bad", "Контур", "Разрешён доступ не только с этой машины. Перед этим нужен TLS-прокси."],
    posture.bootstrap_active
      ? ["bad", "Стартовый пароль", "Администратор ещё не сменил стартовый пароль. Смените его кнопкой «Пароль»."]
      : ["ok", "Стартовый пароль", "Стартовые пароли администраторов уже сменены."],
    ...(db.backend === "postgresql"
      ? [
          ["ok", "База PostgreSQL", `Сервер ${escapeHtml(db.server_version || "")}, отвечает. Защиту базы (пароль, доступ только с 127.0.0.1, резервные копии) настраивает администратор сервера.`],
          db.ssl === "on"
            ? ["ok", "Шифрование соединения", "PostgreSQL принимает TLS."]
            : ["bad", "Шифрование соединения", "TLS на сервере PostgreSQL выключен. Это допустимо только при подключении с этой же машины."],
        ]
      : [
          healthy
            ? ["ok", "Целостность базы", "Проверка файла базы прошла без ошибок."]
            : ["bad", "Целостность базы", "Файл базы не прошёл проверку. Подробности в журнале сервера."],
          db.foreign_keys && db.trusted_schema_off && db.cell_size_check
            ? ["ok", "Защита SQLite", "Чужие базы нельзя подключить, схему нельзя переписать, внешние ключи включены."]
            : ["bad", "Защита SQLite", "Часть ограничений базы не активна. Перезапустите программу."],
          db.secure_delete
            ? ["ok", "Стирание записей", "Удалённые строки затираются на диске, файл базы закрыт для других пользователей."]
            : ["bad", "Стирание записей", "Безопасное стирание не включено. Перезапустите программу."],
        ]),
    ...(posture.pd
      ? [
          posture.pd.key_outside_data
            ? ["ok", "Ключ шифрования", "Ключ хранится отдельно от данных: копия data/ без ключа не читается."]
            : ["bad", "Ключ шифрования", "Ключ лежит в data/ рядом с данными. Задайте MASTER_KEY_FILE, см. docs/152FZ.md."],
          posture.pd.transcripts_encrypted
            ? ["ok", "Шифрование расшифровок", "Текст звонков и цитаты в оценке хранятся в базе зашифрованными."]
            : ["bad", "Шифрование расшифровок", "Текст звонков хранится в базе открытым. Включите ENCRYPT_TRANSCRIPTS=true."],
          posture.pd.retention_days > 0
            ? ["ok", "Срок хранения", `Звонки удаляются автоматически через ${posture.pd.retention_days} дн. после загрузки.`]
            : ["bad", "Срок хранения", "Автоудаление выключено (RETENTION_DAYS=0). Удаляйте звонки вручную по регламенту."],
        ]
      : []),
    posture.login_lock_persisted
      ? ["ok", "Вход", "Неудачные попытки помнятся после перезапуска. Выход гасит сессию до конца её срока."]
      : ["bad", "Вход", "Блокировка входа живёт только до перезапуска программы."],
  ].map(([kind, title, text]) => `<li class="${kind === "ok" ? "" : "bad"}"><div><b>${title}</b><span>${text}</span></div></li>`).join("");
}

function operatorOptions(operators, selected) {
  const head = `<option value="">без карточки</option>`;
  const rest = (operators || []).map((op) => {
    const mark = String(op.id) === String(selected || "") ? "selected" : "";
    const ext = op.external_id ? " · " + escapeHtml(op.external_id) : "";
    return `<option value="${op.id}" ${mark}>${escapeHtml(op.name)}${ext}</option>`;
  }).join("");
  return head + rest;
}

function userRows(users, operators) {
  return users.map((u) => {
    const self = u.id === state.user.id;
    const role = ["admin", "analyst", "operator"].includes(u.role) ? u.role : "analyst";
    const lock = u.password_locked
      ? `<div><span class="badge warn">смена пароля заблокирована</span></div>`
      : u.password_attempts
        ? `<div class="muted">попыток смены: ${u.password_attempts}</div>`
        : "";
    const unlock = !self && (u.password_locked || u.password_attempts)
      ? `<button class="btn ghost row js-unlock" type="button" data-id="${u.id}">Снять блок пароля</button>`
      : "";
    return `<tr>
      <td>
        <b>${escapeHtml(u.username)}</b>
        ${self ? `<div class="muted">это вы</div>` : ""}
        ${u.must_change_password ? `<div><span class="badge warn">ждёт смены пароля</span></div>` : ""}
        ${lock}
      </td>
      <td>
        <select class="role-select js-role" data-id="${u.id}" data-current="${role}" data-operator="${u.operator_id || ""}">
          <option value="operator" ${role === "operator" ? "selected" : ""}>оператор</option>
          <option value="analyst" ${role === "analyst" ? "selected" : ""}>аналитик</option>
          <option value="admin" ${role === "admin" ? "selected" : ""}>администратор</option>
        </select>
      </td>
      <td>
        <select class="role-select js-operator" data-id="${u.id}" data-current="${u.operator_id || ""}" data-role="${role}">
          ${operatorOptions(operators, u.operator_id)}
        </select>
      </td>
      <td>${u.is_active ? `<span class="pill ok">активен</span>` : `<span class="pill bad">отключён</span>`}</td>
      <td>${u.last_login_at ? fmtDate(u.last_login_at) : "ещё не входил"}</td>
      <td>
        <div class="user-actions">
          ${self ? `<button class="btn ghost row js-self-pwd" type="button">Свой пароль</button>` : `<button class="btn ghost row js-toggle" type="button" data-id="${u.id}" data-on="${u.is_active ? "0" : "1"}">${u.is_active ? "Отключить" : "Включить"}</button>`}
          <button class="btn ghost row js-sessions" type="button" data-id="${u.id}" data-self="${self ? "1" : "0"}">${self ? "Закрыть другие сессии" : "Закрыть сессии"}</button>
          ${unlock}
        </div>
      </td>
    </tr>`;
  }).join("");
}

function auditRow(r) {
  const text = `${r.username || ""} ${AUDIT_RU[r.action] || r.action} ${r.detail || ""} ${r.ip || ""}`.toLowerCase();
  return `<tr data-text="${escapeHtml(text)}">
    <td>${escapeHtml(r.created_at_local || fmtDate(r.created_at))}</td>
    <td>${escapeHtml(r.username || "—")}</td>
    <td>${escapeHtml(AUDIT_RU[r.action] || r.action)}</td>
    <td>${escapeHtml(r.detail || "")}</td>
    <td>${escapeHtml(r.ip || "")}</td>
  </tr>`;
}

function keepSession(out) {
  if (!out || !out.token) return;
  sessionStorage.setItem("rk_token", out.token);
  if (out.username) state.user = out;
}

async function renderAdmin(view) {
  if (state.user?.role !== "admin") {
    location.hash = "#/";
    return;
  }
  setNav("admin", "Администрирование", "Люди, сессии, блокировки и защита базы", "Контур");
  let overview;
  let users;
  let audit;
  let posture;
  let operators = [];
  try {
    [overview, users, audit, posture, operators] = await Promise.all([
      api("/admin/overview"),
      api("/admin/users"),
      api("/admin/audit?page_size=40"),
      api("/admin/posture"),
      api("/operators"),
    ]);
  } catch (err) {
    view.innerHTML = `<div class="card featured"><h2>Админка недоступна</h2><p>${escapeHtml(err.message)}</p></div>`;
    return;
  }
  const auditRows = audit.items || [];
  const auditTotal = audit.total || auditRows.length;
  view.innerHTML = `
    <p class="page-lead">Оператор видит только свои звонки. Аналитик смотрит все разговоры, пишет заметки и выгружает KPI. Администратор слушает любую запись, ведёт людей, закрывает чужие сессии и возвращает ошибки в очередь. Свой пароль меняется кнопкой «Сменить пароль» в шапке: причина, если он не сохранился, пишется прямо в окне.</p>
    <div class="grid kpis">
      <div class="card kpi"><div class="label">Пользователи</div><div class="value">${overview.users}</div><div class="hint">администраторов: ${overview.admins}</div></div>
      <div class="card kpi"><div class="label">Звонки</div><div class="value">${overview.calls}</div><div class="hint">в очереди ${overview.queued}</div></div>
      <div class="card kpi">
        <div class="label">Ошибки обработки</div>
        <div class="value">${overview.failed}</div>
        <div class="hint">записей со статусом «ошибка»</div>
        ${overview.failed ? `<button class="btn row mt" id="retry-failed" type="button">Вернуть в очередь</button>` : ""}
      </div>
      <div class="card kpi"><div class="label">Журнал</div><div class="value">${overview.audit}</div><div class="hint">событий аудита</div></div>
    </div>
    <div class="grid two mt">
      <div class="card featured">
        <h2>Защита</h2>
        <ul class="posture">${postureItems(posture)}</ul>
        ${overview.login_locks
          ? `<button class="btn row mt" id="clear-locks" type="button">Снять блокировку входа (${overview.login_locks})</button>`
          : `<p class="muted">Блокировок входа сейчас нет.</p>`}
      </div>
      <div class="card">
        <h2>Новый пользователь</h2>
        <form id="user-form">
          <label>Логин <input name="username" required minlength="3" maxlength="32" autocomplete="off" spellcheck="false" /></label>
          <label>Пароль <input name="password" type="password" required minlength="10" maxlength="128" autocomplete="new-password" /></label>
          <ul class="pw-rules" id="pw-rules">
            <li data-rule="len">от 10 символов</li>
            <li data-rule="letter">есть буква</li>
            <li data-rule="digit">есть цифра</li>
            <li data-rule="symbol">есть символ</li>
          </ul>
          <label>Роль
            <select name="role" id="new-role">
              <option value="operator">оператор — только свои звонки, без админки</option>
              <option value="analyst" selected>аналитик — кабинет без управления людьми</option>
              <option value="admin">администратор — люди, журнал и все звонки</option>
            </select>
          </label>
          <label id="new-operator-label" hidden>Карточка сотрудника
            <select name="operator_id">
              <option value="">выберите оператора</option>
              ${operators.map((op) => `<option value="${op.id}">${escapeHtml(op.name)}${op.external_id ? " · " + escapeHtml(op.external_id) : ""}</option>`).join("")}
            </select>
          </label>
          <button class="btn primary row" type="submit">Создать учётную запись</button>
        </form>
      </div>
    </div>
    <div class="card mt">
      <h2>Учётные записи</h2>
      <p class="muted">Последнего администратора нельзя отключить и нельзя понизить. «Закрыть сессии» выгоняет человека из кабинета, пароль при этом не меняется. Свою блокировку смены пароля снять нельзя: её снимает другой администратор или пауза в 15 минут.</p>
      <div class="table-scroll desk">
        <table>
          <thead><tr><th>Логин</th><th>Роль</th><th>Карточка</th><th>Статус</th><th>Последний вход</th><th></th></tr></thead>
          <tbody>${userRows(users, operators)}</tbody>
        </table>
      </div>
    </div>
    <div class="card mt">
      <h2>Пароль сотрудника</h2>
      <p class="muted">Новый пароль сообщите человеку лично. Его сессии закроются, и при следующем входе кабинет попросит сменить пароль. Если выбрать себя, эта сессия сохранится.</p>
      <form id="reset-form" class="toolbar">
        <label>Пользователь
          <select name="user_id">${users.map((u) => `<option value="${u.id}">${escapeHtml(u.username)}</option>`).join("")}</select>
        </label>
        <label class="grow">Новый пароль <input name="password" type="password" required minlength="10" maxlength="128" autocomplete="new-password" /></label>
        <button class="btn danger row" type="submit">Задать пароль</button>
      </form>
      <ul class="pw-rules" id="reset-rules">
        <li data-rule="len">от 10 символов</li>
        <li data-rule="letter">есть буква</li>
        <li data-rule="digit">есть цифра</li>
        <li data-rule="symbol">есть символ</li>
      </ul>
    </div>
    <div class="card mt">
      <div class="toolbar">
        <h2 class="inline-title">Журнал аудита</h2>
        <label class="grow">Фильтр <input id="audit-q" placeholder="логин, действие, IP" maxlength="80" /></label>
      </div>
      <div class="table-scroll desk">
        <table>
          <thead><tr><th>Когда${audit.tz ? ` <span class="muted">(${escapeHtml(audit.tz)})</span>` : ""}</th><th>Кто</th><th>Действие</th><th>Детали</th><th>IP</th></tr></thead>
          <tbody id="audit-body">
            ${auditRows.length ? auditRows.map(auditRow).join("") : `<tr><td colspan="5">Журнал пока пуст.</td></tr>`}
          </tbody>
        </table>
      </div>
      ${auditRows.length < auditTotal ? `<button class="btn row mt" id="audit-more" type="button">Показать ещё</button>` : ""}
    </div>`;
  const rolePick = $("#new-role");
  const operatorLabel = $("#new-operator-label");
  const syncOperatorField = () => { if (operatorLabel) operatorLabel.hidden = rolePick.value !== "operator"; };
  rolePick.addEventListener("change", syncOperatorField);
  syncOperatorField();
  const pw = $("#user-form [name=password]");
  pw.addEventListener("input", () => paintPasswordRules(pw, $("#pw-rules")));
  const resetPw = $("#reset-form [name=password]");
  resetPw.addEventListener("input", () => paintPasswordRules(resetPw, $("#reset-rules")));
  $("#user-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    const problem = passwordProblem(f.password.value);
    if (problem) { toast(problem, true); return; }
    try {
      const payload = { username: f.username.value, password: f.password.value, role: f.role.value };
      if (f.role.value === "operator") payload.operator_id = Number(f.operator_id.value);
      await api("/admin/users", { method: "POST", json: payload });
      toast("Учётная запись создана");
      renderAdmin(view);
    } catch (err) { toast(err.message, true); }
  };
  $("#reset-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    const problem = passwordProblem(f.password.value);
    if (problem) { toast(problem, true); return; }
    const name = f.user_id.options[f.user_id.selectedIndex].textContent;
    const self = String(f.user_id.value) === String(state.user?.id);
    const ask = self
      ? "Задать новый пароль своей учётке? Эта сессия сохранится, остальные закроются."
      : `Задать новый пароль «${name}» и закрыть его сессии?`;
    if (!window.confirm(ask)) return;
    try {
      const out = await api(`/admin/users/${f.user_id.value}/password`, { method: "POST", json: { password: f.password.value } });
      keepSession(out);
      toast(self ? "Ваш пароль обновлён, эта сессия сохранена" : "Пароль задан, старые сессии закрыты");
      f.password.value = "";
      paintPasswordRules(resetPw, $("#reset-rules"));
      if (!self) renderAdmin(view);
    } catch (err) { toast(err.message, true); }
  };
  const retry = $("#retry-failed");
  if (retry) {
    retry.onclick = async () => {
      if (!window.confirm("Вернуть все ошибочные записи с аудио в очередь?")) return;
      try {
        const out = await api("/calls/retry-failed", { method: "POST" });
        toast(`В очередь возвращено: ${out.retried}`);
        renderAdmin(view);
      } catch (err) { toast(err.message, true); }
    };
  }
  const clearLocks = $("#clear-locks");
  if (clearLocks) {
    clearLocks.onclick = async () => {
      try {
        const out = await api("/admin/locks/login", { method: "POST" });
        toast(`Блокировка входа снята, записей: ${out.cleared}`);
        renderAdmin(view);
      } catch (err) { toast(err.message, true); }
    };
  }
  $$(".js-role").forEach((sel) => {
    sel.onchange = async () => {
      if (sel.value === sel.dataset.current) return;
      const payload = { role: sel.value };
      const card = sel.closest("tr")?.querySelector(".js-operator");
      const operatorId = card && card.value ? card.value : sel.dataset.operator;
      if (sel.value === "operator") {
        if (!operatorId) {
          toast("Сначала выберите карточку сотрудника в соседней колонке.", true);
          sel.value = sel.dataset.current;
          return;
        }
        payload.operator_id = Number(operatorId);
      }
      try {
        await api(`/admin/users/${sel.dataset.id}`, { method: "PATCH", json: payload });
        toast("Роль обновлена. Старая сессия этого пользователя закрыта");
        renderAdmin(view);
      } catch (err) {
        toast(err.message, true);
        sel.value = sel.dataset.current;
      }
    };
  });
  $$(".js-operator").forEach((sel) => {
    sel.onchange = async () => {
      if (sel.value === (sel.dataset.current || "")) return;
      if (!sel.value && sel.dataset.role === "operator") {
        toast("Оператору нужна карточка сотрудника.", true);
        sel.value = sel.dataset.current;
        return;
      }
      const payload = { operator_id: sel.value ? Number(sel.value) : null };
      try {
        await api(`/admin/users/${sel.dataset.id}`, { method: "PATCH", json: payload });
        toast("Карточка сотрудника обновлена");
        renderAdmin(view);
      } catch (err) {
        toast(err.message, true);
        sel.value = sel.dataset.current;
      }
    };
  });
  $$(".js-toggle").forEach((b) => {
    b.onclick = async () => {
      const enable = b.dataset.on === "1";
      if (!enable && !window.confirm("Отключить эту учётную запись?")) return;
      try {
        await api(`/admin/users/${b.dataset.id}`, { method: "PATCH", json: { is_active: enable } });
        renderAdmin(view);
      } catch (err) { toast(err.message, true); }
    };
  });
  $$(".js-self-pwd").forEach((b) => { b.onclick = openAccount; });
  $$(".js-sessions").forEach((b) => {
    b.onclick = async () => {
      const self = b.dataset.self === "1";
      const ask = self ? "Закрыть другие сессии этой учётки? Это окно останется открытым." : "Закрыть все сессии этого пользователя?";
      if (!window.confirm(ask)) return;
      try {
        const out = await api(`/admin/users/${b.dataset.id}/sessions`, { method: "POST" });
        keepSession(out);
        toast(self ? "Другие сессии закрыты" : "Сессии закрыты");
      } catch (err) { toast(err.message, true); }
    };
  });
  $$(".js-unlock").forEach((b) => {
    b.onclick = async () => {
      try {
        await api(`/admin/users/${b.dataset.id}/unlock`, { method: "POST" });
        toast("Блокировка смены пароля снята");
        renderAdmin(view);
      } catch (err) { toast(err.message, true); }
    };
  });
  const auditQ = $("#audit-q");
  const filterAudit = () => {
    if (!auditQ) return;
    const q = auditQ.value.trim().toLowerCase();
    $$("#audit-body tr").forEach((row) => {
      row.hidden = q.length > 0 && !(row.dataset.text || "").includes(q);
    });
  };
  if (auditQ) auditQ.oninput = filterAudit;
  const more = $("#audit-more");
  let auditPage = 1;
  if (more) {
    more.onclick = async () => {
      auditPage += 1;
      more.disabled = true;
      try {
        const next = await api(`/admin/audit?page_size=40&page=${auditPage}`);
        const rows = next.items || [];
        if (rows.length) $("#audit-body").insertAdjacentHTML("beforeend", rows.map(auditRow).join(""));
        filterAudit();
        const shown = $$("#audit-body tr").length;
        if (!rows.length || shown >= (next.total || 0)) more.hidden = true;
      } catch (err) {
        toast(err.message, true);
      } finally {
        more.disabled = false;
      }
    };
  }
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("#login-error");
  const submit = $("#login-submit");
  err.hidden = true;
  const fd = new FormData(e.target);
  submit.disabled = true;
  submit.textContent = "Вхожу…";
  try {
    const out = await api("/auth/login", { method: "POST", json: { username: fd.get("username"), password: fd.get("password") } });
    sessionStorage.setItem("rk_token", out.token);
    state.user = out.user;
    showShell();
    location.hash = "#/";
    route();
  } catch (ex) {
    err.hidden = false;
    err.textContent = ex.message || networkError();
    submit.disabled = false;
    submit.textContent = "Войти в кабинет";
  }
});
$("#logout").addEventListener("click", async () => {
  try { await api("/auth/logout", { method: "POST" }); } catch {}
  sessionStorage.removeItem("rk_token");
  state.user = null;
  state.passwordNudge = false;
  setLive(null);
  showLogin();
});

$("#account").addEventListener("click", openAccount);
$("#pwd-cancel").addEventListener("click", () => $("#account-dialog").close());
$("#account-dialog").addEventListener("cancel", (event) => {
  if (state.user?.must_change_password) event.preventDefault();
});
const pwdNew = $("#pwd-new");
if (pwdNew) pwdNew.addEventListener("input", () => paintPasswordRules(pwdNew, $("#self-pw-rules")));
const pwdReveal = $("#pwd-reveal");
if (pwdReveal) {
  pwdReveal.addEventListener("change", () => {
    const type = pwdReveal.checked ? "text" : "password";
    $$("#pwd-self input").forEach((input) => {
      if (input === pwdReveal) return;
      input.type = type;
    });
  });
}
$("#pwd-self").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const save = $("#pwd-save");
  showPwdError("");
  if (!f.old_password.value) {
    showPwdError("Введите текущий пароль — тот, с которым вы вошли, а не подсказку браузера.");
    return;
  }
  const problem = passwordProblem(f.new_password.value);
  if (problem) { showPwdError(problem); return; }
  if (f.new_password.value !== f.confirm_password.value) {
    showPwdError("Новый пароль и повтор не совпадают.");
    return;
  }
  if (f.new_password.value === f.old_password.value) {
    showPwdError("Новый пароль должен отличаться от старого.");
    return;
  }
  save.disabled = true;
  save.textContent = "Сохраняю…";
  try {
    const out = await api("/auth/password", { method: "POST", json: { old_password: f.old_password.value, new_password: f.new_password.value } });
    sessionStorage.setItem("rk_token", out.token);
    state.user = out.user;
    state.passwordNudge = false;
    showBanner();
    f.reset();
    paintPasswordRules(pwdNew, $("#self-pw-rules"));
    if (pwdReveal) pwdReveal.checked = false;
    $("#account-dialog").close();
    toast("Пароль обновлён. В следующий раз входите уже им. Другие сессии закрыты");
    route();
  } catch (err) {
    showPwdError(err.message || "Пароль не изменился");
  } finally {
    save.disabled = false;
    save.textContent = "Сохранить пароль";
  }
});
$("#nav-toggle").addEventListener("click", () => {
  const open = document.body.classList.toggle("nav-open");
  $("#nav-toggle").setAttribute("aria-expanded", open ? "true" : "false");
});
$("#queue-chip").addEventListener("click", () => { location.hash = "#/calls"; });
document.addEventListener("click", (e) => {
  if (!document.body.classList.contains("nav-open")) return;
  if (e.target.closest && (e.target.closest("#side") || e.target.closest("#nav-toggle"))) return;
  closeNav();
});

window.addEventListener("hashchange", route);
if (token()) {
  if (!location.hash) location.hash = "#/";
  route();
} else {
  showLogin();
}

state.poll = setInterval(() => {
  if (!token() || $("#shell").hidden) return;
  refreshQueueChip();
}, 4000);
