"use strict";
const $ = (s) => document.querySelector(s);
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  return r.json();
};
const post = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
// Open a URL outside the app: native browser when running in the desktop (pywebview) window,
// otherwise a new browser tab.
function openExternal(url) {
  if (window.pywebview && window.pywebview.api && window.pywebview.api.open_external) window.pywebview.api.open_external(url);
  else window.open(url, "_blank", "noopener");
}
// Open/download an attachment: Google Docs → Drive; everything else → download endpoint.
async function openAttachment(a) {
  if (a.drive) {
    // Drive/Docs files have no downloadable bytes — resolve the real document URL
    // from the message body, then open it (Gmail message as a fallback).
    let url = "https://drive.google.com/";
    try {
      const p = new URLSearchParams({ messageId: a.messageId || "", filename: a.filename || "" });
      if (a.threadId) p.set("threadId", a.threadId);
      const r = await api("/api/drive_link?" + p.toString());
      if (r && r.url) url = r.url;
    } catch (e) {}
    openExternal(url);
    return;
  }
  // Prefer opening the file natively (Preview foregrounds it). The browser fallback
  // streams the same bytes from the local endpoint, but can land in a background tab.
  const napi = window.pywebview && window.pywebview.api && window.pywebview.api.open_attachment;
  if (napi) {
    try {
      const r = await window.pywebview.api.open_attachment(a.messageId, a.attachmentId, a.filename || "attachment", a.mimeType || "");
      if (r && r.ok) return;
    } catch (e) {}
  }
  const url = `/api/attachment/${a.messageId}/${a.attachmentId}?name=${encodeURIComponent(a.filename)}&mime=${encodeURIComponent(a.mimeType)}`;
  openExternal(location.origin + url);
}

let PAGE = 50;
let STATE = {
  view: "inbox", data: null, threadCache: {}, inboxCache: null, query: "",
  openBundles: new Set(), token: null, selected: new Set(), snoozeMulti: false,
  // Normalized by-id index (same row objects as in data arrays) for optimistic UI.
  threads: new Map(), pendingActions: new Map(),
  settings: {}, undoSendWindow: 10, imageBlock: false,
};
let undoTimer = null;

// Index every visible row by thread id. The map holds the SAME objects that live in
// STATE.data's arrays, so a mutation through the map is visible to render().
function indexThreads(d) {
  STATE.threads.clear();
  const all = [
    ...(d.pinned || []), ...(d.primary || []), ...(d.items || []),
    ...((d.bundles || []).flatMap((b) => b.items || [])),
  ];
  all.forEach((r) => { if (r && r.id) STATE.threads.set(r.id, r); });
}

// Optimistic mutation: apply locally, render, flush to server, roll back on failure.
function optimistic(tid, mutation, serverCall) {
  const row = STATE.threads.get(tid);
  const snap = row ? { ...row } : null;
  if (row && mutation) mutation(row);
  render();
  return serverCall().catch((err) => {
    console.warn("[optimistic] rollback", tid, err);
    const cur = STATE.threads.get(tid);
    if (cur && snap) Object.assign(cur, snap);
    render();
    toast("Action failed");
  });
}

