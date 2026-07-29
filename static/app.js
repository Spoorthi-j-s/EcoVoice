"use strict";

// Same-origin API — the page and the backend are served by the same FastAPI app.
async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`);
  return res;
}
async function apiJson(path, opts) { return (await api(path, opts)).json(); }

function postJson(path, body) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// Best-effort presence heartbeat (never throws).
function beat(role, state = "online") {
  postJson("/api/presence", { role, state }).catch(() => {});
}

function setStatus(el, msg, kind = "") {
  if (!el) return;
  el.textContent = msg;
  el.className = "status" + (kind ? " " + kind : "");
}

// Render the "other phone" presence pill.
function renderPresence(state, label) {
  const dot = document.querySelector("#presence .dot");
  const text = document.getElementById("presenceText");
  if (!dot || !text) return;
  if (state === "speaking") { dot.className = "dot busy"; text.textContent = `${label} is speaking…`; }
  else if (state === "typing") { dot.className = "dot busy"; text.textContent = `${label} is typing…`; }
  else if (state === "online") { dot.className = "dot online"; text.textContent = `${label} connected`; }
  else { dot.className = "dot offline"; text.textContent = `${label} offline`; }
}

// Show the hung-up overlay and wire "start a new call".
function showEnded(myRole, by) {
  const overlay = document.getElementById("ended");
  if (!overlay || overlay.classList.contains("show")) return;
  const txt = document.getElementById("endedText");
  txt.textContent = by === myRole ? "You ended the call." : `Call ended by the ${cap(by)}.`;
  overlay.classList.add("show");
}
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);

document.getElementById("newCallBtn")?.addEventListener("click", async () => {
  try { await postJson("/api/reset", {}); } catch (e) {}
  location.reload();
});

// The ✕ on the "call ended" card = don't start a new call; go back Home.
document.getElementById("closeBtn")?.addEventListener("click", () => { location.href = "/"; });

// ===========================================================================
// AUDIO: record from the mic, then decode → resample to 16 kHz mono → WAV.
// Parakeet expects 16 kHz WAV, but the browser records webm/opus, so we
// convert in the browser to avoid any server-side audio dependencies.
// ===========================================================================
async function blobToWav16k(blob) {
  const arrayBuf = await blob.arrayBuffer();
  const decodeCtx = new (window.AudioContext || window.webkitAudioContext)();
  const decoded = await decodeCtx.decodeAudioData(arrayBuf);
  decodeCtx.close();

  const targetRate = 16000;
  const frames = Math.ceil(decoded.duration * targetRate);
  const offline = new OfflineAudioContext(1, frames, targetRate); // 1 = mono downmix
  const src = offline.createBufferSource();
  src.buffer = decoded;
  src.connect(offline.destination);
  src.start();
  const rendered = await offline.startRendering();
  return encodeWav(rendered.getChannelData(0), targetRate);
}

function encodeWav(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeStr = (off, s) => { for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)); };
  writeStr(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true);   // PCM header size
  view.setUint16(20, 1, true);    // PCM format
  view.setUint16(22, 1, true);    // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true);    // block align
  view.setUint16(34, 16, true);   // bits per sample
  writeStr(36, "data");
  view.setUint32(40, samples.length * 2, true);
  let off = 44;
  for (let i = 0; i < samples.length; i++, off += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([view], { type: "audio/wav" });
}

// ===========================================================================
// PAGE CONTROLLERS
// ===========================================================================
const page = document.querySelector("[data-page]")?.dataset.page;

if (page === "index") initIndex();
else if (page === "caller") initCaller();
else if (page === "receiver") initReceiver();
else if (page === "history") initHistory();

// ---- Landing -------------------------------------------------------------
function initIndex() {
  document.getElementById("thisAddr").textContent = `${location.protocol}//${location.host}`;
  const status = document.getElementById("status");
  document.getElementById("resetBtn").addEventListener("click", async () => {
    try {
      await postJson("/api/reset", {});
      setStatus(status, "Live call cleared. Pick a role to begin.", "ok");
    } catch (e) {
      setStatus(status, "Could not reach the server: " + e.message, "err");
    }
  });
}

