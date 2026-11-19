"use strict";
// Офлайн-описание API по /openapi.json: без Swagger UI с CDN, чтобы страница работала внутри контура.
(async function () {
  const out = document.getElementById("out");
  const pick = document.getElementById("tag");
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  let spec;
  try {
    spec = await (await fetch("/openapi.json", { credentials: "same-origin" })).json();
  } catch (err) {
    out.innerHTML = `<p>Не удалось загрузить /openapi.json: ${esc(err.message)}</p>`;
    return;
  }
  const refName = (ref) => (ref || "").split("/").pop();
  const typeOf = (s) => {
    if (!s) return "";
    if (s.$ref) return refName(s.$ref);
    if (s.anyOf) return s.anyOf.map(typeOf).join(" | ");
    if (s.enum) return s.enum.map((x) => JSON.stringify(x)).join(" | ");
    if (s.type === "array") return `${typeOf(s.items)}[]`;
    return s.format ? `${s.type} (${s.format})` : s.type || "object";
  };
  const schemaTable = (name) => {
    const s = (spec.components?.schemas || {})[name];
    if (!s || !s.properties) return "";
    const req = new Set(s.required || []);
    const rows = Object.entries(s.properties)
      .map(([k, v]) => `<tr><td><code>${esc(k)}</code>${req.has(k) ? " *" : ""}</td><td><code>${esc(typeOf(v))}</code></td><td>${esc(v.description || "")}</td></tr>`)
      .join("");
    return `<details><summary><code>${esc(name)}</code></summary><table>${rows}</table></details>`;
  };
  const ops = [];
  for (const [path, item] of Object.entries(spec.paths || {})) {
    for (const [method, op] of Object.entries(item)) ops.push({ path, method, op, tag: (op.tags || ["other"])[0] });
  }
  const tags = [...new Set(ops.map((o) => o.tag))];
  const first = tags.includes("external-api") ? "external-api" : tags[0];
  pick.innerHTML = tags.map((t) => `<option ${t === first ? "selected" : ""}>${esc(t)}</option>`).join("");
  const render = () => {
    const tag = pick.value;
    const info = (spec.tags || []).find((t) => t.name === tag);
    out.innerHTML = (info ? `<p>${esc(info.description)}</p>` : "") + ops.filter((o) => o.tag === tag).map(({ path, method, op }) => {
      const params = (op.parameters || []).map((p) => `<tr><td><code>${esc(p.name)}</code>${p.required ? " *" : ""}</td><td>${esc(p.in)}</td><td><code>${esc(typeOf(p.schema))}</code></td><td>${esc(p.description || p.schema?.description || "")}</td></tr>`).join("");
      const body = op.requestBody?.content || {};
      const bodyRef = Object.values(body)[0]?.schema?.$ref;
      const resp = Object.entries(op.responses || {}).map(([code, r]) => {
        const ref = r.content?.["application/json"]?.schema?.$ref;
        return `<tr><td><code>${esc(code)}</code></td><td>${esc(r.description || "")}</td><td>${ref ? schemaTable(refName(ref)) : ""}</td></tr>`;
      }).join("");
      const secured = (op.security || []).map((s) => Object.keys(s).join(", ")).join("; ");
      return `<section class="op">
        <h3><span class="m ${esc(method)}">${esc(method.toUpperCase())}</span><code>${esc(path)}</code></h3>
        <div><b>${esc(op.summary || "")}</b></div>
        ${op.description ? `<p class="muted">${esc(op.description)}</p>` : ""}
        ${secured ? `<div class="muted">Доступ: ${esc(secured)}</div>` : ""}
        ${params ? `<table><tr><th>Параметр</th><th>Где</th><th>Тип</th><th>Описание</th></tr>${params}</table>` : ""}
        ${bodyRef ? `<div>Тело (${esc(Object.keys(body)[0])}): ${schemaTable(refName(bodyRef))}</div>` : ""}
        ${resp ? `<table><tr><th>Код</th><th>Значение</th><th>Схема</th></tr>${resp}</table>` : ""}
      </section>`;
    }).join("");
  };
  pick.addEventListener("change", render);
  render();
})();