function applySettings(s) {
  s = s || {};
  const mode = s.dark_mode || "auto";
  const dark = mode === "dark" || (mode === "auto" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  STATE.imageBlock = s.image_block ?? false;
  STATE.undoSendWindow = s.undo_send_window ?? 10;
  STATE.templatesEnabled = s.templates_enabled ?? true;
  if (s.page_size) PAGE = s.page_size;
  const tb = $("#cTemplate");
  if (tb) tb.hidden = !STATE.templatesEnabled;
}

function initials(name) {
  const parts = (name || "?").trim().split(/\s+/);
  return ((parts[0]?.[0] || "?") + (parts[1]?.[0] || "")).toUpperCase();
}

// ---------- Rendering ----------
function draftCardEl(row) {
  const el = document.createElement("div");
  el.className = "card draft";
  el.innerHTML = `
    <div class="avatar" style="background:var(--fab-red)"><i class="material-icons" style="font-size:20px">edit</i></div>
    <div class="body">
      <div class="row1"><span class="sender">${esc(row.sender)}</span><span class="time">${esc(row.time)}</span></div>
      <div class="subject">${esc(row.subject)}</div>
      <div class="snippet">${esc(row.snippet)}</div>
    </div>
    <div class="actions"><button class="icon-btn act-discard" title="Discard draft"><i class="material-icons">delete</i></button></div>`;
  el.addEventListener("click", (e) => { if (e.target.closest(".actions")) return; openDraft(row.draftId); });
  el.querySelector(".act-discard").onclick = (e) => { e.stopPropagation(); discardDraft(row.draftId); };
  return el;
}

function cardEl(row) {
  if (row.draft) return draftCardEl(row);
  const el = document.createElement("div");
  const isSel = STATE.selected.has(row.id);
  el.className = "card" + (row.unread ? " unread" : "") + (isSel ? " selected" : "");
  el.dataset.tid = row.id;
  el.dataset.ts = row.ts == null ? "" : row.ts; // lets incremental backfill continue date grouping
  const chipsHtml = (row.highlights || []).length
    ? `<div class="chips">${row.highlights.map((h) => `<span class="chip"><i class="material-icons">${h.icon}</i>${esc(h.text)}</span>`).join("")}</div>`
    : "";
  const wakeHtml = row.wake ? `<div class="wake"><i class="material-icons">schedule</i>${esc(row.wake)}</div>` : "";
  const aliasHtml = row.alias
    ? `<div class="chips"><span class="chip alias-chip${row.alias.active ? "" : " dead"}" title="${esc(row.alias.description || row.alias.alias)}"><i class="material-icons">alternate_email</i>via ${esc(row.alias.alias)}${row.alias.active
        ? '<button class="alias-kill" title="Deactivate this alias (mail to it bounces)"><i class="material-icons">block</i></button>'
        : " · off"}</span></div>`
    : "";
  // In the Done view the triage actions (pin/snooze/label/done) don't apply; show a
  // persistent "Move back to Inbox" instead, alongside Send to Things 3.
  const inDone = STATE.view === "done";
  const actionsHtml = inDone
    ? `<div class="actions">
      <button class="icon-btn act-restore" title="Move back to Inbox"><i class="material-icons">move_to_inbox</i></button>
      <button class="icon-btn act-things" title="Send to Things 3"><img class="things-icon" src="/static/things-icon.png?v=37" alt="Send to Things 3"></button>
    </div>`
    : `<div class="actions">
      <button class="icon-btn act-pin" title="Pin"><i class="material-icons">push_pin</i></button>
      <button class="icon-btn act-snooze" title="Snooze"><i class="material-icons">schedule</i></button>
      <button class="icon-btn act-label" title="Move to bundle"><i class="material-icons">label</i></button>
      <button class="icon-btn act-done" title="Done"><i class="material-icons" style="color:var(--done-green)">playlist_add_check</i></button>
      <button class="icon-btn act-things" title="Send to Things 3"><img class="things-icon" src="/static/things-icon.png?v=37" alt="Send to Things 3"></button>
    </div>`;
  const attHtml = (row.attachments || []).length
    ? `<div class="chips">${row.attachments.map((a, i) => `<span class="chip att-chip" data-ai="${i}" title="${esc(a.filename)}"><i class="material-icons">${a.drive ? "description" : "attach_file"}</i>${esc(a.filename)}</span>`).join("")}</div>`
    : "";
  el.innerHTML = `
    <div class="select">
      <div class="avatar">${initials(row.sender)}</div>
      <span class="check"><i class="material-icons">${isSel ? "check_circle" : "radio_button_unchecked"}</i></span>
    </div>
    <div class="body">
      <div class="row1">
        <span class="sender">${esc(row.sender)} ${row.pinned ? '<i class="material-icons pin-dot">push_pin</i>' : ""}${row.unsub ? '<button class="unsub-link" title="Unsubscribe from this sender">Unsubscribe</button>' : ""}</span>
        <span class="time">${esc(row.time)}</span>
      </div>
      <div class="subject">${esc(row.subject)}</div>
      <div class="snippet">${esc(row.snippet)}</div>
      ${aliasHtml}${chipsHtml}${attHtml}${wakeHtml}
    </div>
    ${actionsHtml}`;
  el.addEventListener("click", (e) => {
    if (e.target.closest(".actions") || e.target.closest(".unsub-link") || e.target.closest(".select") || e.target.closest(".att-chip") || e.target.closest(".doc-chip") || e.target.closest(".alias-chip")) return;
    openThread(row.id);
  });
  // Doc/Drive links fetched lazily for this row (see enrichDocLinks) — render if cached.
  if (row.docLinks && row.docLinks.length) el.querySelector(".body").appendChild(docChipsEl(row.docLinks, "card"));
  el.querySelectorAll(".att-chip").forEach((c) => {
    c.onclick = (e) => { e.stopPropagation(); openAttachment({ ...row.attachments[+c.dataset.ai], threadId: row.id }); };
  });
  el.querySelector(".select").onclick = (e) => { e.stopPropagation(); toggleSelect(row.id, el); };
  // Guarded: the Done view renders a different action set (no pin/snooze/label/done).
  const wire = (sel, fn) => { const b = el.querySelector(sel); if (b) b.onclick = fn; };
  wire(".act-done", (e) => { e.stopPropagation(); doDone(row); });
  wire(".act-pin", (e) => { e.stopPropagation(); doPin(row); });
  wire(".act-snooze", (e) => { e.stopPropagation(); openSnooze(e.currentTarget, row); });
  wire(".act-label", (e) => { e.stopPropagation(); openRelabel(e.currentTarget, row); });
  wire(".act-things", (e) => { e.stopPropagation(); doThings(row); });
  wire(".act-restore", (e) => { e.stopPropagation(); doRestore(row); });
  const ul = el.querySelector(".unsub-link");
  if (ul) ul.onclick = (e) => { e.stopPropagation(); doUnsub(row.messageId, ul, row.sender); };
  const ak = el.querySelector(".alias-kill");
  if (ak) ak.onclick = async (e) => {
    e.stopPropagation();
    if (!confirm(`Deactivate ${row.alias.alias}? Mail to it will bounce. You can reactivate it in Aliases.`)) return;
    const r = await post("/api/alias_toggle", { id: row.alias.id, active: false });
    if (r.ok) { const c = ak.closest(".alias-chip"); c.classList.add("dead"); ak.remove(); c.insertAdjacentText("beforeend", " · off"); toast("Alias deactivated"); }
    else toast(r.error || "Failed");
  };
  enableSwipe(el, row);
  return el;
}

function bundleEl(b) {
  const el = document.createElement("div");
  el.className = "bundle";
  el.innerHTML = `
    <div class="bundle-head">
      <span class="bicon" style="background:${b.color}"><i class="material-icons" style="font-size:20px">${b.icon}</i></span>
      <span class="bname">${esc(b.name)}</span><span class="bcount">${b.count}</span>
      <button class="icon-btn bsweep" title="Sweep all to Done"><i class="material-icons" style="color:var(--done-green)">done_all</i></button>
    </div>
    <div class="bundle-items"></div>`;
  el.dataset.bname = b.name; // lets incremental backfill find this bundle to sync its count
  const head = el.querySelector(".bundle-head");
  const items = el.querySelector(".bundle-items");
  const populate = () => { if (!items.childElementCount) b.items.forEach((r) => items.appendChild(cardEl(r))); };
  // Restore expansion across re-renders (e.g. live-sync refresh)
  if (STATE.openBundles.has(b.name)) { populate(); el.classList.add("open"); }
  head.addEventListener("click", (e) => {
    if (e.target.closest(".bsweep")) return;
    populate();
    if (el.classList.toggle("open")) STATE.openBundles.add(b.name);
    else STATE.openBundles.delete(b.name);
  });
  el.querySelector(".bsweep").onclick = async (e) => {
    e.stopPropagation();
    if (el.classList.contains("sweeping")) return;      // guard double-clicks
    const ids = b.items.map((r) => r.id);
    if (!ids.length) return;
    // Optimistic, animated collapse of the whole category (no full reload, no scroll yank).
    el.style.maxHeight = el.offsetHeight + "px";
    el.classList.add("sweeping");
    requestAnimationFrame(() => { el.style.maxHeight = "0px"; });
    const idx = (STATE.data.bundles || []).findIndex((x) => x.name === b.name);
    const removed = idx >= 0 ? STATE.data.bundles.splice(idx, 1)[0] : null;
    ids.forEach((id) => STATE.threads.delete(id));
    STATE.openBundles.delete(b.name);
    setTimeout(() => { el.remove(); renderNavBundles(); }, 300);
    // Archive all members in one parallel round trip.
    let res;
    try { res = await post("/api/bulk_done", { threadIds: ids }); } catch (_) { res = { error: 1 }; }
    if (!res || res.error) {
      if (removed) STATE.data.bundles.splice(Math.min(idx, STATE.data.bundles.length), 0, removed);
      load();
      toast("Sweep failed — restored");
      return;
    }
    toast(`${b.name} swept to Done`, async () => {
      await post("/api/bulk_undo_done", { threadIds: ids });
      load();
    });
  };
  return el;
}

function render() {
  const list = $("#list");
  list.innerHTML = "";
  const d = STATE.data;
  $("#loading").hidden = true;
  $("#empty").hidden = true;
  if (!d) return;
  if (d.error) { list.innerHTML = `<div class="loading">${esc(d.error)}</div>`; return; }

  if (STATE.view === "inbox") {
    const pinnedOnly = $("#pinnedOnly").checked;
    if (d.pinned?.length) {
      list.appendChild(label("Pinned"));
      d.pinned.forEach((r) => list.appendChild(cardEl(r)));
    }
    if (!pinnedOnly) {
      d.bundles?.forEach((b) => list.appendChild(bundleEl(b)));
      if (d.primary?.length) appendGrouped(list, d.primary);
    }
    const total = (d.pinned?.length || 0) + (pinnedOnly ? 0 : (d.primary?.length || 0) + (d.bundles?.length || 0));
    if (!total) $("#empty").hidden = false;
  } else {
    const rows = d.items || [];
    if (!rows.length) $("#empty").hidden = false;
    if (STATE.view === "snoozed") rows.forEach((r) => list.appendChild(cardEl(r)));
    else appendGrouped(list, rows);
  }
  // Bottom footer: "N in view" + a "Load more" button when a full page came back
  const footer = footerNode();
  if (footer) list.appendChild(footer);
  // Gmail-style total next to the Inbox tab (last-known inbox total, persists across views)
  const it = STATE.inboxCache && STATE.inboxCache.inboxTotal;
  $("#inboxCount").textContent = it != null ? it.toLocaleString() : "";
}
// Build the "N in view" + "Load more" footer for the current view (null if neither applies).
// Backfill appends only new cards, so the button no longer needs to save/restore scroll.
function footerNode() {
  const d = STATE.data;
  if (!d) return null;
  let n;
  if (STATE.view === "inbox") {
    n = $("#pinnedOnly").checked
      ? (d.pinned?.length || 0)
      : (d.pinned?.length || 0) + (d.primary?.length || 0) + (d.bundles || []).reduce((s, b) => s + (b.count || 0), 0);
  } else {
    n = (d.items || []).length;
  }
  const hasMore = !!STATE.token;
  if (!(n || hasMore)) return null;
  const footer = document.createElement("div");
  footer.className = "list-footer";
  if (hasMore) {
    const btn = document.createElement("button");
    btn.className = "load-more";
    btn.textContent = "Load more";
    btn.onclick = () => { btn.textContent = "Loading…"; btn.disabled = true; loadMore(); };
    footer.appendChild(btn);
  }
  const count = document.createElement("div");
  count.className = "view-count";
  count.textContent = n ? `${n} in view` : "";
  footer.appendChild(count);
  return footer;
}
// Swap the footer in place after an incremental append (count + token may have changed).
function refreshFooter() {
  const list = $("#list");
  const old = list.querySelector(".list-footer");
  if (old) old.remove();
  const f = footerNode();
  if (f) list.appendChild(f);
}
// Full re-render that keeps the scroll position (used when backfill changes structure,
// e.g. a new bundle category appears, where an incremental append can't place it).
function scrollPreservingRender() {
  const sc = document.scrollingElement || document.documentElement;
  const y = sc.scrollTop;
  render();
  try { sc.scrollTop = y; } catch (e) {}
}
function label(t) { const e = document.createElement("div"); e.className = "section-label"; e.textContent = t; return e; }
function dateBucket(ts) {
  const t = +ts; if (!t) return "Older";
  const d = new Date(t), now = new Date();
  const sod = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const today = sod(now), day = 86400000, dd = sod(d);
  if (dd >= today) return "Today";
  if (dd >= today - day) return "Yesterday";
  if (dd >= today - 7 * day) return "This week";
  if (d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth()) return "This month";
  return "Older";
}
// Append cards with "Today / Yesterday / This week …" separators between date buckets.
function appendGrouped(list, rows) {
  let cur = null;
  (rows || []).forEach((r) => {
    const b = dateBucket(r.ts);
    if (b !== cur) { list.appendChild(label(b)); cur = b; }
    list.appendChild(cardEl(r));
  });
}
function esc(s) { return (s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function fmtBytes(n) {
  n = +n || 0;
  if (n < 1024) return n + " B";
  if (n < 1048576) return (n / 1024).toFixed(0) + " KB";
  return (n / 1048576).toFixed(1) + " MB";
}

// ---------- Actions ----------
async function doDone(row) {
  const snap = { ...row };
  removeCard(row.id);
  removeFromData(row.id);
  try {
    await post("/api/done", { threadId: row.id });
    toast("Marked done", async () => { await post("/api/undo_done", { threadId: row.id }); load(); });
  } catch (err) {
    // Auto-rollback: restore the card if the archive call never reached the server.
    (STATE.data.primary || STATE.data.items || (STATE.data.primary = [])).unshift(snap);
    STATE.threads.set(snap.id, snap);
    render();
    toast("Couldn't archive — restored");
  }
  ensureFilled();
}
// Optimistic pin: flip in place and move between Pinned/Mail sections — no reload flash.
async function doPin(row) {
  const willPin = !row.pinned;
  const d = STATE.data;
  optimistic(row.id, (r) => {
    r.pinned = willPin;
    if (d && STATE.view === "inbox" && d.pinned && d.primary) {
      const from = willPin ? d.primary : d.pinned;
      const to = willPin ? d.pinned : d.primary;
      const i = from.findIndex((x) => x.id === r.id);
      if (i >= 0) to.unshift(from.splice(i, 1)[0]);
    }
  }, () => post("/api/pin", { threadId: row.id, pinned: willPin }));
}
// ---------- Multi-select ----------
function toggleSelect(id, cardEl) {
  if (STATE.selected.has(id)) STATE.selected.delete(id);
  else STATE.selected.add(id);
  const on = STATE.selected.has(id);
  cardEl.classList.toggle("selected", on);
  const icon = cardEl.querySelector(".select .check .material-icons");
  if (icon) icon.textContent = on ? "check_circle" : "radio_button_unchecked";
  updateSelectionBar();
}
function clearSelection() {
  STATE.selected.clear();
  document.querySelectorAll(".card.selected").forEach((c) => {
    c.classList.remove("selected");
    const i = c.querySelector(".select .check .material-icons");
    if (i) i.textContent = "radio_button_unchecked";
  });
  updateSelectionBar();
}
function updateSelectionBar() {
  const n = STATE.selected.size;
  $("#selectionBar").hidden = n === 0;
  $("#selCount").textContent = n + " selected";
}
function findRow(id) {
  const d = STATE.data;
  if (!d) return null;
  const pools = [d.pinned, d.primary, d.items, ...((d.bundles || []).map((b) => b.items))];
  for (const p of pools) { if (p) { const r = p.find((x) => x.id === id); if (r) return r; } }
  return null;
}
async function bulkDone() {
  const ids = [...STATE.selected];
  if (!ids.length) return;
  // Animate the cards out and prune the model; archive in one parallel round trip.
  ids.forEach((id) => { removeCard(id); removeFromData(id); });
  clearSelection();
  ensureFilled(); // top the list back up smoothly, same as single Done
  let res;
  try { res = await post("/api/bulk_done", { threadIds: ids }); } catch (_) { res = { error: 1 }; }
  if (!res || res.error) { load(); toast("Couldn't archive — restored"); return; }
  toast(`${ids.length} marked done`, async () => {
    await post("/api/bulk_undo_done", { threadIds: ids });
    load();
  });
}
async function bulkPin() {
  const ids = [...STATE.selected];
  clearSelection();
  for (const id of ids) await post("/api/pin", { threadId: id, pinned: true });
  toast(`${ids.length} pinned`);
  load();
}
async function bulkThings() {
  const ids = [...STATE.selected];
  for (const id of ids) {
    const r = findRow(id) || {};
    await post("/api/to_things", { threadId: id, title: r.subject || "(email)", sender: r.sender || "", snippet: r.snippet || "" });
  }
  clearSelection();
  toast(`${ids.length} sent to Things 3`, async () => {
    for (const id of ids) await post("/api/undo_things", { threadId: id });
    toast(`Removed ${ids.length} from Things 3`);
  });
}
async function doUnsub(messageId, btn, name) {
  if (!messageId) return;
  const orig = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "Unsubscribing…"; }
  const r = await post("/api/unsubscribe", { messageId });
  if (r.ok && r.method === "link") {
    openExternal(r.url); toast("Opened unsubscribe page");
    if (btn) { btn.disabled = false; btn.textContent = orig; }
  } else if (r.ok) {
    toast(r.method === "mailto" ? "Unsubscribe email sent" : "Unsubscribed");
    if (btn) btn.textContent = "Unsubscribed ✓";
  } else if (r.fallbackUrl) {
    openExternal(r.fallbackUrl); toast("Finish unsubscribing in the browser");
    if (btn) { btn.disabled = false; btn.textContent = orig; }
  } else {
    toast("Unsubscribe failed");
    if (btn) { btn.disabled = false; btn.textContent = orig; }
  }
}
async function doThings(row) {
  const r = await post("/api/to_things", { threadId: row.id, title: row.subject, sender: row.sender, snippet: row.snippet });
  if (r.ok) toast("Sent to Things 3", async () => { await post("/api/undo_things", { threadId: row.id }); toast("Removed from Things 3"); });
  else toast("Things 3 not reachable");
}
// Move a thread out of Done and back to the Inbox (Done-view per-card action).
async function doRestore(row) {
  removeCard(row.id);
  removeFromData(row.id);
  STATE.inboxCache = null; // force the inbox to refetch so the restored thread reappears
  try {
    await post("/api/undo_done", { threadId: row.id });
    toast("Moved to Inbox", async () => { await post("/api/done", { threadId: row.id }); STATE.inboxCache = null; load(); });
  } catch (e) {
    toast("Couldn't move — restored");
    load();
  }
}
// Animate the card out AND collapse its height so the cards below glide up
// instead of snapping into the gap (matches the bundle-sweep collapse).
function removeCard(tid) {
  document.querySelectorAll(`.card[data-tid="${tid}"]`).forEach((c) => {
    if (c.classList.contains("removing")) return; // already collapsing
    c.style.maxHeight = c.offsetHeight + "px";
    c.getBoundingClientRect();                     // force reflow so max-height has a start value to animate from
    c.classList.add("removing");
    c.style.maxHeight = "0px";
    c.style.opacity = "0";
    c.style.marginBottom = "0px";
    c.style.paddingTop = "0px";
    c.style.paddingBottom = "0px";
    c.style.transform = "translateX(40px)";
    setTimeout(() => c.remove(), 300);
  });
}

// ---------- Snooze menu ----------
let snoozeTarget = null;
function positionSnooze(anchor) {
  const m = $("#snoozeMenu");
  const r = anchor.getBoundingClientRect();
  m.style.top = r.bottom + 6 + "px";
  m.style.left = Math.min(r.left, window.innerWidth - 240) + "px";
  m.hidden = false;
}
function openSnooze(anchor, row) {
  STATE.snoozeMulti = false;
  snoozeTarget = row;
  positionSnooze(anchor);
}
function openSnoozeMulti(anchor) {
  STATE.snoozeMulti = true;
  positionSnooze(anchor);
}
function afterTriage() {
  if (!$("#reader").hidden) closeReader();
  else load();
}
async function commitSnooze(payload) {
  $("#snoozeMenu").hidden = true;
  const ids = STATE.snoozeMulti ? [...STATE.selected] : (snoozeTarget ? [snoozeTarget.id] : []);
  if (!ids.length) return;
  ids.forEach(removeCard);
  if (STATE.snoozeMulti) clearSelection();
  for (const id of ids) await post("/api/snooze", Object.assign({ threadId: id }, payload));
  STATE.snoozeMulti = false;
  toast(ids.length > 1 ? `${ids.length} snoozed` : "Snoozed",
        async () => { for (const id of ids) await post("/api/unsnooze", { threadId: id }); load(); });
  afterTriage();
}
$("#snoozeMenu").querySelectorAll("a").forEach((a) => {
  a.onclick = () => {
    const preset = a.dataset.preset;
    if (preset === "pick") {
      const dt = $("#snoozeDT");
      dt.onchange = () => {
        const epoch = Math.floor(new Date(dt.value).getTime() / 1000);
        if (epoch) commitSnooze({ epoch });
      };
      dt.showPicker ? dt.showPicker() : dt.focus();
      return;
    }
    commitSnooze({ preset });
  };
});
document.addEventListener("click", (e) => {
  if (!e.target.closest("#snoozeMenu") && !e.target.closest(".act-snooze") &&
      e.target.id !== "readerSnooze" && !e.target.closest("#selSnooze"))
    $("#snoozeMenu").hidden = true;
});

// ---------- Selection bar wiring ----------
$("#selClear").onclick = clearSelection;
$("#selDone").onclick = bulkDone;
$("#selPin").onclick = bulkPin;
$("#selThings").onclick = bulkThings;
$("#selSnooze").onclick = (e) => openSnoozeMulti(e.currentTarget);

// ---------- Re-label / move to bundle ----------
const BUNDLES = [
  { name: "Primary", icon: "inbox" }, { name: "Travel", icon: "flight" },
  { name: "Purchases", icon: "local_offer" }, { name: "Finance", icon: "attach_money" },
  { name: "Promos", icon: "loyalty" }, { name: "Social", icon: "people" },
  { name: "Updates", icon: "notifications" }, { name: "Forums", icon: "forum" },
];
let relabelTarget = null;
function openRelabel(anchor, row) {
  relabelTarget = row;
  const opts = $("#relabelOptions");
  opts.innerHTML = "";
  BUNDLES.forEach((b) => {
    const a = document.createElement("a");
    a.innerHTML = `<i class="material-icons">${b.icon}</i> ${b.name}`;
    a.onclick = () => commitRelabel(b.name);
    opts.appendChild(a);
  });
  $("#relabelFuture").checked = false;
  $("#relabelFutureLabel").textContent = "Apply to future from " + (row.sender || "sender");
  const m = $("#relabelMenu");
  const r = anchor.getBoundingClientRect();
  m.style.top = r.bottom + 6 + "px";
  m.style.left = Math.min(r.left, window.innerWidth - 280) + "px";
  m.hidden = false;
}
async function commitRelabel(bundle) {
  $("#relabelMenu").hidden = true;
  if (!relabelTarget) return;
  const future = $("#relabelFuture").checked;
  const res = await post("/api/relabel", {
    threadId: relabelTarget.id, bundle, sender: relabelTarget.senderEmail || "", applyFuture: future,
  });
  if (res.ok) {
    toast(future && res.filtered ? `Moved to ${bundle} · future mail too` : `Moved to ${bundle}`);
    STATE.inboxCache = null;
    afterTriage();
  } else {
    toast("Re-label failed: " + (res.error || ""));
  }
}
document.addEventListener("click", (e) => {
  if (!e.target.closest("#relabelMenu") && !e.target.closest(".act-label") && !e.target.closest("#readerLabel"))
    $("#relabelMenu").hidden = true;
});

// ---------- Swipe (right=Done, left=Snooze) ----------
function enableSwipe(el, row) {
  let startX = 0, dx = 0, dragging = false;
  el.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".actions") || e.target.closest(".unsub-link") || e.target.closest(".select") || e.target.closest(".att-chip")) return;
    startX = e.clientX; dragging = true; el.classList.add("swiping"); el.setPointerCapture(e.pointerId);
  });
  el.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    dx = e.clientX - startX;
    el.style.transform = `translateX(${dx}px)`;
    el.style.background = dx > 40 ? "#E6F4EA" : dx < -40 ? "#FEF7E0" : "";
  });
  el.addEventListener("pointerup", () => {
    if (!dragging) return; dragging = false; el.classList.remove("swiping"); el.style.background = "";
    if (dx > 90) { el.style.transform = "translateX(100%)"; doDone(row); }
    else if (dx < -90) { openSnoozeFromSwipe(el, row); el.style.transform = ""; }
    else el.style.transform = "";
    dx = 0;
  });
}
function openSnoozeFromSwipe(el, row) { openSnooze(el.querySelector(".act-snooze") || el, row); }

