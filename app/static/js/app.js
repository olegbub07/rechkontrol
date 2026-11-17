const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = { user: null, period: "30d", poll: null, live: null, page: 1, files: [], passwordNudge: false, gateAccounts: [] };
const OPERATOR_BLOCKED = ["upload", "operators", "export", "dataset", "settings", "admin"];

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

function fillLogin(account) {
  const form = $("#login-form");
  if (!form || !account) return;
  form.username.value = account.username || "";
  form.password.value = account.password || "";
  form.password.focus();
}

async function refreshGate() {
  const status = $("#login-status");
  const box = $("#cred-box");
  const list = $("#cred-list");
  if (!status || $("#login").hidden) return;
  try {
    const res = await fetch("/api/auth/bootstrap", { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error("down");
    const data = await res.json();
    const accounts = Array.isArray(data.accounts) ? data.accounts : [];
    state.gateAccounts = accounts;
    if (data.show_password && accounts.length && box && list) {
      box.hidden = false;
      list.innerHTML = accounts.map((account, index) => `
        <button type="button" class="cred-account" data-i="${index}">
          <b>${escapeHtml(account.title || account.username)}</b>
          <span>${escapeHtml(account.username)} · ${escapeHtml(account.hint || "")}</span>
        </button>`).join("");
      $$(".cred-account", list).forEach((button) => {
        button.onclick = () => fillLogin(state.gateAccounts[Number(button.dataset.i)]);
      });
    } else if (box) {
      box.hidden = true;
    }
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
  if (dlg && typeof dlg.showModal === "function" && !dlg.open) dlg.showModal();
}

function showBanner() {
  const el = $("#banner");
  if (!el) return;
  if (state.user?.must_change_password) {
    el.hidden = false;
    el.innerHTML = `<span>Стартовый пароль из документации ещё действует. Смените его.</span><button type="button" class="btn gold row" id="banner-pwd">Сменить пароль</button>`;
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
  $$(".nav a").forEach((link) => {
    const name = link.dataset.nav;
    if (role === "operator") link.hidden = name !== "dashboard" && name !== "calls";
    else if (name === "admin") link.hidden = role !== "admin";
    else link.hidden = false;
  });
  const side = $("#side-role");
  if (side) side.textContent = role === "operator" ? "кабинет оператора" : role === "admin" ? "кабинет администратора" : "локальный контур";
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
function statusRu(s) {
  return { queued: "в очереди", processing: "обрабатывается", done: "готово", error: "ошибка" }[s] || s;
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
function metricBars(op) {
  const rows = [
    ["Вежливость", op.avg_politeness],
    ["Полнота", op.avg_completeness],
    ["Скрипт", op.avg_script],
    ["Решение", op.avg_resolution],
  ].filter((pair) => pair[1] != null);
  if (!rows.length) return "";
  return `<div class="bars mt">${rows.map(([label, v]) => `
    <div class="bar-row"><span>${label}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(0, Math.min(100, v))}%"></div></div><b>${Number(v).toFixed(0)}</b></div>
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
  if (state.user?.role === "operator" && OPERATOR_BLOCKED.includes(parts[0])) {
    location.hash = "#/";
    return;
  }
  const view = $("#view");
  view.innerHTML = `<div class="card"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div>`;
  try {
    if (parts[0] === "calls" && parts[1]) await renderCall(view, parts[1]);
    else if (parts[0] === "calls") await renderCalls(view);
    else if (parts[0] === "upload") await renderUpload(view);
    else if (parts[0] === "operators") await renderOperators(view);
    else if (parts[0] === "export") await renderExport(view);
    else if (parts[0] === "dataset") await renderDataset(view);
    else if (parts[0] === "settings") await renderSettings(view);
    else if (parts[0] === "admin") await renderAdmin(view);
    else await renderDashboard(view);
    refreshQueueChip();
  } catch (e) {
    view.innerHTML = `<div class="card empty">${escapeHtml(e.message)}</div>`;
  }
}

async function refreshQueueChip() {
  try {
    const q = await api("/queue");
    const n = q.queued + (q.processing?.length || 0);
    const chip = $("#queue-chip");
    chip.textContent = n ? `очередь ${n}` : "очередь пуста";
    chip.classList.toggle("busy", n > 0);
  } catch {}
}

function areaChart(el, points, key, color) {
  const w = 560, h = 190, p = 28;
  if (!points.length) {
    el.innerHTML = `<p class="muted">Недостаточно точек для тренда</p>`;
    return;
  }
  const vals = points.map((d) => d[key]);
  const max = Math.max(100, ...vals);
  const xs = (i) => p + (i * (w - p * 2)) / Math.max(points.length - 1, 1);
  const ys = (v) => h - p - (v / max) * (h - p * 2);
  const line = points.map((d, i) => `${i ? "L" : "M"} ${xs(i).toFixed(1)} ${ys(d[key]).toFixed(1)}`).join(" ");
  const area = `${line} L ${xs(points.length - 1).toFixed(1)} ${h - p} L ${xs(0).toFixed(1)} ${h - p} Z`;
  const labels = points.filter((_, i) => i === 0 || i === points.length - 1 || i % Math.ceil(points.length / 5) === 0);
  el.innerHTML = `<svg viewBox="0 0 ${w} ${h}" width="100%" height="190" role="img">
    <path d="${area}" fill="${color}" opacity="0.14"/>
    <path d="${line}" fill="none" stroke="${color}" stroke-width="2.2"/>
    ${points.map((d, i) => `<circle cx="${xs(i)}" cy="${ys(d[key])}" r="3" fill="${color}"/>`).join("")}
    ${labels.map((d) => `<text x="${xs(points.indexOf(d))}" y="${h - 8}" font-size="11" fill="#6a766d" text-anchor="middle">${d.date.slice(8)}</text>`).join("")}
  </svg>`;
}

function donut(el, parts) {
  const map = [
    { k: "positive", c: "#2d7a51", cls: "pos", l: "позитивная" },
    { k: "neutral", c: "#c9a44a", cls: "neu", l: "нейтральная" },
    { k: "negative", c: "#9c3b32", cls: "neg", l: "негативная" },
  ];
  const total = map.reduce((s, x) => s + (parts[x.k] || 0), 0) || 1;
  let acc = 0;
  const r = 42, c = 2 * Math.PI * r;
  const rings = map.map((x) => {
    const v = parts[x.k] || 0;
    const dash = (v / total) * c;
    const rot = acc;
    acc += (v / total) * 360;
    return `<circle r="${r}" cx="50" cy="50" fill="none" stroke="${x.c}" stroke-width="11"
      stroke-dasharray="${dash} ${c - dash}" transform="rotate(${rot - 90} 50 50)"/>`;
  }).join("");
  el.innerHTML = `<div class="ring-wrap">
    <svg viewBox="0 0 100 100" width="132" height="132">${rings}</svg>
    <div class="legend">${map.map((x) => `<div><span class="swatch ${x.cls}"></span>${x.l} — ${parts[x.k] || 0}</div>`).join("")}</div>
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
    ${mine ? "" : `<div class="card mt">
      <h2>Требования кейса</h2>
      <ul class="case-list">
        <li>Расшифровка Whisper на этой машине${data.offline ? " · модель уже на диске, сеть для звонков не нужна" : " · модель скачается один раз, сами записи наружу не уходят"}</li>
        <li>Оценка: вежливость, полнота ответа, скрипт, решение проблемы и рекомендации оператору</li>
        <li>Форматы WAV, MP3 и OGG. Загрузка, карточка звонка, дашборд и экспорт отчётов</li>
        <li>KPI в CSV и JSON: процент нарушений, время ответа, тональность, дата и время, идентификатор оператора</li>
        <li>Звонок до 10 минут обрабатывается за 3–5 минут. Сейчас в работе ${data.queue.processing}, ждут ${data.queue.queued}</li>
      </ul>
    </div>`}
    <div class="grid kpis mt">
      <div class="card kpi"><div class="label">Нарушения</div><div class="value ${scoreClass(100 - k.violation_pct)}">${k.violation_pct.toFixed(0)}%</div><div class="hint">доля непройденных проверок</div></div>
      <div class="card kpi"><div class="label">Время ответа</div><div class="value">${k.avg_response_sec == null ? "—" : k.avg_response_sec.toFixed(1)}</div><div class="hint">секунды после реплики клиента</div></div>
      <div class="card kpi"><div class="label">Обработка</div><div class="value">${sla}</div><div class="hint">норматив: 3–5 мин на 10 мин аудио</div></div>
      <div class="card kpi"><div class="label">Оценено</div><div class="value">${k.calls_done}</div><div class="hint">из ${k.calls_total} записей в периоде</div></div>
    </div>
    <div class="grid two mt">
      <div class="card"><h2>Тренд качества</h2><div id="trend"></div></div>
      <div class="card"><h2>Тональность</h2><div id="donut"></div></div>
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
          <div>${escapeHtml(c.orig_filename)}<div class="muted">${escapeHtml(c.operator_name || "без оператора")} · нарушения ${c.violation_pct == null ? "—" : Number(c.violation_pct).toFixed(0) + "%"}</div></div>
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
      <td>${fmtDate(c.call_at)}</td><td>${escapeHtml(c.orig_filename)}</td>
      <td>${escapeHtml(c.operator_name || "—")}${c.operator_external_id ? ` · ${escapeHtml(c.operator_external_id)}` : ""}</td><td>${c.status === "done" ? pill(c.overall) : statusRu(c.status)}</td>
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
      <label>Оператор
        <select id="f-op"><option value="">все</option>${ops.map((o) => `<option value="${o.id}">${escapeHtml(o.name)}</option>`).join("")}</select>
      </label>
      <label>Поиск <input id="f-q" placeholder="файл или фраза из расшифровки" /></label>
      <label class="flex-check"><input id="f-flag" type="checkbox" /> только с пометкой</label>
      <button class="btn" id="f-apply" type="button">Показать</button>
      ${state.user?.role === "admin" ? `<button class="btn ghost" id="retry-failed" type="button">Повторить ошибки</button>` : ""}
    </div>
    <div class="card" id="queue-box" hidden>
      <h2>Очередь обработки</h2>
      <div id="queue-list"></div>
      <p class="muted" id="queue-meta"></p>
    </div>
    <div class="card mt" id="table-wrap"></div>
    <div class="pager" id="pager"></div>`;
  const tokenId = {};
  state.callsToken = tokenId;
  const load = async () => {
    if (state.callsToken !== tokenId || !$("#table-wrap")) return;
    try {
    const qs = new URLSearchParams({ page_size: "25", page: String(state.page) });
    if ($("#f-status").value) qs.set("status", $("#f-status").value);
    if ($("#f-op").value) qs.set("operator_id", $("#f-op").value);
    if ($("#f-q").value.trim()) qs.set("q", $("#f-q").value.trim());
    if ($("#f-flag").checked) qs.set("flagged", "true");
    const [data, queue] = await Promise.all([api("/calls?" + qs.toString()), api("/queue")]);
    const box = $("#queue-box");
    const busy = queue.queued || (queue.processing || []).length;
    box.hidden = !busy;
    $("#queue-list").innerHTML = (queue.processing || []).map((j) => `<div><a href="#/calls/${j.id}">${escapeHtml(j.filename)}</a><div class="progress"><span style="width:${j.progress}%"></span></div></div>`).join("");
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
  $("#f-op").onchange = apply;
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
  if (!items.length) return `<p class="empty">Нет звонков по фильтру. Загрузите запись на вкладке «Загрузить».</p>`;
  return `<table><thead><tr>
    <th>Дата</th><th>Файл</th><th>Оператор</th><th>Длит.</th><th>Качество</th>
    <th>Вежл.</th><th>Полнота</th><th>Скрипт</th><th>Решение</th><th>Нарушения</th><th>Тон</th><th>Статус</th>
  </tr></thead><tbody>
    ${items.map((c) => `<tr class="link" data-go="#/calls/${c.id}">
      <td>${fmtDate(c.call_at)}</td>
      <td>${escapeHtml(c.orig_filename)}${c.is_demo ? ' <span class="tag">демо</span>' : ""}${c.flagged ? ' <span class="tag">пометка</span>' : ""}</td>
      <td>${escapeHtml(c.operator_name || "—")}${c.operator_external_id ? `<div class="muted">${escapeHtml(c.operator_external_id)}</div>` : ""}</td>
      <td>${fmtSec(c.duration_sec)}</td>
      <td>${c.overall == null ? "—" : pill(c.overall)}</td>
      <td>${c.politeness == null ? "—" : Number(c.politeness).toFixed(0)}</td>
      <td>${c.completeness == null ? "—" : Number(c.completeness).toFixed(0)}</td>
      <td>${c.script_compliance == null ? "—" : Number(c.script_compliance).toFixed(0)}</td>
      <td>${c.resolution == null ? "—" : Number(c.resolution).toFixed(0)}</td>
      <td>${c.violation_pct == null ? "—" : Number(c.violation_pct).toFixed(0) + "%"}</td>
      <td>${tonalityRu(c.tonality)}</td>
      <td><span class="status-dot ${c.status}"></span>${statusRu(c.status)}${c.status === "processing" ? ` ${c.progress}%` : ""}</td>
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
  const call = await api(`/calls/${id}`);
  const q = call.quality;
  const live = call.status === "queued" || call.status === "processing";
  view.innerHTML = `
    <div class="toolbar">
      <a class="btn ghost" href="#/calls">← к журналу</a>
      ${reviewer ? `<button class="btn ghost" id="flag" type="button">${call.flagged ? "Снять пометку" : "Пометить"}</button>` : ""}
      <button class="btn ghost" id="copy-text" type="button">Копировать текст</button>
      ${reviewer && call.has_audio ? `<button class="btn" id="reprocess" type="button">Повторить обработку</button>` : ""}
      ${state.user?.role === "admin" ? `<button class="btn danger" id="del" type="button">Удалить</button>` : ""}
    </div>
    <div class="dossier">
      <div class="card featured">
        <p class="eyebrow">${statusRu(call.status)}</p>
        <h2>${escapeHtml(call.orig_filename)}</h2>
        <p class="muted" id="live-label"></p>
        ${live ? `<div class="progress" id="live-bar"><span id="live-progress" style="width:${call.progress || 4}%"></span></div>` : `<p class="muted">${slaText(call.duration_sec, call.processed_sec)}</p>`}
        <h3 class="player-title">Прослушивание</h3>
        <div id="player-box">${call.has_audio ? "<p class='muted'>Загрузка записи…</p>" : call.is_demo ? "<p class='muted'>Запись готовится на этой машине. Плеер появится сам.</p>" : "<p class='muted'>У этого звонка ещё нет аудиофайла.</p>"}</div>
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
      <button type="button" class="turn ${s.role}" data-start="${Number(s.start) || 0}" data-end="${Number(s.end) || 0}">
        <div class="meta">${s.role === "operator" ? "Оператор" : "Клиент"} · ${mmss(s.start)}</div>
        <div>${highlight(s.text, query)}</div>
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
        const fresh = await api(`/calls/${id}`);
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
        const next = await api(`/calls/${id}`);
        if (state.callToken !== tokenId) return;
        if (next.status === "done" || next.status === "error") {
          setLive(null);
          renderCall(view, id);
          return;
        }
        const bar = $("#live-progress");
        if (bar) bar.style.width = `${next.progress || 0}%`;
        const label = $("#live-label");
        if (label) label.textContent = dossierLine(next);
      } catch {
        /* следующий тик повторит запрос */
      }
    }, 1500);
  }
}

async function renderUpload(view) {
  setNav("upload", "Загрузить записи", "WAV, MP3, OGG — локальная очередь, без передачи вовне", "Контур");
  const [ops, s] = await Promise.all([api("/operators"), api("/settings")]);
  view.innerHTML = `
    <div class="grid two">
      <div class="card featured">
        <div class="drop" id="drop"><b>Перетащите записи сюда</b>
          <span class="muted">или нажмите, чтобы выбрать · WAV · MP3 · OGG · до ${s.max_upload_mb} МБ</span>
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
        <p class="muted mt">Модель ${s.local_offline ? "уже на диске, режим офлайн" : "скачается при первой обработке"} · лимит ${Math.round(s.max_duration_sec / 60)} мин. Запись на 10 минут укладывается в норматив 3–5 минут: тишина отсекается, длинный файл идёт пакетом.</p>
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
      toast(`В очереди: ${created.length}`);
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
  view.innerHTML = `
    <div class="grid two">
      <div class="card featured"><h2>Рейтинг смены</h2>${operatorRank(ops)}</div>
      <div class="card">
        <h2>Добавить оператора</h2>
        <form id="op-form">
          <label>Имя <input name="name" required maxlength="120" /></label>
          <label>Внешний ID <input name="external_id" maxlength="64" placeholder="OP-001" /></label>
          <button class="btn primary row" type="submit">Сохранить</button>
        </form>
      </div>
    </div>`;
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
  setNav("export", "Экспорт KPI", "CSV и JSON: нарушения, время ответа, тональность, дата, оператор", "Отчётность");
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
        <button class="btn gold row" type="submit">Скачать CSV</button>
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
  $("#ex").onsubmit = async (e) => { e.preventDefault(); await download("/export/csv?" + qs()); };
  $("#ex [name=json]").onclick = () => download("/export/json?" + qs());
  const fullExport = $("#ex [name=full]");
  if (fullExport) fullExport.onclick = () => download("/export/json?full=true&" + qs());
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
        <p>Статус: <b id="ds-status">${escapeHtml(s.status || "idle")}</b> · на диске: <b id="ds-n">${s.stored_clips || 0}</b></p>
        <p class="muted" id="ds-msg">${escapeHtml(s.message || "")}</p>
        <p class="muted">Интенты: ${escapeHtml(intents)}</p>
        <div class="toolbar">
          <button class="btn gold row" id="ds-dl" type="button">Скачать 24 клипа</button>
          <button class="btn row" id="ds-eval" type="button" ${s.ready ? "" : "disabled"}>Проверить Whisper</button>
          <button class="btn row" id="ds-imp" type="button" ${s.ready ? "" : "disabled"}>Импорт в очередь</button>
        </div>
        <div class="progress" id="ds-bar" ${s.progress ? "" : "hidden"}><span></span></div>
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
  if (s.progress) bar.firstElementChild.style.width = s.progress + "%";
  const poll = async () => {
    let now;
    try {
      now = await api("/dataset");
    } catch {
      return;
    }
    if (!$("#ds-status")) return;
    $("#ds-status").textContent = now.status || "idle";
    $("#ds-n").textContent = now.stored_clips || 0;
    $("#ds-msg").textContent = now.message || "";
    if (now.progress) {
      bar.hidden = false;
      bar.firstElementChild.style.width = now.progress + "%";
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
  }
  if (s.status === "downloading" || s.status === "evaluating") poll();
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
};

function postureItems(posture) {
  const db = posture?.db || {};
  const healthy = db.quick_check === "ok";
  return [
    posture.loopback_only
      ? ["ok", "Контур", "Кабинет слушает только эту машину."]
      : ["bad", "Контур", "Разрешён доступ не только с этой машины. Перед этим нужен TLS-прокси."],
    posture.bootstrap_active
      ? ["bad", "Стартовый пароль", "Пароль из инструкции ещё действует. Смените его кнопкой «Пароль»."]
      : ["ok", "Стартовый пароль", "Пароль из инструкции уже не используется."],
    healthy
      ? ["ok", "Целостность базы", "Проверка файла базы прошла без ошибок."]
      : ["bad", "Целостность базы", "Файл базы не прошёл проверку. Подробности в журнале сервера."],
    db.foreign_keys && db.trusted_schema_off && db.cell_size_check
      ? ["ok", "Защита SQLite", "Чужие базы нельзя подключить, схему нельзя переписать, внешние ключи включены."]
      : ["bad", "Защита SQLite", "Часть ограничений базы не активна. Перезапустите программу."],
    db.secure_delete
      ? ["ok", "Стирание записей", "Удалённые строки затираются на диске, файл базы закрыт для других пользователей."]
      : ["bad", "Стирание записей", "Безопасное стирание не включено. Перезапустите программу."],
    posture.login_lock_persisted
      ? ["ok", "Вход", "Неудачные попытки помнятся после перезапуска. Выход гасит сессию до конца её срока."]
      : ["bad", "Вход", "Блокировка входа живёт только до перезапуска программы."],
  ].map(([kind, title, text]) => `<li class="${kind === "ok" ? "" : "bad"}"><div><b>${title}</b><span>${text}</span></div></li>`).join("");
}

function userRows(users, operators) {
  const names = Object.fromEntries((operators || []).map((op) => [String(op.id), op.name]));
  return users.map((u) => {
    const self = u.id === state.user.id;
    const role = ["admin", "analyst", "operator"].includes(u.role) ? u.role : "analyst";
    const linked = u.operator_id ? names[String(u.operator_id)] : "";
    return `<tr>
      <td>
        <b>${escapeHtml(u.username)}</b>
        ${linked ? `<div class="muted">${escapeHtml(linked)}</div>` : ""}
        ${u.must_change_password ? `<div><span class="badge warn">смените пароль</span></div>` : ""}
      </td>
      <td>
        <select class="role-select js-role" data-id="${u.id}" data-current="${role}" data-operator="${u.operator_id || ""}">
          <option value="operator" ${role === "operator" ? "selected" : ""}>оператор</option>
          <option value="analyst" ${role === "analyst" ? "selected" : ""}>аналитик</option>
          <option value="admin" ${role === "admin" ? "selected" : ""}>администратор</option>
        </select>
      </td>
      <td>${u.is_active ? `<span class="pill ok">активен</span>` : `<span class="pill bad">отключён</span>`}</td>
      <td>${u.last_login_at ? fmtDate(u.last_login_at) : "ещё не входил"}</td>
      <td>
        <div class="user-actions">
          ${self ? `<button class="btn ghost row js-self-pwd" type="button">Свой пароль</button>` : `<button class="btn ghost row js-toggle" type="button" data-id="${u.id}" data-on="${u.is_active ? "0" : "1"}">${u.is_active ? "Отключить" : "Включить"}</button>`}
        </div>
      </td>
    </tr>`;
  }).join("");
}

async function renderAdmin(view) {
  if (state.user?.role !== "admin") {
    location.hash = "#/";
    return;
  }
  setNav("admin", "Администрирование", "Учётные записи, журнал действий и защита базы", "Контур");
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
  view.innerHTML = `
    <div class="grid kpis">
      <div class="card kpi"><div class="label">Пользователи</div><div class="value">${overview.users}</div><div class="hint">администраторов: ${overview.admins}</div></div>
      <div class="card kpi"><div class="label">Звонки</div><div class="value">${overview.calls}</div><div class="hint">в очереди ${overview.queued}</div></div>
      <div class="card kpi"><div class="label">Ошибки обработки</div><div class="value">${overview.failed}</div><div class="hint">записей со статусом «ошибка»</div></div>
      <div class="card kpi"><div class="label">Журнал</div><div class="value">${overview.audit}</div><div class="hint">событий аудита</div></div>
    </div>
    <div class="grid two mt">
      <div class="card featured">
        <h2>Защита</h2>
        <ul class="posture">${postureItems(posture)}</ul>
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
              <option value="admin">администратор — пользователи, журнал и все звонки</option>
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
      <p class="muted">Оператор видит только свои звонки и не открывает админку. Аналитик смотрит все звонки, ставит заметки и выгружает KPI. Администратор слушает любой звонок, удаляет записи и ведёт людей. Последнего администратора нельзя отключить.</p>
      <table>
        <thead><tr><th>Логин</th><th>Роль</th><th>Статус</th><th>Последний вход</th><th></th></tr></thead>
        <tbody>${userRows(users, operators)}</tbody>
      </table>
      <h2 class="mt">Сбросить пароль сотрудника</h2>
      <form id="reset-form" class="toolbar">
        <label>Пользователь
          <select name="user_id">${users.map((u) => `<option value="${u.id}">${escapeHtml(u.username)}</option>`).join("")}</select>
        </label>
        <label>Новый пароль <input name="password" type="password" required minlength="10" maxlength="128" autocomplete="new-password" /></label>
        <button class="btn danger row" type="submit">Сбросить и закрыть сессии</button>
      </form>
    </div>
    <div class="card mt">
      <div class="toolbar">
        <h2 style="margin:0">Журнал аудита</h2>
        <label>Фильтр <input id="audit-q" placeholder="логин, действие, IP" maxlength="80" /></label>
      </div>
      <table>
        <thead><tr><th>Когда</th><th>Кто</th><th>Действие</th><th>Детали</th><th>IP</th></tr></thead>
        <tbody id="audit-body">
          ${auditRows.length ? auditRows.map((r) => `<tr data-text="${escapeHtml(`${r.username || ""} ${AUDIT_RU[r.action] || r.action} ${r.detail || ""} ${r.ip || ""}`.toLowerCase())}">
            <td>${fmtDate(r.created_at)}</td>
            <td>${escapeHtml(r.username || "—")}</td>
            <td>${escapeHtml(AUDIT_RU[r.action] || r.action)}</td>
            <td>${escapeHtml(r.detail || "")}</td>
            <td>${escapeHtml(r.ip || "")}</td>
          </tr>`).join("") : `<tr><td colspan="5">Журнал пока пуст.</td></tr>`}
        </tbody>
      </table>
    </div>`;
  const rolePick = $("#new-role");
  const operatorLabel = $("#new-operator-label");
  const syncOperatorField = () => { if (operatorLabel) operatorLabel.hidden = rolePick.value !== "operator"; };
  rolePick.addEventListener("change", syncOperatorField);
  syncOperatorField();
  const pw = $("#user-form [name=password]");
  pw.addEventListener("input", () => {
    const value = pw.value || "";
    $("#pw-rules [data-rule=len]").classList.toggle("ok", value.length >= 10);
    $("#pw-rules [data-rule=letter]").classList.toggle("ok", /[A-Za-zА-Яа-я]/.test(value));
    $("#pw-rules [data-rule=digit]").classList.toggle("ok", /\d/.test(value));
    $("#pw-rules [data-rule=symbol]").classList.toggle("ok", /[^A-Za-zА-Яа-яЁё0-9]/.test(value));
  });
  $("#user-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
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
    const name = f.user_id.options[f.user_id.selectedIndex].textContent;
    if (!window.confirm(`Сбросить пароль «${name}» и закрыть его сессии?`)) return;
    try {
      await api(`/admin/users/${f.user_id.value}/password`, { method: "POST", json: { password: f.password.value } });
      toast("Пароль сброшен, старые сессии закрыты");
      f.password.value = "";
    } catch (err) { toast(err.message, true); }
  };
  $$(".js-role").forEach((sel) => {
    sel.onchange = async () => {
      if (sel.value === sel.dataset.current) return;
      const payload = { role: sel.value };
      if (sel.value === "operator") {
        if (!sel.dataset.operator) {
          toast("Оператору нужна карточка сотрудника. Создайте такую учётку в форме выше.", true);
          sel.value = sel.dataset.current;
          return;
        }
        payload.operator_id = Number(sel.dataset.operator);
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
  const auditQ = $("#audit-q");
  if (auditQ) {
    auditQ.oninput = () => {
      const q = auditQ.value.trim().toLowerCase();
      $$("#audit-body tr").forEach((row) => {
        row.hidden = q.length > 0 && !(row.dataset.text || "").includes(q);
      });
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
$("#pwd-self").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  try {
    const out = await api("/auth/password", { method: "POST", json: { old_password: f.old_password.value, new_password: f.new_password.value } });
    sessionStorage.setItem("rk_token", out.token);
    state.user = out.user;
    state.passwordNudge = false;
    showBanner();
    f.reset();
    $("#account-dialog").close();
    toast("Пароль обновлён, другие сессии закрыты");
    route();
  } catch (err) {
    toast(err.message, true);
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
