<div align="center">

# 📞 EcoVoice

**Real-time, two-way assistive calling for deaf and non-verbal users — right in the browser.**

The caller *speaks*; their words appear as **live subtitles** on the other phone.
The other person *types*; their reply is **spoken aloud** back to the caller.

[![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.138-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![ONNX Runtime](https://img.shields.io/badge/ONNX%20Runtime-CPU-005CED?logo=onnx&logoColor=white)](https://onnxruntime.ai/)
[![NVIDIA Parakeet](https://img.shields.io/badge/ASR-Parakeet--TDT--0.6b-76B900?logo=nvidia&logoColor=white)](https://huggingface.co/nvidia)
![License](https://img.shields.io/badge/License-MIT-yellow)

</div>

---

## The problem

A phone call is inaccessible to someone who is deaf or non-verbal. EcoVoice turns any
two phones on the same Wi-Fi into an **accessible call bridge**: one side of the
conversation is transcribed to text in real time, and the other side's typed messages
are synthesized to natural speech — so a hearing caller and a deaf/non-verbal user can
hold a normal back-and-forth conversation.

## Features

- 🎙️ **Speech → live subtitles** — on-device English ASR with NVIDIA's
  **Parakeet-TDT-0.6b** running on CPU via ONNX Runtime.
- ⌨️ **Text → speech** — the receiver types; the reply is synthesized (gTTS) and
  auto-played on the caller's phone.
- 🟢 **Live presence** — each phone shows the other's status: *connected*, *speaking*, or *typing*.
- 📞 **Call lifecycle** — either side can hang up; both instantly see the call end and can start a fresh one.
- 🗄️ **Persistent transcript** — messages clear from the screen after delivery for privacy,
  but the full conversation is archived to **SQLite** and viewable in a History page.
- 📱 **Zero-install for the second phone** — scan-free access via a friendly
  `https://ecovoice.local:8501` mDNS name; just open the URL and pick a role.

## How it works

A **single FastAPI server** hosts both the web UI and the JSON API on one HTTPS origin
(no CORS, no mixed-content). The frontend is dependency-free **vanilla HTML/CSS/JS**.

```
        Caller phone                 FastAPI server (:8501, HTTPS)              Receiver phone
   ┌────────────────────┐        ┌──────────────────────────────────┐      ┌────────────────────┐
   │ 🎙️ record mic      │──WAV──▶│ /api/transcribe → Parakeet (ONNX) │      │  reads subtitle    │
   │ (decode→16kHz WAV  │        │ /api/say        → gTTS (MP3)       │◀─txt─│  ⌨️ types reply    │
   │  in the browser)   │◀─MP3───│ in-memory relay + SQLite archive  │──────▶│  hears TTS audio   │
   └────────────────────┘        └──────────────────────────────────┘      └────────────────────┘
                     both phones poll /api/messages, /api/presence, /api/call every 2s
```

**Engineering highlights**

- **Browser-side audio conditioning.** The mic records WebM/Opus, but Parakeet needs
  16 kHz mono WAV. Rather than add server-side audio deps, the client decodes and
  resamples via the Web Audio `OfflineAudioContext` and encodes a WAV in JavaScript.
- **Stateless two-device sync.** The two phones are independent browser sessions; the
  server is the single source of truth. A lightweight polling relay (`since`-cursor
  messages, TTL-based presence heartbeats, a call-lifecycle flag) keeps them in sync.
- **Secure context for mobile mics.** Browsers only expose the microphone over HTTPS,
  so the launcher serves a self-signed cert and publishes an mDNS hostname so a phone
  can reach the laptop without typing an IP.
- **Privacy-aware persistence.** Delivered messages are cleared from the UI but written
  to SQLite, so the live screens stay clean while a full transcript survives for review.

## Quick start

```bash
./run.sh
```

That's it. The script installs everything the first time ([`uv`](https://docs.astral.sh/uv/),
Python, dependencies), generates the local HTTPS certificate, and starts the app.
The **first run downloads the Parakeet model** from Hugging Face (cached afterward).

Then:

1. On the laptop, open **`https://localhost:8501`**.
2. On a phone on the same Wi-Fi, open **`https://ecovoice.local:8501`** (accept the
   one-time self-signed-cert warning).
3. One device opens **Caller**, the other opens **Receiver** — start talking.

<details>
<summary>Run manually (developers)</summary>

```bash
uv sync            # install dependencies
uv run main.py     # start server (HTTPS) + mDNS alias;  or:  uv run server.py
```
</details>

## Tech stack

| Layer      | Choice                                                              |
| ---------- | ------------------------------------------------------------------ |
| Backend    | Python · FastAPI · Uvicorn (HTTPS)                                  |
| ASR        | NVIDIA Parakeet-TDT-0.6b · `onnx-asr` · ONNX Runtime (CPU)          |
| TTS        | gTTS (Google Text-to-Speech)                                       |
| Frontend   | Vanilla HTML / CSS / JS · Web Audio API (`OfflineAudioContext`)     |
| Storage    | SQLite                                                             |
| Ops        | `uv` env management · self-signed TLS · Avahi/mDNS · one-shot `run.sh` |

## Project structure

```
ecovoice/
├── server.py           # FastAPI: serves the UI + all /api endpoints + Parakeet ASR
├── main.py             # Launcher: HTTPS server + mDNS (ecovoice.local) alias
├── run.sh              # One-command setup + run (installs uv, deps, cert; launches)
├── static/             # Frontend
│   ├── index.html      #   landing (pick a role)
│   ├── caller.html     #   caller: record speech
│   ├── receiver.html   #   receiver: read subtitle, type reply
│   ├── history.html    #   archived transcript
│   ├── style.css
│   └── app.js          #   polling relay, mic capture + WAV encoding, call lifecycle
└── pyproject.toml
```

## Notes & limitations

- **English** ASR (Parakeet). The architecture cleanly supports adding other languages/models.
- **TTS needs internet** (gTTS is a cloud service); swap in `pyttsx3`/Piper for fully offline speech.
- **Real-time sync via 2s polling** — simple and robust for a demo; WebSockets would cut latency.
- **mDNS** (`ecovoice.local`) resolves on iOS/macOS and Android 12+; an IP fallback is shown otherwise.

## License

MIT
# EcoVoice