// ---------- Thread reader ----------
// Block remote images (read-receipt tracking) by injecting a CSP into the sandboxed
// srcdoc. data: images and inline styles still render; remote http(s) images don't.
const IMG_CSP = '<meta http-equiv="Content-Security-Policy" content="img-src data:; style-src \'unsafe-inline\' data:; font-src data:; default-src \'none\'">';
const HAS_REMOTE_IMG = /<img[^>]+src=["']?https?:/i;
// Force every email link to default to a new browsing context. Paired with the iframe's
// allow-popups sandbox flag, this routes every click through pywebview's createWebView
// delegate, which opens the link's real URL in the default browser. pywebview only sends
// target=_blank links to the browser (never normal in-frame clicks), so without this a link
// would just navigate the app in-place. See stripIframeLinkTargets for the full mechanism.
const BASE_BLANK = '<base target="_blank">';

function renderMsgBody(mb, m, forceImages) {
  if (mb.dataset.rendered && !forceImages) return;
  mb.dataset.rendered = "1";
  mb.innerHTML = "";
  if (!m.html) { mb.textContent = m.text || "(no content)"; return; }
  const blocked = STATE.imageBlock && !forceImages;
  if (blocked && HAS_REMOTE_IMG.test(m.html)) {
    const banner = document.createElement("div");
    banner.className = "img-banner";
    banner.innerHTML = `<i class="material-icons">visibility_off</i><span>Remote images blocked.</span><button class="img-load">Load images</button>`;
    banner.querySelector(".img-load").onclick = () => renderMsgBody(mb, m, true);
    mb.appendChild(banner);
  }
  const f = document.createElement("iframe");
  // allow-same-origin (NEVER allow-scripts) lets the parent read the email DOM for
  // height + link routing; email JS stays disabled by the sandbox spec.
  // allow-popups is REQUIRED: BASE_BLANK makes every link target a new context, and a
  // sandbox without allow-popups BLOCKS that navigation outright — the click reaches
  // neither pywebview's native external-link handler nor (when the iframe's load event
  // misfires) our JS router, so links silently do nothing. With allow-popups the _blank
  // click reaches pywebview's createWebView handler, which opens it in the real browser.
  // No popup window ever appears: pywebview intercepts and returns nil. Email JS still
  // can't open windows — that needs allow-scripts, which we never grant.
  f.setAttribute("sandbox", "allow-same-origin allow-popups");
  f.style.width = "100%";
  f.style.minHeight = "120px";
  // Once the email DOM is reachable, size the frame to its content and strip explicit link
  // targets (see stripIframeLinkTargets). We can't rely on the iframe `load` event alone —
  // for srcdoc content in WKWebView it fires unreliably — so we also poll. The readiness
  // gate (about:srcdoc URL, or a populated body) skips the transient about:blank document
  // the iframe shows before the srcdoc commits.
  const routeWhenReady = () => {
    let tries = 0;
    const tick = () => {
      let ok = false;
      try {
        const doc = f.contentDocument || (f.contentWindow && f.contentWindow.document);
        if (doc && doc.body && (doc.URL === "about:srcdoc" || doc.body.children.length > 0)) {
          f.style.height = (doc.body.scrollHeight + 24) + "px";
          stripIframeLinkTargets(doc);
          ok = true;
        }
      } catch (e) {}
      if (!ok && tries++ < 40) setTimeout(tick, 50);
    };
    tick();
  };
  f.onload = routeWhenReady;
  mb.appendChild(f);
  f.srcdoc = BASE_BLANK + (blocked ? IMG_CSP : "") + m.html;
  routeWhenReady();
}
// Email links open in the system browser entirely through pywebview's NATIVE handler:
// BASE_BLANK makes every link target a new browsing context, allow-popups lets that click
// reach pywebview's createWebView delegate, and the delegate hands the link's real URL to
// the default browser (no popup window ever appears). We tried routing clicks in JS, but in
// this WKWebView the in-iframe click listener never fires, so the native path is the only
// reliable one — and it reads the link's own resolved URL.
//
// So JS must do exactly one thing and NOT a second: strip any EXPLICIT target on a link
// (target="_self"/"_top" would override BASE_BLANK and navigate the app in-frame). It must
// NOT rewrite href — a placeholder like "#" resolves to the app's own 127.0.0.1 origin, so
// the native handler would open the app instead of the link (the bug we just had).
function stripIframeLinkTargets(doc) {
  doc.querySelectorAll("a[target]").forEach((a) => a.removeAttribute("target"));
}

async function openThread(tid) {
  const reader = $("#reader");
  $("#readerSubject").textContent = "Loading…";
  $("#readerBody").innerHTML = "";
  // reset the reply editor for the new thread
  $("#replyBox").innerHTML = "";
  STATE.replyAttachments = [];
  renderChips("replyAttachments", "#rAttachments");
  reader.hidden = false;
  const t = await api("/api/thread/" + tid);
  STATE.threadCache[tid] = t;
  if (t.error) { $("#readerSubject").textContent = "Error"; $("#readerBody").textContent = t.error; return; }
  // Restore an autosaved reply draft for this thread (if any), and reset autosave state.
  clearTimeout(replyDraftTimer); replySaving = false; replyDirty = false;
  STATE.replyDraftId = (t.replyDraft && t.replyDraft.draftId) || null;
  if (t.replyDraft && t.replyDraft.html) $("#replyBox").innerHTML = t.replyDraft.html;
  setReplyStatus(STATE.replyDraftId ? "Draft saved" : "");
  $("#readerSubject").textContent = t.subject;
  reader.dataset.tid = tid;
  reader.dataset.rfc = t.rfcMessageId || "";
  reader.dataset.rfcChain = (t.allRfcIds || []).join(" ");
  reader.dataset.subject = t.subject;
  reader.dataset.muted = t.muted ? "1" : "";
  // Reply-via-alias: if this thread arrived to an active alias, offer (and default to)
  // sending the reply through addy so the contact never sees the real Gmail address.
  const aliasWrap = $("#replyViaAlias");
  if (t.alias && t.alias.active) {
    reader.dataset.alias = t.alias.alias;
    $("#replyViaAliasChk").checked = true;
    $("#replyViaAliasLabel").textContent = "as " + t.alias.alias;
    aliasWrap.hidden = false;
  } else {
    reader.dataset.alias = "";
    $("#replyViaAliasChk").checked = false;
    aliasWrap.hidden = true;
  }
  // Reply vs Reply-all recipient sets (minus self)
  const last = t.messages[t.messages.length - 1] || {};
  const me = (STATE.email || "").toLowerCase();
  const parseAddrs = (s) => (s || "").split(/,\s*/).map((a) => {
    const m = a.match(/<([^>]+)>/); return (m ? m[1] : a).trim();
  }).filter(Boolean);
  reader.dataset.to = last.senderEmail || "";
  const all = new Set([last.senderEmail, ...parseAddrs(last.to), ...parseAddrs(last.cc)]);
  [...all].forEach((a) => { if (a && a.toLowerCase() === me) all.delete(a); });
  all.delete("");
  reader.dataset.toAll = [...all].join(", ");
  // Show "Reply all" only when there's more than one other recipient
  $("#replySendAll").hidden = all.size <= 1;
  $("#readerMute").querySelector(".material-icons").textContent = t.muted ? "volume_up" : "volume_off";
  const body = $("#readerBody");
  body.innerHTML = "";
  if (t.unsub) {
    const banner = document.createElement("div");
    banner.className = "unsub-banner";
    banner.innerHTML = `<i class="material-icons">unsubscribe</i><span>You're subscribed to this mailing list.</span><button class="unsub-btn">Unsubscribe</button>`;
    const mid = t.unsubMessageId || (t.messages[t.messages.length - 1] || {}).id;
    const who = (t.messages[t.messages.length - 1] || {}).sender || t.subject;
    banner.querySelector(".unsub-btn").onclick = (e) => doUnsub(mid, e.currentTarget, who);
    body.appendChild(banner);
  }
  const lastIdx = t.messages.length - 1;
  t.messages.forEach((m, idx) => {
    const collapsed = t.messages.length > 2 && idx < lastIdx;
    const d = document.createElement("div");
    d.className = "msg" + (collapsed ? " collapsed" : "");
    d.innerHTML = `<div class="mhead"><span class="mfrom">${esc(m.sender)}</span><span>${esc(m.date)}</span></div>
      <div class="mpreview">${esc((m.text || "").replace(/\s+/g, " ").trim().slice(0, 120))}</div>
      <div class="mbody"></div>`;
    const mb = d.querySelector(".mbody");
    // The whole collapsed message is the expand target (the header strip alone was
    // too small to hit, and the preview line below it looked clickable but wasn't).
    // Once expanded this no-ops so inner links/attachments keep working.
    d.addEventListener("click", (e) => {
      if (!d.classList.contains("collapsed")) return;
      if (e.target.closest(".msg-attachments")) return; // let attachment chips open instead
      d.classList.remove("collapsed");
      renderMsgBody(mb, m);
    });
    if (!collapsed) renderMsgBody(mb, m);
    if (m.attachments && m.attachments.length) {
      const wrap = document.createElement("div");
      wrap.className = "msg-attachments";
      m.attachments.forEach((a) => {
        const att = { ...a, messageId: a.messageId || m.id, threadId: t.id };
        const chip = document.createElement("button");
        chip.className = "attach-chip";
        chip.innerHTML = `<i class="material-icons" style="font-size:16px;color:#5f6368">${a.drive ? "description" : "attach_file"}</i><span>${esc(a.filename)}</span><small>${a.drive ? "Drive" : fmtBytes(a.size)}</small>`;
        chip.onclick = () => openAttachment(att);
        wrap.appendChild(chip);
      });
      d.appendChild(wrap);
    }
    // Shared Google Docs/Drive links surfaced as real chips (they arrive as plain
    // body links, not Gmail chips), rendered outside the sandboxed iframe so a click
    // reliably opens them in the browser.
    if (m.docLinks && m.docLinks.length) d.appendChild(docChipsEl(m.docLinks, "reader"));
    body.appendChild(d);
  });
}
const DOC_ICONS = { doc: "description", sheet: "table_chart", slides: "slideshow",
                    form: "assignment", folder: "folder", file: "insert_drive_file" };
// Chips for shared Docs/Drive links. style "card" = compact pill row (inbox row),
// "reader" = larger attachment-style chip. Click opens the doc in the browser.
function docChipsEl(links, style) {
  const wrap = document.createElement("div");
  wrap.className = style === "reader" ? "msg-attachments doc-links" : "chips doc-links";
  links.forEach((dl) => {
    const chip = document.createElement(style === "reader" ? "button" : "span");
    chip.className = (style === "reader" ? "attach-chip" : "chip") + " doc-chip";
    const icon = `<i class="material-icons">${DOC_ICONS[dl.kind] || "link"}</i>`;
    chip.innerHTML = style === "reader"
      ? `${icon}<span>${esc(dl.title)}</span><small>${dl.kind === "folder" ? "Drive" : "Google " + (dl.kind || "doc")}</small>`
      : `${icon}${esc(dl.title)}`;
    chip.onclick = (e) => { e.stopPropagation(); openExternal(dl.url); };
    wrap.appendChild(chip);
  });
  return wrap;
}
function closeReader() {
  clearTimeout(replyDraftTimer);
  // Capture the last keystrokes of an in-progress reply before leaving the thread.
  if (!$("#reader").hidden && replyHasContent()) saveReplyDraftNow();
  $("#reader").hidden = true; load();
}
$("#readerBack").onclick = closeReader;
$("#readerDone").onclick = async () => { await post("/api/done", { threadId: $("#reader").dataset.tid }); toast("Marked done"); closeReader(); };
$("#readerPin").onclick = async () => { const t = STATE.threadCache[$("#reader").dataset.tid]; await post("/api/pin", { threadId: $("#reader").dataset.tid, pinned: !(t && t.pinned) }); toast("Pinned"); };
$("#readerSnooze").onclick = (e) => { const r = $("#reader"); openSnooze(e.currentTarget, { id: r.dataset.tid }); };
$("#readerLabel").onclick = (e) => {
  const t = STATE.threadCache[$("#reader").dataset.tid];
  const m0 = (t && t.messages && t.messages[0]) || {};
  openRelabel(e.currentTarget, { id: $("#reader").dataset.tid, sender: m0.sender, senderEmail: m0.senderEmail });
};
$("#readerThings").onclick = async () => {
  const r = $("#reader");
  const t = STATE.threadCache[r.dataset.tid];
  const first = t?.messages?.[0] || {};
  const res = await post("/api/to_things", { threadId: r.dataset.tid, title: r.dataset.subject, sender: first.sender || "", snippet: (first.text || "").slice(0, 140) });
  toast(res.ok ? "Sent to Things 3" : "Things 3 not reachable");
};
// Unified handling of /api/send responses (immediate, undo-window, or scheduled).
function handleSendResult(res) {
  if (!res) { toast("Send failed"); return false; }
  if (res.actionId && res.undoWindow) {
    toast(`Sending in ${res.undoWindow}s`, async () => {
      const c = await post("/api/cancel_send", { actionId: res.actionId });
      toast(c.cancelled ? "Send cancelled" : "Already sent");
    });
    return true;
  }
  if (res.scheduled) { toast("Scheduled to send"); return true; }
  if (res.ok) { toast("Sent"); return true; }
  toast("Send failed: " + (res.error || ""));
  return false;
}

async function sendReply(all) {
  const r = $("#reader");
  const box = $("#replyBox");
  if (!box.innerText.trim() && !(STATE.replyAttachments || []).length) return;
  clearTimeout(replyDraftTimer); // cancel pending autosave so it can't resurrect after send
  const subj = r.dataset.subject.startsWith("Re:") ? r.dataset.subject : "Re: " + r.dataset.subject;
  const fd = new FormData();
  fd.append("to", all ? (r.dataset.toAll || r.dataset.to) : r.dataset.to);
  fd.append("subject", subj);
  fd.append("html", box.innerHTML);
  fd.append("text", box.innerText);
  fd.append("threadId", r.dataset.tid);
  fd.append("inReplyTo", r.dataset.rfc);
  fd.append("references", r.dataset.rfcChain || "");
  if (r.dataset.alias && $("#replyViaAliasChk").checked) fd.append("viaAlias", r.dataset.alias);
  if (STATE.replyDraftId) fd.append("draftId", STATE.replyDraftId); // server deletes the autosaved draft on send
  (STATE.replyAttachments || []).forEach((f) => fd.append("attachments", f, f.name));
  const btns = [$("#replySend"), $("#replySendAll")];
  btns.forEach((b) => (b.disabled = true));
  let res;
  try { res = await (await fetch("/api/send", { method: "POST", body: fd })).json(); }
  catch (e) { res = { error: String(e) }; }
  btns.forEach((b) => (b.disabled = false));
  if (handleSendResult(res)) {
    const tid = r.dataset.tid;
    STATE.replyDraftId = null; setReplyStatus("");
    box.innerHTML = ""; STATE.replyAttachments = []; renderChips("replyAttachments", "#rAttachments");
    setTimeout(() => { if ($("#reader").dataset.tid === tid && !$("#reader").hidden) openThread(tid); },
              (res.undoWindow || 0) * 1000 + 900);
  }
}
$("#replySend").onclick = () => sendReply(false);
$("#replySendAll").onclick = () => sendReply(true);
// ---------- Reply autosave (mirrors compose) ----------
let replyDraftTimer = null, replySaving = false, replyDirty = false;
function setReplyStatus(t) { const e = $("#replySaveStatus"); if (e) e.textContent = t; }
function replyHasContent() { return !!$("#replyBox").innerText.trim(); }
function replySubject() {
  const s = $("#reader").dataset.subject || "";
  return /^re:/i.test(s) ? s : "Re: " + s;
}
function scheduleReplyDraftSave() {
  if ($("#reader").hidden) return;
  setReplyStatus(replyHasContent() ? "Saving…" : "");
  clearTimeout(replyDraftTimer);
  replyDraftTimer = setTimeout(saveReplyDraftNow, 1100);
}
async function saveReplyDraftNow() {
  clearTimeout(replyDraftTimer);
  if (!replyHasContent()) { setReplyStatus(""); return; }
  if (replySaving) { replyDirty = true; return; } // serialize so we never create dupes
  replySaving = true; replyDirty = false;
  setReplyStatus("Saving…");
  const r0 = $("#reader");
  let r = null;
  try {
    r = await post("/api/save_draft", {
      to: r0.dataset.to || "", subject: replySubject(),
      html: $("#replyBox").innerHTML, body: $("#replyBox").innerText,
      threadId: r0.dataset.tid, inReplyTo: r0.dataset.rfc || null,
      references: r0.dataset.rfcChain || null,
      draftId: STATE.replyDraftId || null,
    });
  } catch (e) { r = null; }
  if (r && r.draftId) { STATE.replyDraftId = r.draftId; setReplyStatus("Draft saved"); }
  else setReplyStatus("Couldn't save draft");
  replySaving = false;
  if (replyDirty) saveReplyDraftNow();
}
$("#replyBox").addEventListener("input", scheduleReplyDraftSave);

async function doForward() {
  const r = $("#reader");
  const t = STATE.threadCache[r.dataset.tid];
  if (!t) return;
  const last = (t.messages || [])[(t.messages || []).length - 1] || {};
  const fwd = `<br><br>---------- Forwarded message ---------<br>` +
    `<b>From:</b> ${esc(last.sender)} &lt;${esc(last.senderEmail)}&gt;<br>` +
    `<b>Date:</b> ${esc(last.date)}<br>` +
    `<b>Subject:</b> ${esc(t.subject)}<br><br>` +
    (last.html || esc(last.text || "(no content)"));
  openCompose({
    subject: (t.subject || "").startsWith("Fwd:") ? t.subject : "Fwd: " + (t.subject || ""),
    html: fwd,
    forwardAttachments: (last.attachments || []).map((a) => ({ ...a, messageId: a.messageId || last.id })),
  });
}
$("#readerForward").onclick = doForward;
$("#readerMute").onclick = async () => {
  const r = $("#reader");
  const willMute = r.dataset.muted !== "1";
  await post("/api/mute", { threadId: r.dataset.tid, muted: willMute });
  toast(willMute ? "Thread muted" : "Unmuted");
  if (willMute) closeReader();
  else { r.dataset.muted = ""; $("#readerMute").querySelector(".material-icons").textContent = "volume_off"; }
};
$("#readerMarkUnread").onclick = async () => {
  await post("/api/mark", { threadId: $("#reader").dataset.tid, read: false });
  toast("Marked unread"); closeReader();
};
$("#readerFollowup").onclick = async () => {
  const r = await post("/api/followup", { threadId: $("#reader").dataset.tid });
  toast(r.deduped ? "Reminder already set" : `Will remind in ${r.days || 3} days if no reply`);
};

// ---------- Compose ----------
function openCompose(opts = {}) {
  STATE.composeDraftId = opts.draftId || null;
  clearTimeout(draftSaveTimer); draftSaving = false; draftDirty = false;
  setSaveStatus(opts.draftId ? "Draft saved" : "");
  STATE.attachments = [];
  STATE.fwdAttachments = (opts.forwardAttachments || []).map((a) => ({ ...a, keep: !a.drive }));
  STATE.scheduleAt = 0;
  $("#composeTitle").textContent = opts.draftId ? "Draft" : (opts.subject && opts.subject.startsWith("Fwd:") ? "Forward" : "New message");
  $("#cTo").value = opts.to || "";
  $("#cCc").value = ""; $("#cBcc").value = "";
  $("#cCc").hidden = true; $("#cBcc").hidden = true;
  $("#cCcToggle").style.display = ""; $("#cBccToggle").style.display = "";
  $("#cSubject").value = opts.subject || "";
  const body = opts.html || (opts.body ? esc(opts.body).replace(/\n/g, "<br>") : "");
  const sig = STATE.settings.signature;
  // Append the signature on fresh compose/forward (not when reopening a saved draft).
  $("#cBody").innerHTML = body + (sig && !opts.draftId ? `<br><br><div class="sig">${sig}</div>` : "");
  renderAttachChips();
  renderFwdChips();
  $("#composeDiscard").style.display = STATE.composeDraftId ? "" : "none";
  populateFromSelector(opts.from);
  $("#composeOverlay").hidden = false;
  applyComposeExpanded(); // restore the remembered full-screen / compact state
  setTimeout(() => (opts.to ? $("#cBody") : $("#cTo")).focus(), 30);
}
// Fill the compose "From" dropdown: real Gmail (default) + active addy aliases.
// Picking an alias routes the send through addy so the recipient only sees the alias.
async function populateFromSelector(preferred) {
  const sel = $("#cFrom"), row = $("#cFromRow");
  sel.innerHTML = `<option value="">${esc(STATE.email || "me")}</option>`;
  row.hidden = false;
  try {
    const d = await api("/api/aliases");
    if (!d || d.configured === false) return;
    (d.aliases || []).filter((a) => a.active).forEach((a) => {
      const o = document.createElement("option");
      o.value = a.email;
      o.textContent = a.email + (a.description ? " — " + a.description : "");
      sel.appendChild(o);
    });
    if (preferred) sel.value = preferred;
  } catch (e) { /* addy optional — leave just the real address */ }
}
function renderFwdChips() {
  const wrap = $("#cFwdAttachments");
  if (!wrap) return;
  wrap.innerHTML = "";
  (STATE.fwdAttachments || []).forEach((a, i) => {
    const chip = document.createElement("label");
    chip.className = "attach-chip fwd";
    chip.innerHTML = `<input type="checkbox" ${a.keep ? "checked" : ""}${a.drive ? " disabled" : ""}>` +
      `<i class="material-icons" style="font-size:16px;color:#5f6368">attach_file</i><span>${esc(a.filename)}</span>`;
    chip.querySelector("input").onchange = (e) => { STATE.fwdAttachments[i].keep = e.target.checked; };
    wrap.appendChild(chip);
  });
}
function closeCompose() {
  clearTimeout(draftSaveTimer);
  // Capture the last keystrokes (the debounce may not have fired yet) when dismissing
  // via X / backdrop / Esc. Send & discard paths clear the fields first, so this no-ops.
  if (!$("#composeOverlay").hidden && composeHasContent()) saveDraftNow();
  $("#composeOverlay").hidden = true; $("#scheduleMenu").hidden = true; STATE.composeDraftId = null; STATE.attachments = []; STATE.fwdAttachments = []; setSaveStatus("");
}
function clearCompose() { $("#cTo").value = $("#cSubject").value = $("#cCc").value = $("#cBcc").value = ""; $("#cBody").innerHTML = ""; STATE.attachments = []; STATE.fwdAttachments = []; renderAttachChips(); renderFwdChips(); }
// ---------- Draft autosave ----------
let draftSaveTimer = null, draftSaving = false, draftDirty = false;
function setSaveStatus(t) { const e = $("#composeSaveStatus"); if (e) e.textContent = t; }
function composeHasContent() {
  return !!($("#cTo").value.trim() || $("#cSubject").value.trim() || $("#cCc").value.trim()
    || $("#cBcc").value.trim() || $("#cBody").innerText.trim());
}
// Debounced autosave: ~1.1s after the last keystroke, persist to a Gmail draft.
function scheduleDraftSave() {
  if ($("#composeOverlay").hidden) return;
  setSaveStatus(composeHasContent() ? "Saving…" : "");
  clearTimeout(draftSaveTimer);
  draftSaveTimer = setTimeout(saveDraftNow, 1100);
}
async function saveDraftNow() {
  clearTimeout(draftSaveTimer);
  if (!composeHasContent()) { setSaveStatus(""); return; }
  if (draftSaving) { draftDirty = true; return; } // serialize so we never create dupes
  draftSaving = true; draftDirty = false;
  setSaveStatus("Saving…");
  let r = null;
  try {
    r = await post("/api/save_draft", {
      to: $("#cTo").value, subject: $("#cSubject").value,
      html: $("#cBody").innerHTML, body: $("#cBody").innerText,
      cc: $("#cCc").value || null, bcc: $("#cBcc").value || null,
      draftId: STATE.composeDraftId || null,
    });
  } catch (e) { r = null; }
  if (r && r.draftId) {
    STATE.composeDraftId = r.draftId;       // reuse for subsequent saves (update in place)
    $("#composeDiscard").style.display = "";
    setSaveStatus("Draft saved");
  } else {
    setSaveStatus("Couldn't save draft");
  }
  draftSaving = false;
  if (draftDirty) saveDraftNow();           // changes arrived while we were saving
}
["#cTo", "#cSubject", "#cCc", "#cBcc"].forEach((s) => $(s).addEventListener("input", scheduleDraftSave));
$("#cBody").addEventListener("input", scheduleDraftSave);
$("#fab").onclick = () => openCompose();
$("#composeClose").onclick = closeCompose;
// Full-screen compose toggle — remembered across opens (like Gmail).
function applyComposeExpanded() {
  const on = localStorage.getItem("inbox.composeExpanded") === "1";
  $("#composeOverlay").classList.toggle("expanded", on);
  const ico = $("#composeExpand").querySelector(".material-icons");
  ico.textContent = on ? "close_fullscreen" : "open_in_full";
  $("#composeExpand").title = on ? "Exit full screen" : "Full screen";
}
$("#composeExpand").onclick = () => {
  const on = $("#composeOverlay").classList.contains("expanded");
  try { localStorage.setItem("inbox.composeExpanded", on ? "0" : "1"); } catch (e) {}
  applyComposeExpanded();
  $("#cBody").focus();
};
// Click the dimmed backdrop to dismiss compose
// Compose stays open until an explicit exit (the ✕, Send, or Discard). Clicking the
// dimmed backdrop does NOT dismiss it — too easy to lose a draft-in-progress by accident.
// Escape closes the topmost overlay
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  const helpO = $("#helpOverlay");
  if (!$("#cmdkOverlay").hidden) { $("#cmdkOverlay").hidden = true; return; }
  if (!$("#acDropdown").hidden) { $("#acDropdown").hidden = true; return; }
  if (!$("#scheduleMenu").hidden) { $("#scheduleMenu").hidden = true; return; }
  if (!$("#snoozeMenu").hidden) { $("#snoozeMenu").hidden = true; return; }
  if (!$("#relabelMenu").hidden) { $("#relabelMenu").hidden = true; return; }
  if (helpO && !helpO.hidden) { helpO.hidden = true; return; }
  if (!$("#settingsOverlay").hidden) { $("#settingsOverlay").hidden = true; return; }
  // Escape does NOT close compose (only its sub-popups, handled above). Compose closes
  // only via an explicit exit, so an accidental Escape can't drop a message in progress.
  if (!$("#reader").hidden) { closeReader(); return; }
});
async function doComposeSend(sendAt) {
  clearTimeout(draftSaveTimer); // cancel any pending autosave so it can't fire mid/after-send
  const wasDraft = !!STATE.composeDraftId;
  const fd = new FormData();
  fd.append("to", $("#cTo").value);
  if ($("#cCc").value.trim()) fd.append("cc", $("#cCc").value.trim());
  if ($("#cBcc").value.trim()) fd.append("bcc", $("#cBcc").value.trim());
  fd.append("subject", $("#cSubject").value);
  fd.append("html", $("#cBody").innerHTML);
  fd.append("text", $("#cBody").innerText);
  if (STATE.composeDraftId) fd.append("draftId", STATE.composeDraftId);
  const fromAlias = $("#cFrom") && $("#cFrom").value;
  if (fromAlias) fd.append("viaAlias", fromAlias);
  if (sendAt) fd.append("sendAt", String(sendAt));
  (STATE.attachments || []).forEach((f) => fd.append("attachments", f, f.name));
  const btn = $("#composeSend"); btn.disabled = true;
  // Pull down any carried-forward attachments and re-attach them as real files.
  for (const a of (STATE.fwdAttachments || []).filter((x) => x.keep && !x.drive)) {
    try {
      const resp = await fetch(`/api/attachment/${a.messageId}/${a.attachmentId}?name=${encodeURIComponent(a.filename)}&mime=${encodeURIComponent(a.mimeType)}`);
      fd.append("attachments", new File([await resp.blob()], a.filename, { type: a.mimeType }));
    } catch (e) { /* skip an attachment that won't fetch */ }
  }
  let res;
  try { res = await (await fetch("/api/send", { method: "POST", body: fd })).json(); }
  catch (e) { res = { error: String(e) }; }
  btn.disabled = false;
  // Clear BEFORE close so closeCompose's final-save sees empty fields (the autosaved
  // draft is removed server-side as part of the send, via the draftId we passed).
  if (handleSendResult(res)) { clearCompose(); closeCompose(); if (wasDraft && STATE.view === "drafts") load(); }
}
$("#composeSend").onclick = () => doComposeSend(0);
$("#cCcToggle").onclick = () => { $("#cCc").hidden = false; $("#cCcToggle").style.display = "none"; $("#cCc").focus(); };
$("#cBccToggle").onclick = () => { $("#cBcc").hidden = false; $("#cBccToggle").style.display = "none"; $("#cBcc").focus(); };

