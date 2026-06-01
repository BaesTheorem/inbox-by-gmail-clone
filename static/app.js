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

const PAGE = 50;
let STATE = { view: "inbox", data: null, threadCache: {}, inboxCache: null, query: "", openBundles: new Set(), token: null, selected: new Set(), snoozeMulti: false };
let undoTimer = null;

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
  const chipsHtml = (row.highlights || []).length
    ? `<div class="chips">${row.highlights.map((h) => `<span class="chip"><i class="material-icons">${h.icon}</i>${esc(h.text)}</span>`).join("")}</div>`
    : "";
  const wakeHtml = row.wake ? `<div class="wake"><i class="material-icons">schedule</i>${esc(row.wake)}</div>` : "";
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
      ${chipsHtml}${wakeHtml}
    </div>
    <div class="actions">
      <button class="icon-btn act-pin" title="Pin"><i class="material-icons">push_pin</i></button>
      <button class="icon-btn act-snooze" title="Snooze"><i class="material-icons">schedule</i></button>
      <button class="icon-btn act-label" title="Move to bundle"><i class="material-icons">label</i></button>
      <button class="icon-btn act-done" title="Done"><i class="material-icons" style="color:var(--done-green)">check_circle</i></button>
      <button class="icon-btn act-things" title="Send to Things 3"><i class="material-icons">playlist_add_check</i></button>
    </div>`;
  el.addEventListener("click", (e) => {
    if (e.target.closest(".actions") || e.target.closest(".unsub-link") || e.target.closest(".select")) return;
    openThread(row.id);
  });
  el.querySelector(".select").onclick = (e) => { e.stopPropagation(); toggleSelect(row.id, el); };
  el.querySelector(".act-done").onclick = (e) => { e.stopPropagation(); doDone(row); };
  el.querySelector(".act-pin").onclick = (e) => { e.stopPropagation(); doPin(row, el); };
  el.querySelector(".act-snooze").onclick = (e) => { e.stopPropagation(); openSnooze(e.currentTarget, row); };
  el.querySelector(".act-label").onclick = (e) => { e.stopPropagation(); openRelabel(e.currentTarget, row); };
  el.querySelector(".act-things").onclick = (e) => { e.stopPropagation(); doThings(row); };
  const ul = el.querySelector(".unsub-link");
  if (ul) ul.onclick = (e) => { e.stopPropagation(); doUnsub(row.messageId, ul, row.sender); };
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
    for (const r of b.items) await post("/api/done", { threadId: r.id });
    toast(`${b.name} swept to Done`);
    load();
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
      if (d.primary?.length) {
        if (d.bundles?.length || d.pinned?.length) list.appendChild(label("Mail"));
        d.primary.forEach((r) => list.appendChild(cardEl(r)));
      }
    }
    const total = (d.pinned?.length || 0) + (pinnedOnly ? 0 : (d.primary?.length || 0) + (d.bundles?.length || 0));
    if (!total) $("#empty").hidden = false;
  } else {
    const rows = d.items || [];
    if (!rows.length) $("#empty").hidden = false;
    rows.forEach((r) => list.appendChild(cardEl(r)));
  }
  // Bottom footer: "N in view" + a "Load more" button when a full page came back
  let n;
  if (STATE.view === "inbox") {
    n = $("#pinnedOnly").checked
      ? (d.pinned?.length || 0)
      : (d.pinned?.length || 0) + (d.primary?.length || 0) + (d.bundles || []).reduce((s, b) => s + (b.count || 0), 0);
  } else {
    n = (d.items || []).length;
  }
  const hasMore = !!STATE.token;
  if (n || hasMore) {
    const footer = document.createElement("div");
    footer.className = "list-footer";
    if (hasMore) {
      const btn = document.createElement("button");
      btn.className = "load-more";
      btn.textContent = "Load more";
      btn.onclick = () => {
        const sc = document.scrollingElement || document.documentElement;
        const y = sc.scrollTop;
        btn.textContent = "Loading…"; btn.disabled = true;
        loadMore().then(() => { try { sc.scrollTop = y; } catch (e) {} });
      };
      footer.appendChild(btn);
    }
    const count = document.createElement("div");
    count.className = "view-count";
    count.textContent = n ? `${n} in view` : "";
    footer.appendChild(count);
    list.appendChild(footer);
  }
  // Gmail-style total next to the Inbox tab (last-known inbox total, persists across views)
  const it = STATE.inboxCache && STATE.inboxCache.inboxTotal;
  $("#inboxCount").textContent = it != null ? it.toLocaleString() : "";
}
function label(t) { const e = document.createElement("div"); e.className = "section-label"; e.textContent = t; return e; }
function esc(s) { return (s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

// ---------- Actions ----------
async function doDone(row) {
  removeCard(row.id);
  removeFromData(row.id);
  await post("/api/done", { threadId: row.id });
  toast("Marked done", async () => { await post("/api/undo_done", { threadId: row.id }); load(); });
  ensureFilled();
}
async function doPin(row, el) {
  row.pinned = !row.pinned;
  await post("/api/pin", { threadId: row.id, pinned: row.pinned });
  load();
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
  ids.forEach(removeCard);
  clearSelection();
  for (const id of ids) await post("/api/done", { threadId: id });
  toast(`${ids.length} marked done`);
  load();
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
  toast(`${ids.length} sent to Things 3`);
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
  toast(r.ok ? "Sent to Things 3" : "Things 3 not reachable");
}
function removeCard(tid) {
  document.querySelectorAll(`.card[data-tid="${tid}"]`).forEach((c) => {
    c.style.opacity = 0; c.style.transform = "translateX(60px)";
    setTimeout(() => c.remove(), 180);
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
  toast(ids.length > 1 ? `${ids.length} snoozed` : "Snoozed");
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
    if (e.target.closest(".actions") || e.target.closest(".unsub-link") || e.target.closest(".select")) return;
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
  $("#readerSubject").textContent = t.subject;
  reader.dataset.tid = tid;
  reader.dataset.rfc = t.rfcMessageId || "";
  reader.dataset.to = t.messages.length ? t.messages[t.messages.length - 1].senderEmail : "";
  reader.dataset.subject = t.subject;
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
  t.messages.forEach((m) => {
    const d = document.createElement("div");
    d.className = "msg";
    d.innerHTML = `<div class="mhead"><span class="mfrom">${esc(m.sender)}</span><span>${esc(m.date)}</span></div>
      <div class="mbody"></div>`;
    const mb = d.querySelector(".mbody");
    if (m.html) {
      const f = document.createElement("iframe");
      // allow-same-origin (no allow-scripts) lets the parent read/instrument the email
      // DOM for height + link routing, while email JS stays disabled.
      f.setAttribute("sandbox", "allow-same-origin");
      f.style.width = "100%";
      f.style.minHeight = "120px";
      f.onload = () => {
        try {
          const doc = f.contentWindow.document;
          f.style.height = (doc.body.scrollHeight + 24) + "px";
          // Route every email link to the system browser. Works in both Chrome and the
          // native WebKit window, and avoids X-Frame-Options / ERR_BLOCKED_BY_RESPONSE.
          doc.querySelectorAll("a[href]").forEach((a) => {
            a.addEventListener("click", (e) => { e.preventDefault(); openExternal(a.href); });
          });
        } catch (e) {}
      };
      mb.appendChild(f);
      f.srcdoc = m.html;
    } else {
      mb.textContent = m.text || "(no content)";
    }
    body.appendChild(d);
  });
}
function closeReader() { $("#reader").hidden = true; load(); }
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
$("#replySend").onclick = async () => {
  const r = $("#reader");
  const box = $("#replyBox");
  if (!box.innerText.trim() && !(STATE.replyAttachments || []).length) return;
  const subj = r.dataset.subject.startsWith("Re:") ? r.dataset.subject : "Re: " + r.dataset.subject;
  const fd = new FormData();
  fd.append("to", r.dataset.to);
  fd.append("subject", subj);
  fd.append("html", box.innerHTML);
  fd.append("text", box.innerText);
  fd.append("threadId", r.dataset.tid);
  fd.append("inReplyTo", r.dataset.rfc);
  (STATE.replyAttachments || []).forEach((f) => fd.append("attachments", f, f.name));
  const send = $("#replySend"); send.disabled = true;
  let res;
  try { res = await (await fetch("/api/send", { method: "POST", body: fd })).json(); }
  catch (e) { res = { error: String(e) }; }
  send.disabled = false;
  if (res.ok) { box.innerHTML = ""; STATE.replyAttachments = []; renderChips("replyAttachments", "#rAttachments"); toast("Sent"); openThread(r.dataset.tid); }
  else toast("Send failed: " + (res.error || ""));
};

// ---------- Compose ----------
function openCompose(opts = {}) {
  STATE.composeDraftId = opts.draftId || null;
  STATE.attachments = [];
  $("#cTo").value = opts.to || "";
  $("#cSubject").value = opts.subject || "";
  $("#cBody").innerHTML = opts.html || (opts.body ? esc(opts.body).replace(/\n/g, "<br>") : "");
  renderAttachChips();
  $("#composeDiscard").style.display = STATE.composeDraftId ? "" : "none";
  $("#composeOverlay").hidden = false;
}
function closeCompose() { $("#composeOverlay").hidden = true; STATE.composeDraftId = null; STATE.attachments = []; }
function clearCompose() { $("#cTo").value = $("#cSubject").value = ""; $("#cBody").innerHTML = ""; STATE.attachments = []; renderAttachChips(); }
$("#fab").onclick = () => $("#fabWrap").classList.toggle("open");
$("#fabCompose").onclick = () => { $("#fabWrap").classList.remove("open"); openCompose(); };
$("#fabReminder").onclick = () => { $("#fabWrap").classList.remove("open"); openCompose({ subject: "Reminder" }); };
$("#composeClose").onclick = closeCompose;
// Click the dimmed backdrop to dismiss compose
$("#composeOverlay").addEventListener("click", (e) => { if (e.target.id === "composeOverlay") closeCompose(); });
// Escape closes the topmost overlay
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("#snoozeMenu").hidden) { $("#snoozeMenu").hidden = true; return; }
  if (!$("#relabelMenu").hidden) { $("#relabelMenu").hidden = true; return; }
  if (!$("#composeOverlay").hidden) { closeCompose(); return; }
  if (!$("#reader").hidden) { closeReader(); return; }
});
$("#composeSend").onclick = async () => {
  const wasDraft = !!STATE.composeDraftId;
  const fd = new FormData();
  fd.append("to", $("#cTo").value);
  fd.append("subject", $("#cSubject").value);
  fd.append("html", $("#cBody").innerHTML);
  fd.append("text", $("#cBody").innerText);
  if (STATE.composeDraftId) fd.append("draftId", STATE.composeDraftId);
  (STATE.attachments || []).forEach((f) => fd.append("attachments", f, f.name));
  const btn = $("#composeSend"); btn.disabled = true;
  let res;
  try { res = await (await fetch("/api/send", { method: "POST", body: fd })).json(); }
  catch (e) { res = { error: String(e) }; }
  btn.disabled = false;
  if (res.ok) { closeCompose(); clearCompose(); toast("Sent"); if (wasDraft && STATE.view === "drafts") load(); }
  else toast("Send failed: " + (res.error || ""));
};
$("#composeDiscard").onclick = async () => {
  if (!STATE.composeDraftId) return;
  await post("/api/discard_draft", { draftId: STATE.composeDraftId });
  closeCompose(); clearCompose(); toast("Draft discarded");
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
    document.querySelectorAll(".nav-item").forEach((x) => x.classList.remove("active"));
    n.classList.add("active");
    STATE.view = n.dataset.view;
    STATE.token = null;
    load();
  };
});
$("#refreshBtn").onclick = load;
$("#pinnedOnly").onchange = render;
$("#menuBtn").onclick = () => document.querySelector(".layout").classList.toggle("nav-collapsed");
$("#searchInput").addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  const q = e.target.value.trim();
  document.querySelectorAll(".nav-item").forEach((x) => x.classList.remove("active"));
  if (q) { STATE.view = "search"; STATE.query = q; }
  else { STATE.view = "inbox"; document.querySelector('.nav-item[data-view="inbox"]').classList.add("active"); }
  STATE.token = null;
  load();
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
  if (d.email) $("#acctEmail").textContent = d.email;
  if (v === "inbox") renderNavBundles();
  render();
  ensureFilled();
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
async function loadMore() {
  if (!STATE.token) return;
  const v = STATE.view;
  const lim = "limit=" + PAGE;
  const tok = "&pageToken=" + encodeURIComponent(STATE.token);
  if (v === "inbox" || v === "pinned" || v.startsWith("bundle:")) {
    const p = await api("/api/inbox?" + lim + tok);
    STATE.inboxCache = mergeInbox(STATE.inboxCache, p);
    STATE.token = p.nextPageToken || null;
    if (v === "inbox") { STATE.data = STATE.inboxCache; renderNavBundles(); }
    else if (v === "pinned") STATE.data = { items: STATE.inboxCache.pinned || [], email: STATE.inboxCache.email };
    else {
      const b = (STATE.inboxCache.bundles || []).find((x) => x.name === v.slice(7));
      STATE.data = { items: b ? b.items : [], email: STATE.inboxCache.email };
    }
  } else {
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
  }
  render();
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
  await load();
  renderNavBundles();
  startStream();
  if (window.OPEN_THREAD) openThread(window.OPEN_THREAD);
})();
