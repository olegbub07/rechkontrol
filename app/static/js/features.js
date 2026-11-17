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
};

function registerFeatureAudit() {
  if (typeof AUDIT_RU === "object") Object.assign(AUDIT_RU, FEATURE_AUDIT_RU);
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
          <td><input data-f="title" value="${escapeHtml(c.title)}" maxlength="80" /><div class="hint cl-hint"><code>${c.id}</code></div></td>
          <td><input data-f="weight" type="number" min="0" max="5" step="0.1" value="${c.weight}" class="cl-weight" /></td>
          <td>${phraseCell}</td>
        </tr>`;
      }).join("");
      return `<h3 class="cl-group">${escapeHtml(label)}
          <label class="cl-catw">вес в итоге <input type="number" min="0" max="1" step="0.01" data-cat="${cat}" value="${cl.category_weights[cat]}" /></label>
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
      <form id="cl-form">${groups}
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
    form.onsubmit = async (e) => {
      e.preventDefault();
      const err = $("#cl-error", card);
      err.hidden = true;
      const criteria = cl.criteria.map((c) => {
        const row = form.querySelector(`tr[data-id="${c.id}"]`);
        const out = {
          id: c.id,
          enabled: row.querySelector("[data-f=enabled]").checked,
          title: row.querySelector("[data-f=title]").value.trim(),
          weight: Number(row.querySelector("[data-f=weight]").value),
        };
        const ta = row.querySelector("[data-f=phrases]");
        if (ta) out.phrases = ta.value.split(/\n+/).map((x) => x.trim()).filter(Boolean);
        return out;
      });
      const weights = {};
      form.querySelectorAll("input[data-cat]").forEach((el) => { weights[el.dataset.cat] = Number(el.value); });
      try {
        const saved = await api("/checklist", { method: "PUT", json: { criteria, category_weights: weights } });
        data.checklist = saved.checklist;
        toast("Чек-лист сохранён. Новые звонки оцениваются по нему");
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