// ---------- Scheduled send ----------
function resolveScheduleEpoch(preset) {
  const now = new Date(), t = new Date(now);
  if (preset === "later_today") t.setHours(now.getHours() + 3, 0, 0, 0);
  else if (preset === "tomorrow") { t.setDate(now.getDate() + 1); t.setHours(8, 0, 0, 0); }
  else if (preset === "next_week") { const add = ((8 - now.getDay()) % 7) || 7; t.setDate(now.getDate() + add); t.setHours(8, 0, 0, 0); }
  return Math.floor(t.getTime() / 1000);
}
$("#composeSchedule").onclick = (e) => {
  e.stopPropagation();
  const m = $("#scheduleMenu"), r = e.currentTarget.getBoundingClientRect();
  m.hidden = false;
  m.style.top = Math.max(8, r.top - m.offsetHeight - 6) + "px";
  m.style.left = Math.min(r.left, window.innerWidth - 250) + "px";
};
$("#scheduleMenu").querySelectorAll("a").forEach((a) => {
  a.onclick = () => {
    const s = a.dataset.sched; $("#scheduleMenu").hidden = true;
    if (s === "pick") {
      const dt = $("#scheduleDT");
      dt.onchange = () => { const ep = Math.floor(new Date(dt.value).getTime() / 1000); if (ep) doComposeSend(ep); };
      dt.showPicker ? dt.showPicker() : dt.focus();
      return;
    }
    doComposeSend(resolveScheduleEpoch(s));
  };
});
document.addEventListener("click", (e) => {
  if (!e.target.closest("#scheduleMenu") && !e.target.closest("#composeSchedule")) $("#scheduleMenu").hidden = true;
});

