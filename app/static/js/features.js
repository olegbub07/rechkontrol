/* Дополнительные разделы кабинета: настраиваемый чек-лист, подсветка расшифровки,
   сравнение операторов и PDF-отчёт. Подключается до app.js, вызывается из его страниц. */

const CHECKLIST_HINTS = {
  company: "фразы — в блоке «Скрипт» выше (названия компании)",
  extra_script: "фразы — в блоке «Скрипт» выше (дополнительные фразы); без них критерий не считается",
  no_tutoyage: "считается по обращению на «ты»",
  interrupt: "считается по паузам между репликами",
  answers: "доля вопросов клиента, на которые оператор ответил",
  substance: "средняя длина реплики оператора",
  response_time: "средняя пауза перед ответом оператора",
};

const FEATURE_AUDIT_RU = {
  "checklist.update": "изменение чек-листа",
  "checklist.reset": "чек-лист сброшен",
  "checklist.rescore": "пересчёт оценок",
  "call.unmask": "просмотр без маскирования",
  "report.operator": "PDF-отчёт по оператору",
  "operator.create": "добавлен оператор",
  "export.xlsx": "экспорт Excel",
  "call.operator": "назначен оператор звонка",
};

function registerFeatureAudit() {
  if (typeof AUDIT_RU === "object") Object.assign(AUDIT_RU, FEATURE_AUDIT_RU);
}

function clNumber(raw) {
  const text = String(raw ?? "").trim().replace(",", ".");
  if (!text || !/^-?\d+(\.\d+)?$/.test(text)) return null;
  return Number(text);
}