// ---- Caller --------------------------------------------------------------
function initCaller() {
  const recBtn = document.getElementById("recBtn");
  const status = document.getElementById("status");
  const replyCard = document.getElementById("replyCard");
  const replyAudio = document.getElementById("replyAudio");
  let recorder = null, chunks = [], recording = false, lastAudioId = null;

  recBtn.addEventListener("click", async () => {
    if (!recording) {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        recorder = new MediaRecorder(stream);
        chunks = [];
        recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
        recorder.onstop = () => stream.getTracks().forEach((t) => t.stop());
        recorder.start();
        recording = true;
        recBtn.textContent = "⏹️ Recording — tap to send";
        recBtn.classList.add("recording");
        setStatus(status, "Listening…");
        beat("caller", "speaking");
      } catch (e) {
        setStatus(status, "Microphone blocked: " + e.message + " (needs HTTPS).", "err");
      }
      return;
    }

    // Stop + send
    recording = false;
    recBtn.textContent = "🎙️ Hold conversation — tap to record";
    recBtn.classList.remove("recording");
    recBtn.disabled = true;
    setStatus(status, "Transcribing and sending…");

    const done = new Promise((res) => (recorder.onstop = () => {
      recorder.stream?.getTracks().forEach((t) => t.stop());
      res();
    }));
    recorder.stop();
    await done;

    try {
      const wav = await blobToWav16k(new Blob(chunks, { type: "audio/webm" }));
      const fd = new FormData();
      fd.append("file", wav, "call.wav");
      const r = await apiJson("/api/transcribe", { method: "POST", body: fd });
      if (r.transcript && r.transcript.trim()) setStatus(status, "✓ Message delivered to receiver.", "ok");
      else setStatus(status, "No speech detected in that recording.", "err");
    } catch (e) {
      setStatus(status, "Send failed: " + e.message, "err");
    } finally {
      recBtn.disabled = false;
      beat("caller", "online");
    }
  });

  document.getElementById("endBtn").addEventListener("click", () => postJson("/api/end", { role: "caller" }));

  // The ended overlay must only appear for an end that happens WHILE we're on the
  // page — not for a stale "ended" flag left over from a previous call.
  let sawLiveCall = false, clearedStale = false;

  async function poll() {
    beat("caller", recording ? "speaking" : "online");
    try {
      const call = await apiJson("/api/call");
      if (call.ended) {
        if (sawLiveCall) { showEnded("caller", call.by); return; }
        // Opened while a previous call was still flagged ended → start fresh.
        if (!clearedStale) { clearedStale = true; await postJson("/api/reset", {}); }
        return;
      }
      sawLiveCall = true;
      renderPresence((await apiJson("/api/presence")).receiver, "Receiver");

      const { messages } = await apiJson("/api/messages?since=0");
      const replies = messages.filter((m) => m.role === "receiver" && m.audio_id);
      if (replies.length) {
        const latest = replies[replies.length - 1];
        if (latest.audio_id !== lastAudioId) {
          lastAudioId = latest.audio_id;
          replyCard.style.display = "block";
          replyAudio.src = `/api/audio/${latest.audio_id}`;
          replyAudio.play().catch(() => {}); // may need a tap on some browsers
        }
      }
    } catch (e) { /* transient: backend still loading the model */ }
  }
  poll();
  setInterval(poll, 2000);
}

// ---- Receiver ------------------------------------------------------------
function initReceiver() {
  const input = document.getElementById("reply");
  const sendBtn = document.getElementById("sendBtn");
  const status = document.getElementById("status");
  const subtitle = document.getElementById("subtitle");
  const clearBtn = document.getElementById("clearBtn");
  let clearedUpto = 0, latestId = 0;

  async function send() {
    const text = input.value.trim();
    if (!text) { setStatus(status, "Type something first.", "err"); return; }
    sendBtn.disabled = true;
    try {
      await postJson("/api/say", { text });
      input.value = "";                 // clear our message from the screen
      setStatus(status, "✓ Sent — spoken on the caller's phone.", "ok");
      beat("receiver", "online");
    } catch (e) {
      setStatus(status, "Send failed: " + e.message, "err");
    } finally {
      sendBtn.disabled = false;
    }
  }
  sendBtn.addEventListener("click", send);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
  input.addEventListener("input", () => beat("receiver", input.value.trim() ? "typing" : "online"));

  clearBtn.addEventListener("click", () => {
    clearedUpto = latestId;
    subtitle.innerHTML = '<span class="waiting">Waiting for the caller to speak…</span>';
    clearBtn.style.display = "none";
  });

  document.getElementById("endBtn").addEventListener("click", () => postJson("/api/end", { role: "receiver" }));

  // The ended overlay must only appear for an end that happens WHILE we're on the
  // page — not for a stale "ended" flag left over from a previous call.
  let sawLiveCall = false, clearedStale = false;

  async function poll() {
    beat("receiver", input.value.trim() ? "typing" : "online");
    try {
      const call = await apiJson("/api/call");
      if (call.ended) {
        if (sawLiveCall) { showEnded("receiver", call.by); return; }
        if (!clearedStale) { clearedStale = true; await postJson("/api/reset", {}); }
        return;
      }
      sawLiveCall = true;
      renderPresence((await apiJson("/api/presence")).caller, "Caller");

      const { messages } = await apiJson("/api/messages?since=0");
      const callerLines = messages.filter((m) => m.role === "caller");
      if (callerLines.length) {
        const latest = callerLines[callerLines.length - 1];
        latestId = latest.id;
        if (latest.id > clearedUpto) {
          subtitle.textContent = latest.text;
          clearBtn.style.display = "block";
        }
      }
    } catch (e) { /* transient */ }
  }
  poll();
  setInterval(poll, 2000);
}

// ---- History -------------------------------------------------------------
async function initHistory() {
  const list = document.getElementById("list");
  try {
    const { messages } = await apiJson("/api/history");
    if (!messages.length) { list.innerHTML = '<p class="sub">No messages stored yet.</p>'; return; }
    list.innerHTML = "";
    for (const m of messages) {
      const ts = new Date(m.ts * 1000).toLocaleTimeString();
      const who = m.role === "caller" ? "📞 Caller" : "🦻 Receiver";
      const div = document.createElement("div");
      div.className = "hist-item " + m.role;
      div.innerHTML = `<span class="ts">${ts}</span><span class="who">${who}:</span> ${escapeHtml(m.text)}`;
      list.appendChild(div);
    }
  } catch (e) {
    list.innerHTML = `<p class="status err">Could not load history: ${e.message}</p>`;
  }
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}