// ---------- Paste sanitizer (compose, reply, signature) ----------
function stripDangerousHtml(html) {
  const d = document.createElement("div");
  d.innerHTML = html;
  d.querySelectorAll("script,style,meta,link,head,object,embed,iframe").forEach((n) => n.remove());
  d.querySelectorAll("img[src]").forEach((img) => { if (!(img.getAttribute("src") || "").startsWith("data:")) img.remove(); });
  d.querySelectorAll("*").forEach((el) => [...el.attributes].forEach((at) => { if (/^on/i.test(at.name)) el.removeAttribute(at.name); }));
  return d.innerHTML;
}
// Cmd/Ctrl+Shift+V = paste without formatting. The browser would do this natively,
// but our paste handler intercepts every paste and prefers text/html — so we must
// honor the intent ourselves. Set on keydown (fires before the paste event), consume
// it in the handler, with a timeout as a safety reset if no paste follows.
let forcePlainPaste = false;
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.shiftKey && (e.key === "v" || e.key === "V")) {
    forcePlainPaste = true;
    setTimeout(() => { forcePlainPaste = false; }, 300);
  }
});
["cBody", "replyBox", "setSignature"].forEach((id) => {
  const el = document.getElementById(id);
  if (!el) return;
  el.addEventListener("paste", (e) => {
    e.preventDefault();
    const html = e.clipboardData.getData("text/html");
    const text = e.clipboardData.getData("text/plain");
    if (!forcePlainPaste && html) document.execCommand("insertHTML", false, stripDangerousHtml(html));
    else document.execCommand("insertText", false, text);
    forcePlainPaste = false;
  });
});

// ---------- Drag-to-attach (Finder → compose) ----------
(function wireDrop() {
  const co = $("#composeOverlay");
  const show = (on) => $("#cDropHint").classList.toggle("show", on);
  ["dragenter", "dragover"].forEach((ev) => co.addEventListener(ev, (e) => { if (co.hidden) return; e.preventDefault(); show(true); }));
  co.addEventListener("dragleave", (e) => { if (e.relatedTarget && co.contains(e.relatedTarget)) return; show(false); });
  co.addEventListener("drop", (e) => {
    if (co.hidden) return;
    e.preventDefault(); show(false);
    const files = [...((e.dataTransfer && e.dataTransfer.files) || [])];
    if (files.length) { STATE.attachments = (STATE.attachments || []).concat(files); renderAttachChips(); }
  });
})();

// ---------- Contact autocomplete (To / Cc / Bcc) ----------
let acTimer = null;
async function runAutocomplete(input) {
  const token = (input.value.split(",").pop() || "").trim();
  const dd = $("#acDropdown");
  if (token.length < 2) { dd.hidden = true; return; }
  let r;
  try { r = await api("/api/contacts?q=" + encodeURIComponent(token)); } catch (e) { return; }
  const list = (r && r.contacts) || [];
  if (!list.length) { dd.hidden = true; return; }
  dd.innerHTML = "";
  list.forEach((c) => {
    const item = document.createElement("div");
    item.className = "ac-item";
    item.innerHTML = `<span class="ac-name">${esc(c.name || c.email)}</span><span class="ac-email">${esc(c.email)}</span>`;
    item.onmousedown = (e) => {
      e.preventDefault();
      const parts = input.value.split(",");
      parts[parts.length - 1] = c.email;
      input.value = parts.join(", ").replace(/^\s+/, "") + ", ";
      dd.hidden = true; input.focus();
    };
    dd.appendChild(item);
  });
  const rect = input.getBoundingClientRect();
  dd.style.left = rect.left + "px"; dd.style.top = (rect.bottom + 2) + "px"; dd.style.width = rect.width + "px";
  dd.hidden = false;
}
["#cTo", "#cCc", "#cBcc"].forEach((s) => {
  const el = $(s);
  el.addEventListener("input", () => { clearTimeout(acTimer); acTimer = setTimeout(() => runAutocomplete(el), 150); });
  el.addEventListener("blur", () => setTimeout(() => ($("#acDropdown").hidden = true), 150));
});
$("#composeDiscard").onclick = async () => {
  if (!STATE.composeDraftId) return;
  clearTimeout(draftSaveTimer);                 // stop autosave from resurrecting it
  await post("/api/discard_draft", { draftId: STATE.composeDraftId });
  clearCompose(); closeCompose(); toast("Draft discarded");
  if (STATE.view === "drafts") load();
};
async function openDraft(draftId) {
  const r = await api("/api/draft/" + draftId);
  if (r.error) { toast("Couldn't open draft"); return; }
  openCompose({ draftId, to: r.to, subject: r.subject, body: r.body, html: r.bodyHtml });
}
// Rich-text toolbars (compose + reply). execCommand is deprecated but works
// everywhere and acts on whichever contenteditable currently holds the selection.
document.querySelectorAll(".compose-toolbar [data-cmd]").forEach((b) => {
  b.addEventListener("mousedown", (e) => { e.preventDefault(); document.execCommand(b.dataset.cmd, false, null); });
});
document.querySelectorAll(".tb-link").forEach((b) => {
  b.addEventListener("mousedown", (e) => {
    e.preventDefault();
    const raw = prompt("Link URL:");
    if (!raw) return;
    const url = /^[a-z]+:\/\//i.test(raw) ? raw : "https://" + raw;
    const sel = window.getSelection();
    if (sel && sel.toString()) document.execCommand("createLink", false, url);
    else document.execCommand("insertHTML", false, `<a href="${url}">${esc(url)}</a>`);
  });
});
// Attachment chips, keyed by STATE field + list container
function renderChips(key, sel) {
  const wrap = $(sel);
  if (!wrap) return;
  wrap.innerHTML = "";
  (STATE[key] || []).forEach((f, i) => {
    const chip = document.createElement("div");
    chip.className = "attach-chip";
    chip.innerHTML = `<i class="material-icons" style="font-size:16px;color:#5f6368">attach_file</i><span>${esc(f.name)}</span><button title="Remove"><i class="material-icons">close</i></button>`;
    chip.querySelector("button").onclick = () => { STATE[key].splice(i, 1); renderChips(key, sel); };
    wrap.appendChild(chip);
  });
}
function renderAttachChips() { renderChips("attachments", "#cAttachments"); }
function wireAttach(btnId, inputId, key, listSel) {
  $(btnId).onclick = () => $(inputId).click();
  $(inputId).onchange = () => {
    STATE[key] = (STATE[key] || []).concat([...$(inputId).files]);
    $(inputId).value = "";
    renderChips(key, listSel);
  };
}
wireAttach("#cAttach", "#cFiles", "attachments", "#cAttachments");
wireAttach("#rAttach", "#rFiles", "replyAttachments", "#rAttachments");
async function discardDraft(draftId) {
  await post("/api/discard_draft", { draftId });
  toast("Draft discarded"); load();
}

// ---------- Nav ----------
document.querySelectorAll(".nav-item").forEach((n) => {
  n.onclick = () => {
    if (n.dataset.panel === "aliases") { openAliases(); return; }
    if (n.dataset.panel === "templates") { openTemplates(); return; }
    document.querySelectorAll(".nav-item").forEach((x) => x.classList.remove("active"));
    n.classList.add("active");
    STATE.view = n.dataset.view;
    STATE.token = null;
    clearSearchBox(); // leaving a search → empty the box
    load();
  };
});
$("#refreshBtn").onclick = load;
$("#pinnedOnly").onchange = render;
$("#menuBtn").onclick = () => document.querySelector(".layout").classList.toggle("nav-collapsed");
// ---------- Search (Gmail-style: suggestions, operators, advanced builder) ----------
const SEARCH_OPS = [
  { op: "from:", desc: "Sender", icon: "person" },
  { op: "to:", desc: "Recipient", icon: "person_outline" },
  { op: "subject:", desc: "Words in the subject", icon: "subject" },
  { op: "has:attachment", desc: "Has any attachment", icon: "attach_file" },
  { op: "filename:", desc: "Attachment name or type (e.g. pdf)", icon: "description" },
  { op: "is:unread", desc: "Unread mail", icon: "markunread" },
  { op: "is:read", desc: "Read mail", icon: "drafts" },
  { op: "is:starred", desc: "Starred / pinned", icon: "star" },
  { op: "is:important", desc: "Marked important", icon: "label_important" },
  { op: "in:inbox", desc: "In the inbox", icon: "inbox" },
  { op: "in:sent", desc: "Sent mail", icon: "send" },
  { op: "in:trash", desc: "In trash", icon: "delete" },
  { op: "in:anywhere", desc: "Everywhere, incl. spam & trash", icon: "all_inbox" },
  { op: "label:", desc: "Has a label", icon: "label" },
  { op: "category:", desc: "primary / social / promotions / updates / forums", icon: "category" },
  { op: "after:", desc: "After a date — YYYY/MM/DD", icon: "event" },
  { op: "before:", desc: "Before a date — YYYY/MM/DD", icon: "event" },
  { op: "newer_than:", desc: "Within a span — e.g. 7d, 1m, 1y", icon: "schedule" },
  { op: "older_than:", desc: "Older than a span — e.g. 7d, 1m, 1y", icon: "history" },
  { op: "larger:", desc: "Larger than a size — e.g. 5M", icon: "data_usage" },
  { op: "smaller:", desc: "Smaller than a size — e.g. 1M", icon: "data_usage" },
  { op: '"exact phrase"', desc: "Match an exact phrase", icon: "format_quote" },
  { op: "-", desc: "Exclude a term", icon: "remove_circle_outline" },
  { op: "OR", desc: "Match either term", icon: "call_split" },
];
const RECENT_KEY = "inbox.recentSearches";
function recentSearches() { try { return JSON.parse(localStorage.getItem(RECENT_KEY)) || []; } catch (e) { return []; } }
function pushRecent(q) { q = q.trim(); if (!q) return; let r = recentSearches().filter((x) => x !== q); r.unshift(q); try { localStorage.setItem(RECENT_KEY, JSON.stringify(r.slice(0, 12))); } catch (e) {} }
function removeRecent(q) { try { localStorage.setItem(RECENT_KEY, JSON.stringify(recentSearches().filter((x) => x !== q))); } catch (e) {} }
function clearSearchBox() { $("#searchInput").value = ""; $("#searchClear").hidden = true; $("#searchSuggest").hidden = true; $("#searchAdvanced").hidden = true; }

let searchSel = -1, searchItems = [], searchContactTimer = null;