async function mountChecklistEditor(view) {
  registerFeatureAudit();
  const isAdmin = state.user?.role === "admin";
  const data = await api("/checklist");
  const card = document.createElement("div");
  card.className = "card mt cl-card";
  view.appendChild(card);
  const paint = (cl) => {
    const groups = Object.entries(data.categories).map(([cat, label]) => {
      const rows = cl.criteria.filter((c) => c.category === cat).map((c) => {
        const negative = data.negative_criteria.includes(c.id);
        const phraseCell = Array.isArray(c.phrases)
          ? `<textarea class="cl-phrases" data-f="phrases" rows="3" title="По фразе на строку">${escapeHtml(c.phrases.join("\n"))}</textarea>
             <div class="hint cl-hint">${negative ? "найденная фраза — нарушение" : "найденная фраза — критерий пройден"} · ${c.phrases.length} фраз</div>`
          : `<span class="muted">${escapeHtml(CHECKLIST_HINTS[c.id] || "считается автоматически")}</span>`;
        return `<tr data-id="${c.id}" class="${c.enabled ? "" : "cl-off"}">
          <td><input type="checkbox" data-f="enabled" ${c.enabled ? "checked" : ""} aria-label="Включить" /></td>
          <td><input data-f="title" value="${escapeHtml(c.title)}" maxlength="80" aria-label="Название критерия" /><div class="hint cl-hint"><code>${c.id}</code></div></td>
          <td><input data-f="weight" type="text" inputmode="decimal" value="${c.weight}" class="cl-weight" aria-label="Вес критерия" /></td>
          <td>${phraseCell}</td>
        </tr>`;
      }).join("");
      return `<h3 class="cl-group">${escapeHtml(label)}
          <label class="cl-catw">доля в итоге <input type="text" inputmode="decimal" data-cat="${cat}" value="${cl.category_weights[cat]}" aria-label="Доля метрики «${escapeHtml(label)}» в итоговой оценке" /></label>
        </h3>
        <div class="table-scroll cl-scroll"><table class="cl-table">
          <thead><tr><th>Вкл.</th><th>Критерий</th><th>Вес</th><th>Ключевые фразы</th></tr></thead>
          <tbody>${rows}</tbody>
        </table></div>`;
    }).join("");
    card.innerHTML = `
      <p class="eyebrow">Оценка звонка</p>
      <h2>Чек-лист качества</h2>
      <p class="muted">Критерии, их вес внутри метрики, ключевые фразы и вес метрик в итоговой оценке. По умолчанию — встроенные правила. Изменения действуют для новых звонков; уже оценённые можно пересчитать кнопкой ниже (без повторной расшифровки, оценка LLM при пересчёте не повторяется).</p>
      <p class="muted">Вес критерия - от 0 до 5 с шагом 0,1 (например, 1,2), у включённого больше 0. Доля метрики - от 0 до 1, до трёх знаков после запятой; при сохранении доли приводятся к сумме 1 (например, 1, 1, 1, 1 станет по 0,25). Сейчас сумма долей: <b id="cl-sum">${Object.values(cl.category_weights).reduce((a, b) => a + Number(b), 0).toFixed(2).replace(".", ",")}</b>.</p>
      <form id="cl-form" novalidate>${groups}
        <div class="toolbar mt">
          <button class="btn primary row" type="submit">Сохранить чек-лист</button>
          <button class="btn ghost row" type="button" id="cl-reset">Вернуть по умолчанию</button>
          <button class="btn row" type="button" id="cl-rescore">Пересчитать оценки звонков</button>
        </div>
        <p class="form-error" id="cl-error" hidden></p>
      </form>
      ${isAdmin ? "" : `<p class="muted">Чек-лист меняет только администратор.</p>`}`;
    const form = $("#cl-form", card);
    if (!isAdmin) {
      form.querySelectorAll("input, textarea, button").forEach((el) => { el.disabled = true; });
      form.querySelector(".toolbar").hidden = true;
      return;
    }
    form.querySelectorAll("input[data-f=enabled]").forEach((box) => {
      box.onchange = () => box.closest("tr").classList.toggle("cl-off", !box.checked);
    });
    const sumBox = $("#cl-sum", card);
    form.querySelectorAll("input[data-cat]").forEach((el) => {
      el.oninput = () => {
        let sum = 0;
        form.querySelectorAll("input[data-cat]").forEach((x) => { sum += clNumber(x.value) ?? 0; });
        sumBox.textContent = sum.toFixed(2).replace(".", ",");
      };
    });
    form.onsubmit = async (e) => {
      e.preventDefault();
      const err = $("#cl-error", card);
      err.hidden = true;
      form.querySelectorAll(".cl-bad").forEach((el) => el.classList.remove("cl-bad"));
      const problems = [];
      const check = (el, label, min, max, positive, digits) => {
        const v = clNumber(el.value);
        const frac = (el.value.trim().replace(",", ".").split(".")[1] || "").replace(/0+$/, "").length;
        let msg = "";
        if (el.value.trim() === "") msg = `${label}: поле пустое, укажите число`;
        else if (v === null) msg = `${label}: «${el.value.trim().slice(0, 20)}» не число`;
        else if (frac > digits) msg = `${label}: допускается ${digits === 1 ? "один знак после запятой (шаг 0,1)" : "до трёх знаков после запятой (шаг 0,001)"}`;
        else if (v < min || v > max) msg = `${label}: допустимо от ${String(min).replace(".", ",")} до ${String(max).replace(".", ",")}`;
        else if (positive && v <= 0) msg = `${label}: должно быть больше 0`;
        if (msg) { el.classList.add("cl-bad"); problems.push(msg); }
      };
      const criteria = cl.criteria.map((c) => {
        const row = form.querySelector(`tr[data-id="${c.id}"]`);
        const enabled = row.querySelector("[data-f=enabled]").checked;
        const wEl = row.querySelector("[data-f=weight]");
        const title = row.querySelector("[data-f=title]").value.trim();
        check(wEl, `«${title || c.title}», вес`, 0, 5, enabled, 1);
        const out = { id: c.id, enabled, title, weight: wEl.value.trim() };
        const ta = row.querySelector("[data-f=phrases]");
        if (ta) out.phrases = ta.value.split(/\n+/).map((x) => x.trim()).filter(Boolean);
        return out;
      });
      const weights = {};
      form.querySelectorAll("input[data-cat]").forEach((el) => {
        check(el, `Доля метрики «${data.categories[el.dataset.cat]}»`, 0, 1, true, 3);
        weights[el.dataset.cat] = el.value.trim();
      });
      if (problems.length) {
        err.textContent = problems.slice(0, 4).join(". ") + (problems.length > 4 ? ` (и ещё ${problems.length - 4})` : "");
        err.hidden = false;
        toast(problems[0], true);
        form.querySelector(".cl-bad")?.focus();
        return;
      }
      try {
        // Значения уходят как введены: сервер сам проверит их и ответит по-русски.
        const saved = await api("/checklist", { method: "PUT", json: { criteria, category_weights: weights } });
        data.checklist = saved.checklist;
        toast(saved.message || "Чек-лист сохранён");
        paint(saved.checklist);
      } catch (ex) {
        err.textContent = ex.message;
        err.hidden = false;
        toast(ex.message, true);
      }
    };
    $("#cl-reset", card).onclick = async () => {
      if (!confirm("Вернуть встроенный чек-лист? Ваши фразы и веса будут заменены.")) return;
      try {
        const fresh = await api("/checklist/reset", { method: "POST" });
        toast("Чек-лист возвращён к умолчанию");
        paint(fresh.checklist);
      } catch (ex) { toast(ex.message, true); }
    };
    $("#cl-rescore", card).onclick = async (e) => {
      if (!confirm("Пересчитать оценки всех готовых звонков по сохранённому чек-листу?")) return;
      e.target.disabled = true;
      try {
        const res = await api("/checklist/rescore", { method: "POST" });
        toast(`Пересчитано звонков: ${res.rescored}`);
      } catch (ex) { toast(ex.message, true); }
      e.target.disabled = false;
    };
  };
  paint(data.checklist);
}

