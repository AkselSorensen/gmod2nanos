const $ = (id) => document.getElementById(id);
let mode = "workshop";
let file = null;
let poll = null;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const size = (n) => (n > 1e6 ? (n / 1e6).toFixed(1) + " Mo" : Math.max(1, Math.round(n / 1e3)) + " Ko");

async function api(url, opts) {
  const r = await fetch(url, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Erreur " + r.status);
  return body;
}

document.querySelectorAll(".tab").forEach((t) =>
  t.addEventListener("click", () => {
    mode = t.dataset.tab;
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t));
    document.querySelectorAll(".pane").forEach((p) => (p.hidden = p.dataset.pane !== mode));
  })
);

$("check").addEventListener("click", async () => {
  const box = $("preview");
  box.hidden = false;
  box.textContent = "Recherche…";
  try {
    const info = await api("/api/workshop/" + encodeURIComponent($("workshop").value.trim()));
    const items = info.is_collection
      ? `<ul>${info.items.map((i) => `<li>${esc(i.title)} <span class="tag">${size(i.size)}</span></li>`).join("")}</ul>`
      : `<div class="muted">${size(info.items[0].size)}${info.items[0].direct ? "" : " · téléchargé via SteamCMD"}</div>`;
    box.innerHTML = `${info.preview_url ? `<img src="${esc(info.preview_url)}" alt="">` : ""}
      <div><b>${esc(info.title)}</b>${info.is_collection ? ` <span class="tag">collection · ${info.items.length} addons</span>` : ""}${items}</div>`;
    if (!$("title").value) $("title").placeholder = info.title;
  } catch (e) {
    box.innerHTML = `<span class="error">${esc(e.message)}</span>`;
  }
});

const drop = $("drop");
const setFile = (f) => {
  file = f;
  $("drop-text").textContent = f ? `${f.name} (${size(f.size)})` : "Glisse un fichier ici ou clique pour choisir";
};
$("file").addEventListener("change", (e) => setFile(e.target.files[0]));
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => setFile(e.dataTransfer.files[0]));

$("form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("form-error").hidden = true;
  const opts = { title: $("title").value, author: $("author").value, pack_name: $("pack_name").value, scale: parseFloat($("scale").value) || 0.0254 };
  $("go").disabled = true;
  try {
    let job;
    if (mode === "workshop") {
      if (!$("workshop").value.trim()) throw new Error("Colle un lien du Workshop.");
      job = await api("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workshop: $("workshop").value, ...opts }) });
    } else {
      if (!file) throw new Error("Choisis un fichier.");
      const fd = new FormData();
      fd.append("file", file);
      Object.entries(opts).forEach(([k, v]) => fd.append(k, v));
      job = await api("/api/jobs/upload", { method: "POST", body: fd });
    }
    track(job.id);
  } catch (err) {
    $("form-error").textContent = err.message;
    $("form-error").hidden = false;
  } finally {
    $("go").disabled = false;
  }
});

function track(id) {
  clearTimeout(poll);
  let logSize = 0;
  $("job").hidden = false;
  $("log").textContent = "";
  $("summary").hidden = true;
  $("download").hidden = true;
  $("bar").parentElement.className = "bar";
  $("job").scrollIntoView({ behavior: "smooth" });
  history.replaceState(null, "", "#job=" + id);

  const tick = async () => {
    let job;
    try {
      job = await api(`/api/jobs/${id}?log_from=${logSize}`);
    } catch (e) {
      $("job-step").textContent = e.message;
      return;
    }
    $("job-title").textContent = job.title;
    $("job-step").textContent = job.error ? "Erreur : " + job.error : job.step;
    $("job-img").hidden = !job.preview_url;
    if (job.preview_url) $("job-img").src = job.preview_url;
    if (job.log.length) {
      $("log").textContent += job.log.join("\n") + "\n";
      $("log").scrollTop = $("log").scrollHeight;
    }
    logSize = job.log_size;
    if (job.status === "done") {
      $("bar").parentElement.className = "bar done";
      $("download").href = `/api/jobs/${id}/download`;
      $("download").hidden = false;
      showSummary(job.summary);
    } else if (job.status === "error") {
      $("bar").parentElement.className = "bar error";
      $("log-box").open = true;
    } else {
      poll = setTimeout(tick, 1000);
    }
  };
  tick();
}

function showSummary(s) {
  const rows = s.static_meshes
    .map((m) => `<tr><td><code>${esc(s.pack_name)}::${esc(m.key)}</code>${m.bones > 1 ? ' <span class="tag warn">riggé</span>' : ""}</td><td>${esc(m.source)}</td><td>${m.triangles}</td></tr>`)
    .join("");
  const skipped = Object.entries(s.skipped).map(([k, v]) => `${esc(k)} ×${v}`).join(", ");
  $("summary").innerHTML = `
    <div class="stats">
      <div class="stat"><b>${s.static_meshes.length}</b><span>modèles</span></div>
      <div class="stat"><b>${s.textures}</b><span>textures</span></div>
      <div class="stat"><b>${s.sounds}</b><span>sons .ogg</span></div>
      <div class="stat"><b>${s.warnings + s.errors.length}</b><span>avertissements</span></div>
    </div>
    ${rows ? `<div class="table-wrap"><table><tr><th>Clé nanos</th><th>Source</th><th>Triangles</th></tr>${rows}</table></div>` : ""}
    ${s.errors.length ? `<p class="error">${s.errors.map(esc).join("<br>")}</p>` : ""}
    ${skipped ? `<p class="muted">Non convertis : ${skipped}</p>` : ""}
    <p class="muted">Ouvre <code>LISEZMOI.md</code> dans le zip pour les étapes Unreal → nanos world.</p>`;
  $("summary").hidden = false;
}

const m = location.hash.match(/job=([a-f0-9]+)/);
if (m) track(m[1]);