function runSearch(q) {
  q = (q || "").trim();
  $("#searchSuggest").hidden = true; $("#searchAdvanced").hidden = true;
  $("#searchInput").value = q; $("#searchClear").hidden = !q;
  document.querySelectorAll(".nav-item").forEach((x) => x.classList.remove("active"));
  if (q) { STATE.view = "search"; STATE.query = q; pushRecent(q); }
  else { STATE.view = "inbox"; document.querySelector('.nav-item[data-view="inbox"]').classList.add("active"); }
  STATE.token = null;
  load();
}
// The whitespace-delimited token under the cursor (so operators complete in place).
function currentToken() {
  const inp = $("#searchInput");
  const v = inp.value.slice(0, inp.selectionStart ?? inp.value.length);
  const m = v.match(/(\S+)$/);
  return m ? m[1] : "";
}
function replaceToken(insert) {
  const inp = $("#searchInput");
  const pos = inp.selectionStart ?? inp.value.length;
  const before = inp.value.slice(0, pos).replace(/\S+$/, "") + insert;
  inp.value = before + inp.value.slice(pos);
  inp.setSelectionRange(before.length, before.length);
  inp.focus();
  $("#searchClear").hidden = !inp.value;
}
function buildSuggestions() {
  const inp = $("#searchInput");
  const full = inp.value.trim();
  const tok = currentToken(), tl = tok.toLowerCase();
  const items = [];
  if (full) items.push({ type: "run", icon: "search", main: full, value: full });
  if (tok) {
    SEARCH_OPS.filter((o) => o.op.toLowerCase().startsWith(tl) && o.op.toLowerCase() !== tl)
      .slice(0, 6).forEach((o) => items.push({ type: "op", icon: o.icon, op: o.op, desc: o.desc, value: o.op }));
  } else if (!full) {
    ["from:", "to:", "subject:", "has:attachment", "is:unread", "after:"].forEach((op) => {
      const o = SEARCH_OPS.find((x) => x.op === op); if (o) items.push({ type: "op", icon: o.icon, op: o.op, desc: o.desc, value: o.op });
    });
  }
  recentSearches().filter((r) => !full || (r.toLowerCase().includes(full.toLowerCase()) && r.toLowerCase() !== full.toLowerCase()))
    .slice(0, 5).forEach((r) => items.push({ type: "recent", icon: "history", main: r, value: r }));
  renderSuggestions(items);
  // Contacts — when typing a bare name fragment or after from:/to:
  const mFT = tok.match(/^(from:|to:)(.*)$/i);
  const cq = mFT ? mFT[2] : (/^[a-z][a-z.@-]*$/i.test(tok) && tok.length >= 2 ? tok : "");
  if (cq && cq.length >= 2) {
    clearTimeout(searchContactTimer);
    searchContactTimer = setTimeout(async () => {
      let cs; try { cs = await api("/api/contacts?q=" + encodeURIComponent(cq)); } catch (e) { return; }
      if (!Array.isArray(cs) || !cs.length || inp.value.trim() !== full) return;
      const prefix = mFT ? mFT[1].toLowerCase() : "from:";
      const citems = cs.slice(0, 5).map((c) => ({ type: "contact", icon: "person", main: c.name || c.email, sub: c.email, value: prefix + c.email + " " }));
      renderSuggestions(items.concat(citems));
    }, 160);
  }
}
function renderSuggestions(items) {
  searchItems = items; searchSel = -1;
  const pop = $("#searchSuggest");
  if (!items.length) { pop.hidden = true; return; }
  pop.innerHTML = "";
  const headFor = (t) => t === "recent" ? "Recent searches" : t === "contact" ? "Contacts" : t === "op" ? "Search operators" : null;
  let lastHead = null;
  items.forEach((it, i) => {
    const head = headFor(it.type);
    if (head && head !== lastHead) { const h = document.createElement("div"); h.className = "ss-head"; h.textContent = head; pop.appendChild(h); }
    lastHead = head;
    const el = document.createElement("div");
    el.className = "ss-item"; el.dataset.i = i;
    if (it.type === "op") el.innerHTML = `<i class="material-icons">${it.icon}</i><span class="ss-op">${esc(it.op)}</span><span class="ss-main">${esc(it.desc)}</span>`;
    else if (it.type === "contact") el.innerHTML = `<i class="material-icons">${it.icon}</i><span class="ss-main">${esc(it.main)}</span><span class="ss-sub">${esc(it.sub || "")}</span>`;
    else {
      el.innerHTML = `<i class="material-icons">${it.icon}</i><span class="ss-main">${esc(it.main)}</span>`;
      if (it.type === "recent") { const x = document.createElement("i"); x.className = "material-icons ss-remove"; x.textContent = "close"; x.title = "Remove"; x.onmousedown = (e) => { e.preventDefault(); e.stopPropagation(); removeRecent(it.value); buildSuggestions(); }; el.appendChild(x); }
    }
    el.onmousedown = (e) => { e.preventDefault(); selectSuggestion(i); };
    pop.appendChild(el);
  });
  pop.hidden = false;
}
function selectSuggestion(i) {
  const it = searchItems[i]; if (!it) return;
  if (it.type === "op" || it.type === "contact") { replaceToken(it.value); buildSuggestions(); }
  else runSearch(it.value);
}
function moveSearchSel(d) {
  const n = searchItems.length; if (!n) return;
  searchSel = (searchSel + d + n) % n;
  $("#searchSuggest").querySelectorAll(".ss-item").forEach((el) => el.classList.toggle("sel", +el.dataset.i === searchSel));
}
$("#searchInput").addEventListener("input", () => { $("#searchClear").hidden = !$("#searchInput").value; buildSuggestions(); });
$("#searchInput").addEventListener("focus", () => { $("#searchAdvanced").hidden = true; buildSuggestions(); });
$("#searchInput").addEventListener("blur", () => setTimeout(() => { $("#searchSuggest").hidden = true; }, 150));
$("#searchInput").addEventListener("keydown", (e) => {
  const pop = $("#searchSuggest");
  if ((e.key === "ArrowDown" || e.key === "ArrowUp") && !pop.hidden) { e.preventDefault(); moveSearchSel(e.key === "ArrowDown" ? 1 : -1); }
  else if (e.key === "Enter") { e.preventDefault(); (!pop.hidden && searchSel >= 0) ? selectSuggestion(searchSel) : runSearch(e.target.value); }
  else if (e.key === "Escape") { if (!pop.hidden) pop.hidden = true; else if (!$("#searchAdvanced").hidden) $("#searchAdvanced").hidden = true; else e.target.blur(); }
  else if (e.key === "Tab" && !pop.hidden) { const opi = searchItems.findIndex((x) => x.type === "op"); if (opi >= 0) { e.preventDefault(); selectSuggestion(opi); } }
});
$("#searchGo").onclick = () => runSearch($("#searchInput").value);
$("#searchClear").onclick = () => { clearSearchBox(); if (STATE.view === "search") runSearch(""); $("#searchInput").focus(); };
$("#searchAdvToggle").onclick = (e) => { e.stopPropagation(); $("#searchSuggest").hidden = true; const a = $("#searchAdvanced"); a.hidden = !a.hidden; };
// Build a Gmail query from the advanced form and run it.
function searchDateWindow(dateStr, days) {
  const d = new Date(dateStr + "T00:00:00"), ms = +days * 86400000;
  const fmt = (t) => { const x = new Date(t); return x.getFullYear() + "/" + (x.getMonth() + 1) + "/" + x.getDate(); };
  return { after: fmt(d.getTime() - ms), before: fmt(d.getTime() + ms) };
}
function buildAdvQuery() {
  const v = (id) => $("#" + id).value.trim();
  const grp = (s) => /\s/.test(s) ? "(" + s + ")" : s;
  const p = [];
  if (v("saFrom")) p.push("from:" + grp(v("saFrom")));
  if (v("saTo")) p.push("to:" + grp(v("saTo")));
  if (v("saSubject")) p.push("subject:" + grp(v("saSubject")));
  if (v("saHas")) p.push(v("saHas"));
  if (v("saNot")) v("saNot").split(/\s+/).forEach((w) => p.push("-" + w));
  if ($("#saHasAttach").checked) p.push("has:attachment");
  if (v("saSizeVal")) p.push($("#saSizeOp").value + ":" + v("saSizeVal") + $("#saSizeUnit").value);
  if ($("#saWithin").value && v("saDate")) { const w = searchDateWindow(v("saDate"), $("#saWithin").value); p.push("after:" + w.after, "before:" + w.before); }
  if ($("#saScope").value) p.push($("#saScope").value);
  return p.join(" ");
}
$("#saSearch").onclick = () => { const q = buildAdvQuery(); $("#searchAdvanced").hidden = true; if (q) runSearch(q); };
$("#saClear").onclick = () => {
  ["saFrom", "saTo", "saSubject", "saHas", "saNot", "saSizeVal", "saDate"].forEach((id) => ($("#" + id).value = ""));
  $("#saHasAttach").checked = false; $("#saWithin").value = ""; $("#saScope").value = ""; $("#saSizeOp").value = "larger"; $("#saSizeUnit").value = "M";
};
// Close search dropdowns on an outside click.
document.addEventListener("click", (e) => { if (!e.target.closest("#searchWrap")) { $("#searchSuggest").hidden = true; $("#searchAdvanced").hidden = true; } });

// ---------- Settings panel ----------
function openSettings() {
  const s = STATE.settings || {};
  $("#setSignature").innerHTML = s.signature || "";
  $("#setUndoWindow").value = String(s.undo_send_window ?? 10);
  $("#setDarkMode").value = s.dark_mode || "auto";
  $("#setImageBlock").checked = s.image_block ?? false;
  $("#setFollowupDays").value = String(s.followup_default_days ?? 3);
  $("#setTemplatesEnabled").checked = s.templates_enabled ?? true;
  $("#settingsOverlay").hidden = false;
}
$("#settingsBtn").onclick = openSettings;
$("#settingsClose").onclick = () => ($("#settingsOverlay").hidden = true);
$("#settingsOverlay").addEventListener("click", (e) => { if (e.target.id === "settingsOverlay") $("#settingsOverlay").hidden = true; });
$("#settingsSave").onclick = async () => {
  const upd = {
    signature: $("#setSignature").innerHTML,
    undo_send_window: +$("#setUndoWindow").value,
    dark_mode: $("#setDarkMode").value,
    image_block: $("#setImageBlock").checked,
    followup_default_days: +$("#setFollowupDays").value,
    templates_enabled: $("#setTemplatesEnabled").checked,
  };
  STATE.settings = Object.assign(STATE.settings || {}, upd);
  applySettings(STATE.settings);
  await post("/api/settings", upd);
  $("#settingsOverlay").hidden = true;
  toast("Settings saved");
};
$("#setOpenTemplates").onclick = () => { $("#settingsOverlay").hidden = true; openTemplates(); };

// ---------- Aliases (addy.io disposable email) ----------
async function openAliases() {
  $("#aliasOverlay").hidden = false;
  await loadAliases();
}
async function loadAliases() {
  const list = $("#aliasList");
  list.innerHTML = '<div class="alias-empty">Loading…</div>';
  const d = await api("/api/aliases");
  if (!d || d.configured === false) {
    $("#aliasQuota").textContent = "";
    list.innerHTML = '<div class="alias-empty">addy.io isn\'t configured. Add your API key to '
      + '<code>disposable-email/secrets.json</code>.</div>';
    return;
  }
  const q = d.quota || {};
  if (q.limit) {
    const full = q.active >= q.limit;
    $("#aliasQuota").innerHTML = `<span class="${full ? "quota-full" : ""}">${q.active}/${q.limit} active</span>`
      + (q.plan ? ` · ${esc(q.plan)}` : "");
    $("#aliasCreate").disabled = full;
    $("#aliasCreate").title = full ? "At the free-plan limit — deactivate or delete one first" : "";
  } else {
    $("#aliasQuota").textContent = "";
  }
  const rows = d.aliases || [];
  if (!rows.length) {
    list.innerHTML = '<div class="alias-empty">No aliases yet. Mint one above — it forwards into this inbox.</div>';
    return;
  }
  list.innerHTML = "";
  rows.forEach((a) => list.appendChild(aliasEl(a)));
}
function aliasEl(a) {
  const el = document.createElement("div");
  el.className = "alias-row" + (a.active ? "" : " inactive");
  const stats = [];
  if (a.forwarded) stats.push(`${a.forwarded} fwd`);
  if (a.replied) stats.push(`${a.replied} repl`);
  if (a.blocked) stats.push(`${a.blocked} blocked`);
  el.innerHTML = `
    <div class="alias-main">
      <div class="alias-addr">${esc(a.email)}
        <button class="alias-copy icon-btn" title="Copy address"><i class="material-icons">content_copy</i></button>
      </div>
      <div class="alias-meta">${a.description ? esc(a.description) + " · " : ""}${esc(a.created)}${stats.length ? " · " + stats.join(" · ") : ""}${a.active ? "" : " · <span class=\"alias-off\">deactivated</span>"}</div>
    </div>
    <div class="alias-actions">
      <button class="alias-toggle" title="${a.active ? "Deactivate (bounce mail, free a slot)" : "Reactivate"}">
        <i class="material-icons">${a.active ? "toggle_on" : "toggle_off"}</i></button>
      <button class="alias-del" title="Delete permanently (forget)"><i class="material-icons">delete_outline</i></button>
    </div>`;
  el.querySelector(".alias-copy").onclick = () => {
    navigator.clipboard.writeText(a.email); toast("Copied " + a.email);
  };
  el.querySelector(".alias-toggle").onclick = async () => {
    const r = await post("/api/alias_toggle", { id: a.id, active: !a.active });
    if (r.ok) loadAliases(); else toast(r.error || "Failed");
  };
  el.querySelector(".alias-del").onclick = async () => {
    if (!confirm(`Delete ${a.email} permanently? This frees the slot and can't be undone.`)) return;
    const r = await post("/api/alias_delete", { id: a.id, forget: true });
    if (r.ok) { toast("Alias deleted"); loadAliases(); } else toast(r.error || "Failed");
  };
  return el;
}
$("#aliasClose").onclick = () => ($("#aliasOverlay").hidden = true);
$("#aliasOverlay").addEventListener("click", (e) => { if (e.target.id === "aliasOverlay") $("#aliasOverlay").hidden = true; });
$("#aliasCreate").onclick = async () => {
  const desc = $("#aliasDesc").value.trim();
  $("#aliasCreate").disabled = true;
  const r = await post("/api/aliases", { description: desc });
  $("#aliasCreate").disabled = false;
  if (r.ok) {
    $("#aliasDesc").value = "";
    if (r.alias && r.alias.email) { navigator.clipboard.writeText(r.alias.email); toast("Minted + copied " + r.alias.email); }
    loadAliases();
  } else { toast(r.error || "Create failed"); }
};
$("#aliasDesc").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#aliasCreate").click(); });