document.addEventListener("DOMContentLoaded", registerFeatureAudit);

/* ---------- Подсветка правил в расшифровке ---------- */

const HL_KINDS = {
  ok: "пункт чек-листа выполнен",
  bad: "нарушение",
  neg: "негативная лексика",
  pii: "персональные данные",
  miss: "пропущенный пункт",
};

function hlTip(item) {
  return escapeHtml([item.title, item.impact].filter(Boolean).join(" — "));
}

function turnText(seg, query) {
  const text = String(seg.text || "");
  const markers = (seg.markers || []).map((m) =>
    `<span class="hl-miss" title="${hlTip(m)}">${escapeHtml(m.title)}</span>`).join("");
  const head = markers ? `<span class="hl-markers">${markers}</span>` : "";
  if ((query || "").trim().length >= 2 || !(seg.spans || []).length) return head + highlight(text, query);
  let html = "";
  let pos = 0;
  for (const sp of seg.spans) {
    const start = Math.max(pos, Number(sp.start) || 0);
    const end = Math.min(text.length, Number(sp.end) || 0);
    if (end <= start) continue;
    html += escapeHtml(text.slice(pos, start));
    html += `<span class="hl hl-${escapeHtml(sp.kind)}" title="${hlTip(sp)}">${escapeHtml(text.slice(start, end))}</span>`;
    pos = end;
  }
  html += escapeHtml(text.slice(pos));
  return head + html;
}

