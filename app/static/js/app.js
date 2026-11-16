const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = { user: null, period: "30d", poll: null };

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

async function api(path, opts = {}) {
  const headers = opts.headers ? { ...opts.headers } : {};
  if (token()) headers.Authorization = `Bearer ${token()}`;
  if (opts.json) {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.json);
  }
  const res = await fetch("/api" + path, { ...opts, headers });
  if (res.status === 401) {
    sessionStorage.removeItem("rk_token");
    showLogin();
    throw new Error("Нужна авторизация");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || JSON.stringify(j);
    } catch {}
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
}

function showShell() {
  $("#login").hidden = true;
  $("#shell").hidden = false;
  $("#who-name").textContent = state.user?.username || "admin";
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
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function mmss(sec) {
  const n = Math.max(0, Math.floor(sec || 0));
  return `${String(Math.floor(n / 60)).padStart(2, "0")}:${String(n % 60).padStart(2, "0")}`;
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
    <svg viewBox="0 0 90 90" width="90" height="90">
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
  if (!token()) { showLogin(); return; }
  if (!state.user) {
    try { state.user = await api("/auth/me"); }
    catch { showLogin(); return; }
  }
  showShell();
  const parts = hashParts();
  const view = $("#view");
  view.innerHTML = `<p class="muted">Загрузка…</p>`;
  try {
    if (parts[0] === "calls" && parts[1]) await renderCall(view, parts[1]);
    else if (parts[0] === "calls") await renderCalls(view);
    else if (parts[0] === "upload") await renderUpload(view);
    else if (parts[0] === "operators") await renderOperators(view);
    else if (parts[0] === "export") await renderExport(view);
    else if (parts[0] === "settings") await renderSettings(view);
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
  const w = 560, h = 180, p = 24;
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
  el.innerHTML = `<svg viewBox="0 0 ${w} ${h}" width="100%" height="180" role="img">
    <path d="${area}" fill="${color}" opacity="0.16"/>
    <path d="${line}" fill="none" stroke="${color}" stroke-width="2"/>
    ${points.map((d, i) => `<circle cx="${xs(i)}" cy="${ys(d[key])}" r="2.5" fill="${color}"/>`).join("")}
    ${labels.map((d) => `<text x="${xs(points.indexOf(d))}" y="${h - 6}" font-size="10" fill="#66756c" text-anchor="middle">${d.date.slice(8)}</text>`).join("")}
  </svg>`;
}

function donut(el, parts) {
  const map = [
    { k: "positive", c: "#2f7a52", l: "позитивная" },
    { k: "neutral", c: "#d4b56a", l: "нейтральная" },
    { k: "negative", c: "#9c3a32", l: "негативная" },
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
    <svg viewBox="0 0 100 100" width="128" height="128">${rings}</svg>
    <div class="legend">${map.map((x) => `<div><span class="swatch" style="background:${x.c}"></span>${x.l} — ${parts[x.k] || 0}</div>`).join("")}</div>
  </div>`;
}

function metricBars(el, metrics) {
  const labels = { politeness: "Вежливость", completeness: "Полнота", script_compliance: "Скрипт", resolution: "Решение" };
  el.innerHTML = `<div class="bars">${Object.entries(labels).map(([k, l]) => {
    const v = metrics[k] || 0;
    return `<div class="bar-row"><span>${l}</span><div class="bar-track"><div class="bar-fill" style="width:${v}%"></div></div><b class="${scoreClass(v)}">${v.toFixed(0)}</b></div>`;
  }).join("")}</div>`;
}

async function renderDashboard(view) {
  setNav("dashboard", "Обзор смены", "Четыре метрики качества, тренд и очередь обработки", "Дашборд");
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
    <div class="grid kpis">
      <div class="card kpi featured"><div class="label">Среднее качество</div><div class="value ${scoreClass(k.overall)}">${k.overall.toFixed(0)}</div><div class="hint">${k.calls_done} оценённых звонков</div></div>
      <div class="card kpi"><div class="label">Процент нарушений</div><div class="value ${scoreClass(100 - k.violation_pct)}">${k.violation_pct.toFixed(0)}%</div><div class="hint">доля непройденных проверок</div></div>
      <div class="card kpi"><div class="label">Время ответа</div><div class="value">${k.avg_response_sec == null ? "—" : k.avg_response_sec.toFixed(1)}</div><div class="hint">секунды после реплики клиента</div></div>
      <div class="card kpi"><div class="label">Обработка записи</div><div class="value">${sla}</div><div class="hint">норматив кейса: 3–5 мин на 10 мин аудио</div></div>
    </div>
    <div class="card" style="margin-top:16px">
      <h2>Вежливость · полнота · скрипт · решение</h2>
      <div class="gauges">
        ${scoreGauge(data.metrics_avg.politeness, "Вежливость")}
        ${scoreGauge(data.metrics_avg.completeness, "Полнота")}
        ${scoreGauge(data.metrics_avg.script_compliance, "Скрипт")}
        ${scoreGauge(data.metrics_avg.resolution, "Решение")}
      </div>
    </div>
    <div class="grid two" style="margin-top:16px">
      <div class="card"><h2>Тренд качества</h2><div id="trend"></div></div>
      <div class="card"><h2>Тональность</h2><div id="donut"></div></div>
    </div>
    <div class="grid two" style="margin-top:16px">
      <div class="card">
        <h2>Очередь</h2>
        <p>В обработке <b>${data.queue.processing}</b> · ждут <b>${data.queue.queued}</b></p>
        <p>Готово сегодня <b>${data.queue.done_today}</b> · ошибки <b>${data.queue.failed}</b></p>
        <p class="muted">Whisper и оценка качества выполняются на этой машине. Аудио на диске зашифровано.</p>
      </div>
      <div class="card">
        <h2>Рейтинг операторов</h2>
        ${operatorRank(data.operators)}
      </div>
    </div>
    <div class="card" style="margin-top:16px">
      <h2>Последние звонки</h2>
      ${callsMini(data.recent)}
    </div>`;
  $$("#period button").forEach((b) => {
    b.classList.toggle("on", b.dataset.p === state.period);
    b.onclick = () => { state.period = b.dataset.p; renderDashboard(view); };
  });
  areaChart($("#trend"), data.trend, "overall", "#2f7a52");
  donut($("#donut"), data.tonality);
  bindRowLinks();
}

function operatorRank(ops) {
  if (!ops.length) return `<p class="empty">Нет оценённых звонков</p>`;
  return `<div class="op-rank">${ops.map((o, i) => `
    <div class="op-row">
      <b>${String(i + 1).padStart(2, "0")}</b>
      <div>${escapeHtml(o.name)}<div class="muted">${escapeHtml(o.external_id || "—")} · ${o.calls_count} зв.</div></div>
      ${pill(o.avg_overall)}
    </div>`).join("")}</div>`;
}

function operatorTable(ops) {
  if (!ops.length) return `<p class="empty">Нет оценённых звонков</p>`;
  return `<table><thead><tr><th>Оператор</th><th>ID</th><th>Звонков</th><th>Качество</th><th>Нарушения</th></tr></thead><tbody>
    ${ops.map((o) => `<tr class="link" data-go="#/operators">
      <td>${escapeHtml(o.name)}</td><td>${escapeHtml(o.external_id || "—")}</td>
      <td>${o.calls_count}</td><td>${pill(o.avg_overall)}</td><td>${o.violation_pct == null ? "—" : o.violation_pct.toFixed(0) + "%"}</td>
    </tr>`).join("")}
  </tbody></table>`;
}

function callsMini(items) {
  if (!items.length) return `<p class="empty">Пока нет звонков</p>`;
  return `<table><thead><tr><th>Когда</th><th>Файл</th><th>Оператор</th><th>Оценка</th></tr></thead><tbody>
    ${items.map((c) => `<tr class="link" data-go="#/calls/${c.id}">
      <td>${fmtDate(c.call_at)}</td><td>${escapeHtml(c.orig_filename)}</td>
      <td>${escapeHtml(c.operator_name || "—")}</td><td>${c.status === "done" ? pill(c.overall) : statusRu(c.status)}</td>
    </tr>`).join("")}
  </tbody></table>`;
}

async function renderCalls(view) {
  setNav("calls", "Журнал звонков", "Очередь, расшифровки и оценки качества", "Коммуникации");
  const [ops, data, queue] = await Promise.all([api("/operators"), api("/calls?page_size=50"), api("/queue")]);
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
      <label>Файл <input id="f-q" placeholder="имя файла" /></label>
      <button class="btn" id="f-apply" type="button">Показать</button>
    </div>
    <div class="card" id="queue-box" ${queue.queued || queue.processing.length ? "" : "hidden"}>
      <h2>Очередь обработки</h2>
      ${(queue.processing || []).map((j) => `<div>${escapeHtml(j.filename)}<div class="progress"><span style="width:${j.progress}%"></span></div></div>`).join("")}
      <p class="muted">Ждут: ${queue.queued}. Параллельных слотов: ${queue.max_parallel_jobs}.</p>
    </div>
    <div class="card" style="margin-top:16px" id="table-wrap">${callsTable(data.items)}</div>`;
  $("#f-apply").onclick = async () => {
    const qs = new URLSearchParams({ page_size: "50" });
    if ($("#f-status").value) qs.set("status", $("#f-status").value);
    if ($("#f-op").value) qs.set("operator_id", $("#f-op").value);
    if ($("#f-q").value.trim()) qs.set("q", $("#f-q").value.trim());
    const next = await api("/calls?" + qs.toString());
    $("#table-wrap").innerHTML = callsTable(next.items);
    bindRowLinks();
  };
  bindRowLinks();
}

function callsTable(items) {
  if (!items.length) return `<p class="empty">Нет звонков по фильтру. Загрузите запись на вкладке «Загрузить».</p>`;
  return `<table><thead><tr>
    <th>Дата</th><th>Файл</th><th>Оператор</th><th>Длит.</th><th>Качество</th>
    <th>Вежл.</th><th>Полнота</th><th>Скрипт</th><th>Решение</th><th>Нарушения</th><th>Тон</th><th>Статус</th>
  </tr></thead><tbody>
    ${items.map((c) => `<tr class="link" data-go="#/calls/${c.id}">
      <td>${fmtDate(c.call_at)}</td>
      <td>${escapeHtml(c.orig_filename)}${c.is_demo ? ' <span class="tag">демо</span>' : ""}</td>
      <td>${escapeHtml(c.operator_name || "—")}</td>
      <td>${fmtSec(c.duration_sec)}</td>
      <td>${c.overall == null ? "—" : pill(c.overall)}</td>
      <td>${c.politeness == null ? "—" : Number(c.politeness).toFixed(0)}</td>
      <td>${c.completeness == null ? "—" : Number(c.completeness).toFixed(0)}</td>
      <td>${c.script_compliance == null ? "—" : Number(c.script_compliance).toFixed(0)}</td>
      <td>${c.resolution == null ? "—" : Number(c.resolution).toFixed(0)}</td>
      <td>${c.violation_pct == null ? "—" : Number(c.violation_pct).toFixed(0) + "%"}</td>
      <td>${tonalityRu(c.tonality)}</td>
      <td>${statusRu(c.status)}${c.status === "processing" ? ` ${c.progress}%` : ""}</td>
    </tr>`).join("")}
  </tbody></table>`;
}

function bindRowLinks() {
  $$("[data-go]").forEach((el) => { el.onclick = () => { location.hash = el.dataset.go; }; });
}

async function renderCall(view, id) {
  setNav("calls", "Досье звонка", "Расшифровка, четыре метрики и рекомендации оператору", "Разбор");
  const call = await api(`/calls/${id}`);
  const q = call.quality;
  view.innerHTML = `
    <div class="toolbar">
      <a class="btn ghost" href="#/calls">← к журналу</a>
      ${call.has_audio ? `<button class="btn" id="reprocess" type="button">Повторить обработку</button>` : ""}
      <button class="btn danger" id="del" type="button">Удалить</button>
    </div>
    <div class="dossier">
      <div class="card featured">
        <p class="eyebrow">${statusRu(call.status)}</p>
        <h2>${escapeHtml(call.orig_filename)}</h2>
        <p class="muted">${fmtDate(call.call_at)} · ${escapeHtml(call.operator_name || "без оператора")} · ${fmtSec(call.duration_sec)}${call.processed_sec ? " · обработка " + fmtSec(call.processed_sec) : ""}</p>
        <div id="player-box">${call.has_audio ? "Загрузка записи…" : "<p class='muted'>Демо-звонок без аудиофайла. Реальные записи появляются после загрузки WAV, MP3 или OGG.</p>"}</div>
        ${q ? `<p class="lead">${escapeHtml(q.summary)}</p>` : call.error_message ? `<p class="form-error">${escapeHtml(call.error_message)}</p>` : `<p class="muted">Оценка появится после обработки.</p>`}
        ${q ? `<h2>Рекомендации оператору</h2><ul class="recs">${q.recommendations.map((r) => `<li>${escapeHtml(r)}</li>`).join("")}</ul>` : ""}
      </div>
      <div class="card">
        ${q ? `
          <div class="gauges" style="grid-template-columns:1fr 1fr">
            ${scoreGauge(q.overall, "Итог")}
            ${scoreGauge(100 - q.violation_pct, "Без нарушений")}
          </div>
          <div class="gauges" style="margin-top:12px">
            ${scoreGauge(q.politeness, "Вежливость")}
            ${scoreGauge(q.completeness, "Полнота")}
            ${scoreGauge(q.script_compliance, "Скрипт")}
            ${scoreGauge(q.resolution, "Решение")}
          </div>
          <p class="muted" style="margin-top:14px">Тональность: ${tonalityRu(q.tonality)} · ответ: ${fmtSec(q.avg_response_sec)} · речь оператора: ${Math.round((q.talk_ratio_operator || 0) * 100)}%</p>
        ` : `<p class="muted">Метрик ещё нет.</p>`}
      </div>
    </div>
    <div class="grid two" style="margin-top:16px">
      <div class="card">
        <h2>Расшифровка</h2>
        <div class="turns">
          ${call.segments.length ? call.segments.map((s) => `
            <div class="turn ${s.role}">
              <div class="meta">${s.role === "operator" ? "Оператор" : "Клиент"} · ${mmss(s.start)}</div>
              <div>${escapeHtml(s.text)}</div>
            </div>`).join("") : `<p class="empty">${escapeHtml(call.full_text || "Текст появится после расшифровки")}</p>`}
        </div>
      </div>
      <div class="card">
        <h2>Чек-лист качества</h2>
        <div class="checks">
          ${q ? q.checks.map((c) => `
            <div class="check ${c.passed ? "pass" : "fail"}">
              <div class="mark">${c.passed ? "✓" : "!"}</div>
              <div>
                <div>${escapeHtml(c.title)}</div>
                <div class="ev">${escapeHtml(c.detail)}${c.evidence ? " — «" + escapeHtml(c.evidence) + "»" : ""}</div>
              </div>
              <b class="${scoreClass(c.score)}">${c.score.toFixed(0)}</b>
            </div>`).join("") : "<p class='muted'>Чек-лист недоступен</p>"}
        </div>
      </div>
    </div>`;
  if (call.has_audio) {
    try {
      const res = await api(`/calls/${id}/audio`, { raw: true });
      const blob = await res.blob();
      $("#player-box").innerHTML = `<audio class="player" controls src="${URL.createObjectURL(blob)}"></audio>`;
    } catch {
      $("#player-box").innerHTML = `<p class="muted">Не удалось открыть запись.</p>`;
    }
  }
  $("#del").onclick = async () => {
    if (!confirm("Удалить звонок и связанную запись?")) return;
    await api(`/calls/${id}`, { method: "DELETE" });
    toast("Звонок удалён");
    location.hash = "#/calls";
  };
  const rp = $("#reprocess");
  if (rp) rp.onclick = async () => {
    await api(`/calls/${id}/reprocess`, { method: "POST" });
    toast("Отправлено в очередь");
    route();
  };
}

async function renderUpload(view) {
  setNav("upload", "Загрузить записи", "WAV, MP3, OGG — локальная очередь, без передачи вовне", "Контур");
  const [ops, s] = await Promise.all([api("/operators"), api("/settings")]);
  view.innerHTML = `
    <div class="grid two">
      <div class="card featured">
        <div class="drop" id="drop">Перетащите записи сюда или <b>выберите файлы</b><br/>
          <span class="muted">WAV · MP3 · OGG · до ${s.max_upload_mb} МБ · 10 минут аудио — норматив 3–5 минут обработки</span>
          <input id="files" type="file" accept=".wav,.mp3,.ogg,.oga,.flac,.m4a,audio/*" multiple hidden />
        </div>
        <form id="up" class="stack" style="margin-top:18px">
          <label>Оператор
            <select name="operator_id">
              <option value="">не указан</option>
              ${ops.map((o) => `<option value="${o.id}">${escapeHtml(o.name)}</option>`).join("")}
            </select>
          </label>
          <label>Или новый оператор <input name="operator_name" maxlength="120" placeholder="Фамилия И.О." /></label>
          <label>Дата и время звонка <input name="call_at" type="datetime-local" /></label>
          <label style="display:flex;gap:8px;align-items:center"><input type="checkbox" name="operator_first" checked /> Оператор говорит первым</label>
          <button class="btn primary row" type="submit">Поставить в очередь</button>
        </form>
        <p id="up-status" class="muted"></p>
      </div>
      <div class="card">
        <h2>Как устроен контур</h2>
        <p>1. Файл проверяется по сигнатуре и шифруется на диске этой машины.</p>
        <p>2. Очередь берёт следующую запись, когда свободен слот.</p>
        <p>3. Whisper расшифровывает речь локально. Движок считает вежливость, полноту, скрипт и решение.</p>
        <p>4. На дашборде появляются метрики и тренд. Во внешние сети ничего не уходит.</p>
        <p class="muted">Модель ${s.local_offline ? "уже на диске, режим офлайн" : "скачается при первой обработке"} · лимит длительности ${Math.round(s.max_duration_sec / 60)} мин.</p>
      </div>
    </div>`;
  const drop = $("#drop");
  const input = $("#files");
  drop.onclick = () => input.click();
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => {
    e.preventDefault();
    drop.classList.remove("over");
    input.files = e.dataTransfer.files;
    drop.querySelector("span").textContent = `${input.files.length} файл(ов) готовы к загрузке`;
  };
  input.onchange = () => { drop.querySelector("span").textContent = `${input.files.length} файл(ов) готовы к загрузке`; };
  $("#up").onsubmit = async (e) => {
    e.preventDefault();
    if (!input.files.length) { toast("Выберите аудио", true); return; }
    const fd = new FormData();
    for (const f of input.files) fd.append("files", f);
    const form = e.target;
    if (form.operator_id.value) fd.append("operator_id", form.operator_id.value);
    if (form.operator_name.value.trim()) fd.append("operator_name", form.operator_name.value.trim());
    if (form.call_at.value) fd.append("call_at", form.call_at.value);
    fd.append("operator_first", form.operator_first.checked ? "true" : "false");
    $("#up-status").textContent = "Загрузка…";
    try {
      const created = await api("/calls/upload", { method: "POST", body: fd });
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
      <div class="card"><h2>Рейтинг</h2>${operatorRank(ops)}</div>
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
    await api("/operators", { method: "POST", json: { name: f.name.value, external_id: f.external_id.value || null } });
    toast("Оператор добавлен");
    renderOperators(view);
  };
}

async function renderExport(view) {
  setNav("export", "Экспорт KPI", "CSV и JSON: нарушения, время ответа, тональность, дата, оператор", "Отчётность");
  const ops = await api("/operators");
  view.innerHTML = `
    <div class="card featured">
      <h2>Выгрузка для предпринимателя</h2>
      <form id="ex" class="toolbar">
        <label>Оператор
          <select name="operator_id"><option value="">все</option>${ops.map((o) => `<option value="${o.id}">${escapeHtml(o.name)}</option>`).join("")}</select>
        </label>
        <label>С <input type="date" name="date_from" /></label>
        <label>По <input type="date" name="date_to" /></label>
        <button class="btn gold row" type="submit">Скачать CSV</button>
        <button class="btn row" name="json" type="button">Скачать JSON</button>
        <button class="btn ghost row" name="full" type="button">JSON с расшифровками</button>
      </form>
      <p class="muted">В CSV: идентификатор оператора, дата и время, процент нарушений, среднее время ответа, тональность, четыре метрики качества.</p>
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
    const res = await api(path, { raw: true });
    const blob = await res.blob();
    const a = document.createElement("a");
    const dispo = res.headers.get("content-disposition") || "";
    const m = /filename="?([^"]+)"?/.exec(dispo);
    a.href = URL.createObjectURL(blob);
    a.download = m ? m[1] : "export";
    a.click();
  };
  $("#ex").onsubmit = async (e) => { e.preventDefault(); await download("/export/csv?" + qs()); };
  $("#ex [name=json]").onclick = () => download("/export/json?" + qs());
  $("#ex [name=full]").onclick = () => download("/export/json?full=true&" + qs());
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
        <p>Максимум файла: <code>${s.max_upload_mb} МБ</code></p>
        <p>Максимум длительности: <code>${Math.round(s.max_duration_sec / 60)} мин</code></p>
        <p class="muted">Параметры модели задаются в <code>.env</code>. Аудио шифруется ключом в <code>data/master.key</code>.</p>
      </div>
    </div>`;
  $("#sc").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    const split = (t) => t.split(/\n+/).map((x) => x.trim()).filter(Boolean);
    await api("/settings", { method: "PUT", json: { company_phrases: split(f.company.value), extra_script_phrases: split(f.extra.value) } });
    toast("Скрипт сохранён");
  };
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("#login-error");
  err.hidden = true;
  const fd = new FormData(e.target);
  try {
    const out = await api("/auth/login", { method: "POST", json: { username: fd.get("username"), password: fd.get("password") } });
    sessionStorage.setItem("rk_token", out.token);
    state.user = out.user;
    showShell();
    location.hash = "#/";
    route();
  } catch (ex) {
    err.hidden = false;
    err.textContent = ex.message;
  }
});

$("#logout").addEventListener("click", () => {
  sessionStorage.removeItem("rk_token");
  state.user = null;
  showLogin();
});

window.addEventListener("hashchange", route);
if (token()) route();
else showLogin();

state.poll = setInterval(() => {
  if (!token() || $("#shell").hidden) return;
  refreshQueueChip();
}, 4000);