// ---------- Templates + auto-reply ----------
// Date<->epoch helpers. A "date" means the whole local day: expirations and the
// auto-reply "last day" run through 23:59:59 so the chosen day is inclusive.
function dateInputToEpoch(str, endOfDay) {
  if (!str) return null;
  const [y, m, d] = str.split("-").map(Number);
  const dt = endOfDay ? new Date(y, m - 1, d, 23, 59, 59) : new Date(y, m - 1, d, 0, 0, 0);
  return Math.floor(dt.getTime() / 1000);
}
function epochToDateInput(sec) {
  if (!sec) return "";
  const dt = new Date(sec * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${dt.getFullYear()}-${p(dt.getMonth() + 1)}-${p(dt.getDate())}`;
}
function fmtExpiry(sec) {
  return new Date(sec * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

STATE.templates = [];

async function openTemplates() {
  $("#tmplOverlay").hidden = false;
  $("#tmplEditor").hidden = true;
  await loadTemplates();
  await loadAutoReply();
}
async function loadTemplates() {
  const d = await api("/api/templates");
  STATE.templates = (d && d.templates) || [];
  renderTemplateList();
  renderAutoReplyTemplateOptions();
}
function renderTemplateList() {
  const list = $("#tmplList");
  if (!STATE.templates.length) {
    list.innerHTML = '<div class="tmpl-empty">No templates yet. Create one to reuse it in compose or as an auto-reply.</div>';
    return;
  }
  list.innerHTML = "";
  STATE.templates.forEach((t) => list.appendChild(templateRowEl(t)));
}
function templateRowEl(t) {
  const el = document.createElement("div");
  el.className = "tmpl-row" + (t.expired ? " expired" : "");
  const expChip = t.expires_at
    ? `<span class="tmpl-chip${t.expired ? " dead" : ""}"><i class="material-icons">schedule</i>${t.expired ? "Expired " : "Expires "}${fmtExpiry(t.expires_at)}</span>`
    : "";
  el.innerHTML = `
    <div class="tmpl-main">
      <div class="tmpl-name">${esc(t.name)} ${expChip}</div>
      <div class="tmpl-sub">${esc(t.subject || "(no subject)")}</div>
    </div>
    <div class="tmpl-actions">
      <button class="tmpl-edit icon-btn" title="Edit"><i class="material-icons">edit</i></button>
      <button class="tmpl-del icon-btn" title="Delete"><i class="material-icons">delete_outline</i></button>
    </div>`;
  el.querySelector(".tmpl-edit").onclick = () => openTemplateEditor(t);
  el.querySelector(".tmpl-del").onclick = async () => {
    if (!confirm(`Delete template "${t.name}"?`)) return;
    const r = await post("/api/template_delete", { id: t.id });
    if (r.ok) { toast("Template deleted"); loadTemplates(); } else toast(r.error || "Failed");
  };
  return el;
}
function openTemplateEditor(t) {
  t = t || {};
  $("#teId").value = t.id || "";
  $("#teName").value = t.name || "";
  $("#teSubject").value = t.subject || "";
  $("#teBody").innerHTML = t.body_html || "";
  $("#teExpires").value = epochToDateInput(t.expires_at);
  $("#tmplEditor").hidden = false;
  $("#tmplEditor").scrollIntoView({ behavior: "smooth", block: "nearest" });
  $("#teName").focus();
}
$("#tmplNew").onclick = () => openTemplateEditor({});
$("#teCancel").onclick = () => ($("#tmplEditor").hidden = true);
$("#teSave").onclick = async () => {
  const name = $("#teName").value.trim();
  if (!name) { toast("Name is required"); return; }
  const payload = {
    id: $("#teId").value || undefined,
    name,
    subject: $("#teSubject").value,
    body_html: $("#teBody").innerHTML,
    expires_at: dateInputToEpoch($("#teExpires").value, true),
  };
  const r = await post("/api/templates", payload);
  if (r.ok) { toast("Template saved"); $("#tmplEditor").hidden = true; loadTemplates(); }
  else toast(r.error || "Save failed");
};
$("#tmplClose").onclick = () => ($("#tmplOverlay").hidden = true);
$("#tmplOverlay").addEventListener("click", (e) => { if (e.target.id === "tmplOverlay") $("#tmplOverlay").hidden = true; });

// --- Auto-reply (vacation responder) ---
function renderAutoReplyTemplateOptions() {
  const sel = $("#arTemplate");
  const cur = sel.value;
  sel.innerHTML = '<option value="">Custom (write below)</option>';
  STATE.templates.filter((t) => !t.expired).forEach((t) => {
    const o = document.createElement("option");
    o.value = t.id; o.textContent = t.name;
    sel.appendChild(o);
  });
  sel.value = cur;
}
function applyAutoReplyTemplateMode() {
  const usingTemplate = !!$("#arTemplate").value;
  // When a template drives the reply, subject/body come from it — lock the fields.
  $("#arSubjectField").style.display = usingTemplate ? "none" : "";
  $("#arBodyField").style.display = usingTemplate ? "none" : "";
  const t = STATE.templates.find((x) => x.id === $("#arTemplate").value);
  const hint = $("#arExpiryHint");
  if (t && t.expires_at) {
    hint.hidden = false;
    hint.textContent = `This template expires ${fmtExpiry(t.expires_at)} — the auto-reply will stop then, even if no last day is set.`;
  } else { hint.hidden = true; }
}
$("#arTemplate").onchange = applyAutoReplyTemplateMode;
async function loadAutoReply() {
  let v;
  try { v = await api("/api/vacation"); } catch (e) { v = null; }
  if (!v || v.ok === false) {
    $("#arGrid").classList.add("disabled-block");
    toast("Couldn't load auto-reply settings");
    return;
  }
  $("#arGrid").classList.remove("disabled-block");
  $("#arEnabled").checked = v.enabled;
  $("#arSubject").value = v.subject || "";
  $("#arBody").innerHTML = v.body_html || "";
  $("#arStart").value = epochToDateInput(v.start);
  $("#arEnd").value = epochToDateInput(v.end);
  $("#arRestrict").checked = v.restrict_contacts;
  $("#arTemplate").value = v.template_id || "";
  applyAutoReplyTemplateMode();
}
$("#arSave").onclick = async () => {
  const payload = {
    enabled: $("#arEnabled").checked,
    template_id: $("#arTemplate").value,
    subject: $("#arSubject").value,
    body_html: $("#arBody").innerHTML,
    start: dateInputToEpoch($("#arStart").value, false),
    end: dateInputToEpoch($("#arEnd").value, true),
    restrict_contacts: $("#arRestrict").checked,
  };
  $("#arSave").disabled = true;
  const r = await post("/api/vacation", payload);
  $("#arSave").disabled = false;
  if (r.ok) {
    toast($("#arEnabled").checked ? "Auto-reply on" : "Auto-reply off");
    if (r.end) { $("#arEnd").value = epochToDateInput(r.end); }
  } else toast(r.error || "Failed to save");
};

// --- Insert template into compose ---
$("#cTemplate").onclick = async (e) => {
  e.preventDefault();
  const menu = $("#cTemplateMenu");
  if (!menu.hidden) { menu.hidden = true; return; }
  const d = await api("/api/templates?active=1");
  const items = (d && d.templates) || [];
  if (!items.length) {
    menu.innerHTML = '<div class="tmpl-menu-empty">No templates yet. Add some in Templates.</div>';
  } else {
    menu.innerHTML = items.map((t) =>
      `<a data-tid="${t.id}"><i class="material-icons">article</i>${esc(t.name)}</a>`).join("");
  }
  menu.hidden = false;
};
$("#cTemplateMenu").addEventListener("click", (e) => {
  const a = e.target.closest("a[data-tid]");
  if (!a) return;
  const t = STATE.templates.find((x) => x.id === a.dataset.tid)
    || null;
  $("#cTemplateMenu").hidden = true;
  // STATE.templates may be stale (panel never opened) — fetch the one we need.
  insertTemplateIntoCompose(a.dataset.tid, t);
});
async function insertTemplateIntoCompose(tid, cached) {
  let t = cached;
  if (!t) {
    const d = await api("/api/templates?active=1");
    t = ((d && d.templates) || []).find((x) => x.id === tid);
  }
  if (!t) { toast("Template unavailable"); return; }
  if (t.subject && !$("#cSubject").value.trim()) $("#cSubject").value = t.subject;
  const body = $("#cBody");
  body.focus();
  if (!document.execCommand("insertHTML", false, t.body_html)) {
    body.innerHTML = t.body_html + body.innerHTML;
  }
  draftDirty = true;
}
document.addEventListener("click", (e) => {
  if (!e.target.closest("#cTemplate") && !e.target.closest("#cTemplateMenu"))
    $("#cTemplateMenu").hidden = true;
}, true);

// ---------- Keyboard help ----------
const HELP_ROWS = [
  ["j / k", "Move down / up"], ["Enter or o", "Open conversation"],
  ["e", "Done (archive)"], ["p", "Pin"], ["s", "Snooze"], ["r", "Reply / open"],
  ["c", "Compose"], ["/", "Search"], ["⌘K / Ctrl+K", "Command palette"],
  ["?", "This help"], ["Esc", "Close / back"],
].map((r) => `<div class="help-row"><kbd>${esc(r[0])}</kbd><span>${esc(r[1])}</span></div>`).join("");
function openHelp() {
  let o = $("#helpOverlay");
  if (!o) {
    o = document.createElement("div");
    o.id = "helpOverlay"; o.className = "modal-overlay center";
    o.innerHTML = `<div class="help-card"><div class="compose-head">Keyboard shortcuts <button class="icon-btn" id="helpClose"><i class="material-icons">close</i></button></div><div class="help-body">${HELP_ROWS}</div></div>`;
    document.body.appendChild(o);
    o.addEventListener("click", (e) => { if (e.target.id === "helpOverlay" || e.target.closest("#helpClose")) o.hidden = true; });
  }
  o.hidden = false;
}
$("#helpBtn").onclick = openHelp;

// ---------- Command palette ----------
function gotoView(v) {
  document.querySelectorAll(".nav-item").forEach((x) => x.classList.remove("active"));
  const n = document.querySelector(`.nav-item[data-view="${v}"]`);
  if (n) n.classList.add("active");
  STATE.view = v; STATE.token = null; load();
}
const COMMANDS = [
  { label: "Compose new email", icon: "edit", run: () => openCompose() },
  { label: "Go to Inbox", icon: "inbox", run: () => gotoView("inbox") },
  { label: "Go to Snoozed", icon: "schedule", run: () => gotoView("snoozed") },
  { label: "Go to Done", icon: "done", run: () => gotoView("done") },
  { label: "Go to Drafts", icon: "drafts", run: () => gotoView("drafts") },
  { label: "Go to Sent", icon: "send", run: () => gotoView("sent") },
  { label: "Refresh", icon: "refresh", run: () => load() },
  { label: "Email aliases", icon: "alternate_email", run: () => openAliases() },
  { label: "Templates & auto-reply", icon: "article", run: () => openTemplates() },
  { label: "Open settings", icon: "settings", run: () => openSettings() },
  { label: "Toggle pinned only", icon: "push_pin", run: () => { $("#pinnedOnly").checked = !$("#pinnedOnly").checked; render(); } },
  { label: "Keyboard shortcuts", icon: "keyboard", run: () => openHelp() },
];
let palIdx = 0, palFiltered = [];
function openPalette() { $("#cmdkOverlay").hidden = false; $("#cmdkInput").value = ""; renderPalette(""); $("#cmdkInput").focus(); }
function closePalette() { $("#cmdkOverlay").hidden = true; }
function renderPalette(q) {
  q = (q || "").toLowerCase();
  palFiltered = COMMANDS.filter((c) => c.label.toLowerCase().includes(q));
  palIdx = 0;
  const list = $("#cmdkList"); list.innerHTML = "";
  palFiltered.forEach((c, i) => {
    const el = document.createElement("div");
    el.className = "cmdk-item" + (i === 0 ? " sel" : "");
    el.innerHTML = `<i class="material-icons">${c.icon}</i><span>${esc(c.label)}</span>`;
    el.onmousedown = (e) => { e.preventDefault(); runPalette(i); };
    list.appendChild(el);
  });
}
function runPalette(i) { const c = palFiltered[i]; closePalette(); if (c) c.run(); }
function markPal() {
  [...$("#cmdkList").children].forEach((el, i) => el.classList.toggle("sel", i === palIdx));
  const sel = $("#cmdkList").children[palIdx]; if (sel) sel.scrollIntoView({ block: "nearest" });
}
$("#cmdkInput").addEventListener("input", (e) => renderPalette(e.target.value));
$("#cmdkInput").addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closePalette(); return; }
  if (e.key === "ArrowDown") { e.preventDefault(); palIdx = Math.min(palIdx + 1, palFiltered.length - 1); markPal(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); palIdx = Math.max(palIdx - 1, 0); markPal(); }
  else if (e.key === "Enter") { e.preventDefault(); runPalette(palIdx); }
});
$("#cmdkOverlay").addEventListener("click", (e) => { if (e.target.id === "cmdkOverlay") closePalette(); });

// ---------- Keyboard verbs (calm: verbs without density) ----------
let cursorIdx = -1;
function visibleCards() { return [...document.querySelectorAll("#list .card")]; }
function setCursor(i) {
  const cards = visibleCards();
  if (!cards.length) return;
  cursorIdx = Math.max(0, Math.min(i, cards.length - 1));
  cards.forEach((c) => c.classList.remove("cursor"));
  const el = cards[cursorIdx];
  el.classList.add("cursor");
  el.scrollIntoView({ block: "nearest" });
}
function cursorRow() {
  const el = visibleCards()[cursorIdx];
  if (!el) return null;
  return findRow(el.dataset.tid) || STATE.threads.get(el.dataset.tid) || null;
}
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); return; }
  if (!$("#cmdkOverlay").hidden) return;
  const t = e.target;
  if (t && (t.matches("input,textarea,select") || t.isContentEditable)) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (!$("#composeOverlay").hidden || !$("#settingsOverlay").hidden) return;
  const inReader = !$("#reader").hidden;
  switch (e.key) {
    case "j": setCursor(cursorIdx + 1); break;
    case "k": setCursor(cursorIdx - 1); break;
    case "o": case "Enter": { if (!inReader) { const r = cursorRow(); if (r) openThread(r.id); } break; }
    case "e": { if (inReader) $("#readerDone").click(); else { const r = cursorRow(); if (r) doDone(r); } break; }
    case "p": { if (!inReader) { const r = cursorRow(); if (r) doPin(r); } break; }
    case "s": { if (!inReader) { const cards = visibleCards(); const r = cursorRow(); if (r && cards[cursorIdx]) openSnooze(cards[cursorIdx], r); } break; }
    case "r": { if (inReader) $("#replyBox").focus(); else { const r = cursorRow(); if (r) openThread(r.id); } break; }
    case "c": openCompose(); break;
    case "/": e.preventDefault(); $("#searchInput").focus(); break;
    case "?": openHelp(); break;
    default: return;
  }
});

// ---------- Snackbar ----------
function toast(msg, undo) {
  const sb = $("#snackbar");
  $("#snackText").textContent = msg;
  $("#snackUndo").style.display = undo ? "" : "none";
  $("#snackUndo").onclick = () => { sb.hidden = true; if (undo) undo(); };
  sb.hidden = false;
  clearTimeout(undoTimer);
  undoTimer = setTimeout(() => (sb.hidden = true), 6000);
}

// ---------- Load ----------
// Fresh load: fetch the first page (PAGE items) and REPLACE the current view.
async function load() {
  $("#loading").hidden = false;
  $("#loading").textContent = "Loading…";
  const v = STATE.view;
  const lim = "limit=" + PAGE;
  let d;
  if (v === "inbox") {
    d = await api("/api/inbox?" + lim);
    STATE.inboxCache = d;
    STATE.token = d.nextPageToken || null;
  } else if (v === "pinned") {
    const i = await api("/api/inbox?" + lim);
    STATE.inboxCache = i;
    STATE.token = i.nextPageToken || null;
    d = { items: i.pinned || [], email: i.email };
  } else if (v === "snoozed") {
    d = await api("/api/snoozed?" + lim);
    STATE.token = d.nextPageToken || null;
  } else if (v === "done") {
    d = await api("/api/done_list?" + lim);
    STATE.token = d.nextPageToken || null;
  } else if (v === "drafts") {
    d = await api("/api/drafts?" + lim);
    STATE.token = d.nextPageToken || null;
  } else if (v === "sent") {
    d = await api("/api/sent?" + lim);
    STATE.token = d.nextPageToken || null;
  } else if (v === "search") {
    d = await api("/api/search?q=" + encodeURIComponent(STATE.query || "") + "&" + lim);
    STATE.token = d.nextPageToken || null;
  } else if (v.startsWith("bundle:")) {
    let i = STATE.inboxCache;
    if (!i) { i = await api("/api/inbox?" + lim); STATE.inboxCache = i; }
    STATE.token = i.nextPageToken || null;
    const b = (i.bundles || []).find((x) => x.name === v.slice(7));
    d = { items: b ? b.items : [], email: i.email };
  } else {
    d = { items: [] };
    STATE.token = null;
  }
  STATE.data = d;
  indexThreads(d);
  if (d.email) STATE.email = d.email;
  if (d.email) $("#acctEmail").textContent = d.email;
  if (v === "inbox") renderNavBundles();
  render();
  enrichDocLinks();
  ensureFilled();
}

// ---------- Lazy Docs/Drive link chips on cards ----------
function allVisibleRows() {
  const d = STATE.data;
  if (!d) return [];
  const pools = [d.pinned, d.primary, d.items, ...((d.bundles || []).map((b) => b.items))];
  const rows = [];
  pools.forEach((p) => { if (p) p.forEach((r) => rows.push(r)); });
  return rows;
}
function injectDocChips(row) {
  document.querySelectorAll(`.card[data-tid="${row.id}"]`).forEach((c) => {
    if (c.querySelector(".doc-links")) return; // already there
    const body = c.querySelector(".body");
    if (body) body.appendChild(docChipsEl(row.docLinks, "card"));
  });
}
// After a list renders, fetch shared Docs/Drive links for the visible rows (the list
// fetch skips bodies) and pop them onto the cards. Deduped via row.docLinks so the
// inbox shows instantly and chips fill in a beat later; cached on the row so re-renders
// (backfill, view switches) keep them without refetching.
async function enrichDocLinks() {
  const need = allVisibleRows().filter((r) => r.messageId && r.docLinks === undefined);
  if (!need.length) return;
  need.forEach((r) => { r.docLinks = null; }); // mark in-flight
  let res;
  try { res = await post("/api/doc_links", { messageIds: need.map((r) => r.messageId) }); }
  catch (e) { res = null; }
  if (!res) { need.forEach((r) => { if (r.docLinks === null) r.docLinks = undefined; }); return; }
  need.forEach((r) => {
    r.docLinks = res[r.messageId] || [];
    if (r.docLinks.length) injectDocChips(r);
  });
}

// Merge a fresh inbox page into the accumulated cache, deduping by thread id
// (a thread can straddle a page boundary, so the same id may recur).
function mergeInbox(base, page) {
  base = base || {};
  base.pinned = base.pinned || [];
  base.primary = base.primary || [];
  base.bundles = base.bundles || [];
  base.email = base.email || page.email;
  const seen = new Set();
  base.pinned.forEach((r) => seen.add(r.id));
  base.primary.forEach((r) => seen.add(r.id));
  base.bundles.forEach((b) => b.items.forEach((r) => seen.add(r.id)));
  const addAll = (arr, items) => (items || []).forEach((r) => {
    if (!seen.has(r.id)) { arr.push(r); seen.add(r.id); }
  });
  addAll(base.pinned, page.pinned);
  addAll(base.primary, page.primary);
  (page.bundles || []).forEach((pb) => {
    let bb = base.bundles.find((x) => x.name === pb.name);
    if (!bb) { bb = { name: pb.name, icon: pb.icon, color: pb.color, count: 0, items: [] }; base.bundles.push(bb); }
    addAll(bb.items, pb.items);
    bb.count = bb.items.length;
  });
  return base;
}

// "Load more": fetch only the NEXT page via pageToken and append it.
// Appends ONLY the newly-arrived cards to the existing DOM so existing cards
// never get torn down — no flash, no scroll jump, no interrupted animations.
async function loadMore() {
  if (!STATE.token) return;
  const v = STATE.view;
  const lim = "limit=" + PAGE;
  const tok = "&pageToken=" + encodeURIComponent(STATE.token);
  if (v === "inbox" || v === "pinned" || v.startsWith("bundle:")) {
    const cache0 = STATE.inboxCache || {};
    const prevPinned = new Set((cache0.pinned || []).map((r) => r.id));
    const prevBundles = new Set((cache0.bundles || []).map((b) => b.name));
    const p = await api("/api/inbox?" + lim + tok);
    STATE.inboxCache = mergeInbox(STATE.inboxCache, p);
    STATE.token = p.nextPageToken || null;
    if (v === "inbox") { STATE.data = STATE.inboxCache; renderNavBundles(); }
    else if (v === "pinned") STATE.data = { items: STATE.inboxCache.pinned || [], email: STATE.inboxCache.email };
    else {
      const b = (STATE.inboxCache.bundles || []).find((x) => x.name === v.slice(7));
      STATE.data = { items: b ? b.items : [], email: STATE.inboxCache.email };
    }
    indexThreads(STATE.data);
    if (v === "inbox") {
      const cache = STATE.inboxCache;
      // Pinned growth or a brand-new bundle category changes the list structure
      // above the append point — an incremental tail-append can't place those, so
      // re-render once (keeping scroll). The common case (more primary mail) appends.
      const grewPinned = (cache.pinned || []).some((r) => !prevPinned.has(r.id));
      const newBundle = (cache.bundles || []).some((b) => !prevBundles.has(b.name));
      if (grewPinned || newBundle || $("#pinnedOnly").checked) { scrollPreservingRender(); enrichDocLinks(); return; }
      appendInboxDelta();
      syncBundles();
      refreshFooter();
    } else {
      appendItemsDelta();
    }
    enrichDocLinks();
    return;
  }
  let url;
  if (v === "snoozed") url = "/api/snoozed?" + lim + tok;
  else if (v === "done") url = "/api/done_list?" + lim + tok;
  else if (v === "sent") url = "/api/sent?" + lim + tok;
  else if (v === "search") url = "/api/search?q=" + encodeURIComponent(STATE.query || "") + "&" + lim + tok;
  else return;
  const p = await api(url);
  STATE.data.items = STATE.data.items || [];
  const seen = new Set(STATE.data.items.map((r) => r.id));
  (p.items || []).forEach((r) => { if (!seen.has(r.id)) { STATE.data.items.push(r); seen.add(r.id); } });
  STATE.token = p.nextPageToken || null;
  indexThreads(STATE.data);
  appendItemsDelta();
  enrichDocLinks();
}

// Insert cards before the footer, emitting date-bucket labels and continuing
// from `startBucket` (the bucket of the last card already in the flow).
function insertGrouped(list, footer, rows, startBucket) {
  let cur = startBucket == null ? null : startBucket;
  rows.forEach((r) => {
    const b = dateBucket(r.ts);
    if (b !== cur) { list.insertBefore(label(b), footer); cur = b; }
    list.insertBefore(cardEl(r), footer);
  });
}
// Bucket of the last top-level card currently in the list (ignores nested bundle cards).
function lastDirectCardBucket(list) {
  const direct = [...list.children].filter((el) => el.classList.contains("card"));
  const last = direct[direct.length - 1];
  return last ? dateBucket(last.dataset.ts) : null;
}
// Append just the primary rows that aren't on screen yet (inbox view).
function appendInboxDelta() {
  const list = $("#list");
  const footer = list.querySelector(".list-footer");
  const have = new Set([...list.querySelectorAll(".card[data-tid]")].map((c) => c.dataset.tid));
  const newRows = (STATE.data.primary || []).filter((r) => !have.has(r.id));
  if (!newRows.length) return;
  // Only continue the prior bucket if primary cards are already on screen; if the
  // primary section was empty, start fresh (the last card would be a pinned one).
  const primaryShown = (STATE.data.primary || []).length - newRows.length;
  insertGrouped(list, footer, newRows, primaryShown > 0 ? lastDirectCardBucket(list) : null);
}
// Append just the rows that aren't on screen yet (non-inbox list views), then fix the footer.
function appendItemsDelta() {
  const list = $("#list");
  const footer = list.querySelector(".list-footer");
  const have = new Set([...list.querySelectorAll(".card[data-tid]")].map((c) => c.dataset.tid));
  const newRows = (STATE.data.items || []).filter((r) => !have.has(r.id));
  if (newRows.length) {
    if (STATE.view === "snoozed") newRows.forEach((r) => list.insertBefore(cardEl(r), footer));
    else insertGrouped(list, footer, newRows, lastDirectCardBucket(list));
  }
  refreshFooter();
}
// After a backfill, refresh each visible bundle's count badge and top up any open bundle.
function syncBundles() {
  const list = $("#list");
  const map = new Map((STATE.data.bundles || []).map((b) => [b.name, b]));
  list.querySelectorAll(".bundle").forEach((el) => {
    const b = map.get(el.dataset.bname);
    if (!b) return;
    const bc = el.querySelector(".bcount");
    if (bc) bc.textContent = b.count;
    if (el.classList.contains("open")) {
      const items = el.querySelector(".bundle-items");
      const have = new Set([...items.querySelectorAll(".card[data-tid]")].map((c) => c.dataset.tid));
      b.items.forEach((r) => { if (!have.has(r.id)) items.appendChild(cardEl(r)); });
    }
  });
}

// ---------- Keep the inbox topped up to ~50 in view ----------
const TARGET = PAGE; // keep at least this many conversations visible when more exist
function inViewCount() {
  const d = STATE.data;
  if (!d) return 0;
  if (STATE.view === "inbox") {
    if ($("#pinnedOnly").checked) return d.pinned?.length || 0;
    return (d.pinned?.length || 0) + (d.primary?.length || 0) + (d.bundles || []).reduce((s, b) => s + (b.count || 0), 0);
  }
  return (d.items || []).length;
}
async function ensureFilled() {
  // Only auto-fill the main inbox view (not pinned-only, which has its own small set)
  if (STATE.view !== "inbox" || $("#pinnedOnly").checked || STATE.filling) return;
  STATE.filling = true;
  try {
    let guard = 0;
    while (STATE.token && inViewCount() < TARGET && guard < 25) { guard++; await loadMore(); }
  } finally {
    STATE.filling = false;
  }
}
// Prune a thread from the in-memory model (in place, so shared refs stay consistent)
function removeFromData(id) {
  const d = STATE.data;
  if (!d) return;
  const prune = (arr) => { if (arr) { const i = arr.findIndex((r) => r.id === id); if (i >= 0) arr.splice(i, 1); } };
  prune(d.pinned); prune(d.primary); prune(d.items);
  (d.bundles || []).forEach((b) => { prune(b.items); b.count = b.items.length; });
  STATE.threads.delete(id);
}

// nav bundles populated after first load
function renderNavBundles() {
  const nb = $("#navBundles");
  nb.innerHTML = "";
  (STATE.data?.bundles || []).forEach((b) => {
    const a = document.createElement("a");
    a.className = "nav-item";
    a.dataset.view = "bundle:" + b.name;
    a.innerHTML = `<i class="material-icons" style="color:${b.color}">${b.icon}</i> ${esc(b.name)} <span style="margin-left:auto;color:#9aa0a6;font-weight:400">${b.count}</span>`;
    a.onclick = () => {
      document.querySelectorAll(".nav-item").forEach((x) => x.classList.remove("active"));
      a.classList.add("active");
      STATE.view = "bundle:" + b.name;
      STATE.token = null;
      load();
    };
    nb.appendChild(a);
  });
}

// ---------- Live sync (Server-Sent Events) ----------
let evtSource = null;
function startStream() {
  if (evtSource) return;
  try {
    evtSource = new EventSource("/api/stream");
    evtSource.onmessage = (e) => {
      let msg = {};
      try { msg = JSON.parse(e.data); } catch (_) {}
      // Deep link asked us to surface a specific thread (from the inboxclone:// handler)
      if (msg.focus) { openThread(msg.focus); return; }
      // Mailbox changed server-side (new mail / label change). Refresh the current view;
      // overlays sit on top and are untouched. Preserve scroll so it doesn't yank.
      const scroller = document.scrollingElement || document.documentElement;
      const y = scroller.scrollTop;
      STATE.inboxCache = null;
      load().then(() => { try { scroller.scrollTop = y; } catch (e) {} });
    };
    // EventSource reconnects automatically on error — nothing to handle.
  } catch (e) { /* SSE unsupported */ }
}

(async function start() {
  try { STATE.settings = await api("/api/settings"); } catch (e) { STATE.settings = {}; }
  applySettings(STATE.settings);
  await load();
  renderNavBundles();
  startStream();
  if (window.OPEN_THREAD) openThread(window.OPEN_THREAD);
})();
// Re-evaluate auto dark mode when the OS theme flips.
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if ((STATE.settings.dark_mode || "auto") === "auto") applySettings(STATE.settings);
});