function mountTurnLegend(call) {
  const box = $("#turns");
  if (!box || $("#hl-legend")) return;
  const counts = {};
  for (const s of call.segments || []) {
    for (const sp of s.spans || []) counts[sp.kind] = (counts[sp.kind] || 0) + 1;
    for (const m of s.markers || []) counts[m.kind] = (counts[m.kind] || 0) + 1;
  }
  if (!Object.keys(counts).length) return;
  const items = Object.entries(HL_KINDS).filter(([k]) => counts[k])
    .map(([k, label]) => `<span class="hl-key"><span class="${k === "miss" ? "hl-miss" : `hl hl-${k}`}">${label}</span> ${counts[k]}</span>`).join("");
  box.insertAdjacentHTML("beforebegin", `
    <div class="hl-legend" id="hl-legend">
      <label class="hl-toggle"><input type="checkbox" id="hl-on" checked /> Подсветка правил</label>
      ${items}
      <span class="hint">Наведите на фрагмент — правило и влияние на оценку</span>
    </div>`);
  $("#hl-on").onchange = (e) => box.classList.toggle("hl-off", !e.target.checked);
}

/* ---------- Сравнение операторов на дашборде ---------- */

function sparkline(trend) {
  const pts = (trend || []).filter((p) => p.overall != null);
  if (!pts.length) return `<span class="muted">—</span>`;
  const w = 120, h = 32, pad = 3;
  const xs = (i) => pts.length === 1 ? w / 2 : pad + (i * (w - 2 * pad)) / (pts.length - 1);
  const ys = (v) => h - pad - ((Math.max(0, Math.min(100, v))) / 100) * (h - 2 * pad);
  const line = pts.map((p, i) => `${xs(i).toFixed(1)},${ys(p.overall).toFixed(1)}`).join(" ");
  const tip = pts.map((p) => `неделя с ${fmtWeek(p.week)}: ${Number(p.overall).toFixed(0)} (${p.count} зв.)`).join("\n");
  const last = pts[pts.length - 1];
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="Тренд по неделям">
    <title>${escapeHtml(tip)}</title>
    <line x1="0" x2="${w}" y1="${ys(80)}" y2="${ys(80)}" class="spark-goal" />
    <polyline points="${line}" class="spark-line" fill="none" />
    <circle cx="${xs(pts.length - 1)}" cy="${ys(last.overall)}" r="2.6" class="spark-dot" />
  </svg>`;
}

function fmtWeek(iso) {
  const [y, m, d] = String(iso).split("-");
  return `${d}.${m}`;
}

function deltaBadge(d) {
  if (d == null) return `<span class="muted" title="Нужны две недели, в каждой хотя бы по 2 оценённых звонка">—</span>`;
  const cls = d > 0.4 ? "up" : d < -0.4 ? "down" : "flat";
  const sign = d > 0 ? "+" : "";
  return `<span class="delta ${cls}" title="Последняя неделя к предыдущей (недели, где не меньше 2 звонков)">${d > 0.4 ? "▲" : d < -0.4 ? "▼" : "■"} ${sign}${d.toFixed(1)}</span>`;
}

function compareRangeNote(data) {
  if (!data.data_from) return "";
  const from = fmtWeek(data.data_from);
  const to = fmtWeek(data.data_to);
  const spanDays = Math.round((new Date(data.data_to) - new Date(data.data_from)) / 86400000) + 1;
  const short = !data.period_days || spanDays < data.period_days;
  return ` Оценённые звонки есть с ${from} по ${to}${short && data.period !== "7d" ? " - поэтому «30 дней» и «всё время» могут показывать одинаковые цифры" : ""}.`;
}

function mistakeChips(list) {
  if (!list.length) return `<span class="muted">ошибок нет</span>`;
  return list.map((m) => `<span class="mistake" title="Провален в ${m.calls} зв. из периода">${escapeHtml(m.title)} · ${m.share.toFixed(0)}%</span>`).join("");
}

async function mountOperatorCompare(view) {
  const period = state.period === "7d" || state.period === "all" ? state.period : "30d";
  let data;
  try {
    data = await api(`/operators/compare?period=${period}`);
  } catch (ex) {
    return;
  }
  const self = data.scope === "self";
  const card = document.createElement("div");
  card.className = "card mt op-compare";
  const rows = data.operators.map((o) => `
    <tr data-op="${o.id}">
      ${self ? "" : `<td class="rank">${o.rank}</td>`}
      <td><b>${escapeHtml(o.name)}</b>${o.external_id ? `<div class="muted">${escapeHtml(o.external_id)}</div>` : ""}</td>
      <td>${o.calls_done}</td>
      <td>${pill(o.overall)}</td>
      <td>${deltaBadge(o.delta)}</td>
      <td>${sparkline(o.trend)}</td>
      <td>${o.violation_pct == null ? "—" : o.violation_pct.toFixed(0) + "%"}</td>
      <td class="mistakes">${mistakeChips(o.top_mistakes)}</td>
      ${self ? "" : `<td><button class="btn ghost small-btn" type="button" data-pdf="${o.id}" title="PDF-отчёт за период дашборда">PDF</button></td>`}
    </tr>`).join("");
  const team = data.team;
  card.innerHTML = `
    <h2>${self ? "Моя динамика" : "Сравнение операторов"}</h2>
    <p class="chart-lead">${self ? "Ваш средний балл по неделям и пункты чек-листа, которые чаще всего не выполнены." : "Рейтинг по среднему баллу, тренд по неделям (линия - цель 80) и самые частые ошибки каждого оператора."} Период как у дашборда.${compareRangeNote(data)}</p>
    ${data.unassigned ? `<p class="muted cmp-note">Звонков без оператора за период: <b>${data.unassigned}</b>. Они входят в общие цифры дашборда, но не в рейтинг и PDF. Оператора можно назначить в карточке звонка или указать при загрузке (поле «Оператор» или код вида OP-104 в имени файла).</p>` : ""}
    ${rows ? `<div class="table-scroll cmp-scroll"><table class="cmp-table">
      <thead><tr>${self ? "" : "<th>#</th>"}<th>Оператор</th><th>Звонков</th><th>Итог</th><th>К пред. неделе</th><th>Тренд</th><th>Нарушения</th><th>Частые ошибки</th>${self ? "" : "<th></th>"}</tr></thead>
      <tbody>${rows}</tbody></table></div>` : `<p class="empty">Нет оценённых звонков с оператором за период</p>`}
    ${team && team.top_mistakes.length ? `<h3 class="cmp-sub">Частые ошибки команды</h3>
      <div class="cmp-team">${team.top_mistakes.map((m) => `
        <div class="cmp-mistake"><span>${escapeHtml(m.title)}</span><progress class="meter" max="100" value="${m.share}"></progress><span class="muted">${m.calls} зв. · ${m.share.toFixed(0)}%</span></div>`).join("")}
      </div>` : ""}`;
  const cards = view.querySelectorAll(":scope > .card");
  const last = cards[cards.length - 1];
  if (last) view.insertBefore(card, last); else view.appendChild(card);
  const span = { "7d": 6, "30d": 29, all: 365 }[period];
  card.querySelectorAll("[data-pdf]").forEach((b) => {
    b.onclick = () => downloadOperatorPdf(b.dataset.pdf, daysAgo(span), isoDay(new Date()), b);
  });
}

/* ---------- PDF-отчёт по оператору ---------- */

function isoDay(d) {
  const z = new Date(d.getTime() - d.getTimezoneOffset() * 60000);
  return z.toISOString().slice(0, 10);
}

function daysAgo(n) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return isoDay(d);
}

async function downloadOperatorPdf(operatorId, dateFrom, dateTo, button) {
  if (button) button.disabled = true;
  try {
    const p = new URLSearchParams();
    if (dateFrom) p.set("date_from", dateFrom);
    if (dateTo) p.set("date_to", dateTo);
    const res = await api(`/reports/operator/${operatorId}.pdf?${p.toString()}`, { raw: true });
    const blob = await res.blob();
    const dispo = res.headers.get("content-disposition") || "";
    const star = /filename\*=UTF-8''([^;]+)/i.exec(dispo);
    const plain = /filename="?([^";]+)"?/i.exec(dispo);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = star ? decodeURIComponent(star[1]) : plain ? plain[1] : "report.pdf";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1500);
    toast("PDF-отчёт сформирован");
  } catch (err) {
    toast(err.message, true);
  }
  if (button) button.disabled = false;
}

function mountPdfReport(view, ops) {
  if (state.user?.role === "operator") return;
  const card = document.createElement("div");
  card.className = "card mt";
  card.innerHTML = `
    <h2>PDF-отчёт по оператору</h2>
    <p class="lead">Сводка метрик в сравнении с командой, тренд по неделям, частые ошибки с советами и примеры звонков с маской персональных данных. Файл собирается на этой машине.</p>
    <form id="pdf-rep" class="toolbar">
      <label>Оператор
        <select name="operator_id" required>${(ops || []).map((o) => `<option value="${o.id}">${escapeHtml(o.name)}${o.external_id ? ` · ${escapeHtml(o.external_id)}` : ""}</option>`).join("")}</select>
      </label>
      <label>С <input type="date" name="date_from" value="${daysAgo(29)}" required /></label>
      <label>По <input type="date" name="date_to" value="${isoDay(new Date())}" required /></label>
      <div class="seg pdf-quick">
        <button type="button" data-days="6">7 дней</button>
        <button type="button" data-days="29">30 дней</button>
        <button type="button" data-days="89">90 дней</button>
      </div>
      <button class="btn gold row" type="submit">Скачать PDF</button>
    </form>
    ${(ops || []).length ? "" : `<p class="empty">Нет операторов</p>`}`;
  view.appendChild(card);
  const form = $("#pdf-rep", card);
  form.querySelectorAll("[data-days]").forEach((b) => {
    b.onclick = () => {
      form.date_from.value = daysAgo(Number(b.dataset.days));
      form.date_to.value = isoDay(new Date());
    };
  });
  form.onsubmit = (e) => {
    e.preventDefault();
    if (!form.operator_id.value) return;
    if (form.date_from.value > form.date_to.value) { toast("Начало периода позже конца", true); return; }
    downloadOperatorPdf(form.operator_id.value, form.date_from.value, form.date_to.value, form.querySelector("[type=submit]"));
  };
}

/* ---------- Оператор звонка (звонки без оператора не попадают в рейтинг и PDF) ---------- */

async function mountOperatorAssign(call) {
  if (!state.user || state.user.role === "operator") return;
  const anchor = $("#note-form");
  if (!anchor) return;
  let ops;
  try {
    ops = await api("/operators");
  } catch {
    return;
  }
  const box = document.createElement("form");
  box.className = "op-assign mt";
  box.noValidate = true;
  const options = [`<option value="0">не указан</option>`]
    .concat(ops.map((o) => `<option value="${o.id}" ${o.id === call.operator_id ? "selected" : ""}>${escapeHtml(o.name)}${o.external_id ? " (" + escapeHtml(o.external_id) + ")" : ""}</option>`))
    .join("");
  box.innerHTML = `
    <label>Оператор звонка <select name="op">${options}</select></label>
    <button class="btn ghost row" type="submit">Сохранить оператора</button>
    ${call.operator_id ? "" : `<p class="muted">Без оператора звонок учитывается в общих цифрах, но не в рейтинге и PDF.</p>`}`;
  anchor.parentNode.insertBefore(box, anchor.previousElementSibling || anchor);
  box.onsubmit = async (e) => {
    e.preventDefault();
    const value = Number(box.querySelector("select").value);
    try {
      const saved = await api(`/calls/${call.id}`, { method: "PATCH", json: { operator_id: value } });
      call.operator_id = saved.operator_id;
      toast(saved.operator_name ? `Оператор: ${saved.operator_name}` : "Оператор снят");
      const hint = box.querySelector("p");
      if (hint) hint.hidden = !!saved.operator_id;
    } catch (ex) {
      toast(ex.message, true);
    }
  };
}
