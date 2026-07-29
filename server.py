import io
import os
import time
import sqlite3
import tempfile
import itertools
import threading
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, UploadFile, File, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from gtts import gTTS
import onnx_asr

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# Global reference placeholder for the ONNX Speech Recognition model
parakeet_model = None

# ----------------------------------------------------------------------------
# Shared call state (in-memory relay between the two phones).
# The Caller phone and Receiver phone are separate browser sessions, so they
# cannot share Streamlit session_state. Instead they both talk to this backend,
# which holds the single source of truth for the live conversation.
# ----------------------------------------------------------------------------
_lock = threading.Lock()
_id_counter = itertools.count(1)
messages: list[dict] = []          # conversation log, oldest first
audio_store: dict[str, bytes] = {}  # audio_id -> mp3 bytes (receiver replies)

# Presence: each phone heartbeats its current activity. An entry older than
# PRESENCE_TTL seconds is treated as offline (the phone stopped polling).
PRESENCE_TTL = 6.0
presence: dict[str, dict] = {}      # role -> {"state": str, "ts": float}

# Call lifecycle: either phone can end the call; both phones then see it ended.
call_ended = False
call_ended_by: str | None = None

# Persistent archive. Messages are cleared from the phone screens after delivery,
# but every one is written here so the full conversation survives for the demo.
DB_PATH = os.path.join(os.path.dirname(__file__), "ecovoice_calls.db")


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS messages ("
    "id INTEGER PRIMARY KEY, role TEXT, text TEXT, audio_id TEXT, ts REAL)"
)


def _db():
    """A connection with the schema ensured, so reads never hit a missing table
    (e.g. if the db file is deleted or recreated empty while the server runs)."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(_SCHEMA)
    return conn


def _init_db():
    """Start each server run with a clean slate: empty the archive and reset ids.

    The demo is meant to start fresh on every launch, so stopping/restarting the
    server clears the stored conversation (History) rather than carrying it over.
    """
    global _id_counter
    with _db() as conn:
        conn.execute("DELETE FROM messages")
    _id_counter = itertools.count(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initializes the Parakeet TDT v3 model mapped to local CPU vectors"""
    global parakeet_model
    _init_db()
    _clear_call()  # no leftover live-call / "ended" state from a previous run
    print("🚀 Loading Multilingual NVIDIA Parakeet-TDT-0.6b-v3 (ONNX CPU Engine)...")

    # Load the TDT model mapped directly to ONNX CPU Runtime optimizations.
    # This will automatically download weights from Hugging Face on the first boot.
    # Let load errors propagate so the server fails loudly instead of serving a
    # half-broken endpoint that crashes on every request.
    parakeet_model = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3")
    print("✅ Parakeet TDT v3 loaded successfully with AVX2 CPU operator fusion.")

    yield

    parakeet_model = None


app = FastAPI(
    title="EcoVoice Multiplatform CPU - NVIDIA Parakeet Core",
    lifespan=lifespan,
)


@app.middleware("http")
async def no_cache(request, call_next):
    """Disable caching for everything. This is a live two-device app: pages, JS,
    and API polls must always be fresh — stale cached HTML/JS was making the
    'call ended' overlay reappear on newly started calls."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# Serve the frontend's CSS/JS. Pages themselves use clean routes (see below).
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _add_message(role: str, text: str, audio_id: str | None = None) -> dict:
    """Append a message to the live log AND persist it to the database."""
    with _lock:
        msg = {
            "id": next(_id_counter),
            "role": role,          # "caller" or "receiver"
            "text": text,
            "audio_id": audio_id,  # set for receiver replies that have TTS audio
        }
        messages.append(msg)
    # Persist outside the lock — the DB is the permanent archive for the demo.
    with _db() as conn:
        conn.execute(
            "INSERT INTO messages (id, role, text, audio_id, ts) VALUES (?, ?, ?, ?, ?)",
            (msg["id"], role, text, audio_id, time.time()),
        )
    return msg


# -------- Frontend pages (plain HTML/CSS/JS served from ./static) -----------
def _page(name: str) -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, name))


@app.get("/", include_in_schema=False)
async def index():
    # Returning home ends the "call ended" state, so opening a role next starts fresh.
    if call_ended:
        _clear_call()
    return _page("index.html")


# Opening a role page = starting/joining a call. If a previous call was left in
# the "ended" state, clear it here (server-side, so it works even with cached JS)
# so the "call ended" overlay never appears on a freshly opened call.
@app.get("/caller", include_in_schema=False)
async def caller_page():
    if call_ended:
        _clear_call()
    return _page("caller.html")


@app.get("/receiver", include_in_schema=False)
async def receiver_page():
    if call_ended:
        _clear_call()
    return _page("receiver.html")


@app.get("/history", include_in_schema=False)
async def history_page():
    return _page("history.html")


@app.get("/api/health")
async def health():
    """Liveness/health check — confirms the server and model are up."""
    return {
        "service": "EcoVoice Parakeet ASR",
        "model_loaded": parakeet_model is not None,
        "messages": len(messages),
    }


@app.post("/api/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """Caller speaks: transcribe the audio and post it to the shared conversation."""
    if parakeet_model is None:
        raise HTTPException(status_code=503, detail="ASR model is not loaded yet")

    # Stream incoming network data to a private temp file. Using tempfile avoids
    # trusting the client-supplied filename (path traversal) and writing into CWD.
    suffix = os.path.splitext(file.filename or "")[1] or ".wav"
    fd, temp_filepath = tempfile.mkstemp(prefix="ecovoice_asr_", suffix=suffix)

    try:
        with os.fdopen(fd, "wb") as buffer:
            buffer.write(await file.read())

        out = parakeet_model.recognize(temp_filepath)
        text_payload = out.strip() if out else ""

        # Publish the caller's spoken words to the shared log for the receiver.
        if text_payload:
            _add_message("caller", text_payload)

        return {"status": "success", "transcript": text_payload}

    except Exception as e:
        return {"status": "error", "message": str(e)}

    finally:
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)


class ReplyIn(BaseModel):
    text: str


@app.post("/api/say")
async def say(reply: ReplyIn):
    """Receiver types: synthesize speech and queue it for the caller to hear."""
    text = (reply.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty text")

    # Synthesize the typed reply to MP3 in memory (gTTS = Google cloud TTS).
    buf = io.BytesIO()
    gTTS(text=text, lang="en", slow=False).write_to_fp(buf)

    audio_id = f"a{next(_id_counter)}"
    with _lock:
        audio_store[audio_id] = buf.getvalue()

    msg = _add_message("receiver", text, audio_id=audio_id)
    return {"status": "success", "message_id": msg["id"], "audio_id": audio_id}


@app.get("/api/messages")
async def get_messages(since: int = 0):
    """Return conversation messages with id greater than `since` (for polling)."""
    with _lock:
        new = [m for m in messages if m["id"] > since]
        last_id = messages[-1]["id"] if messages else 0
    return {"messages": new, "last_id": last_id}


@app.get("/api/audio/{audio_id}")
async def get_audio(audio_id: str):
    """Serve a synthesized receiver reply so the caller's phone can play it."""
    data = audio_store.get(audio_id)
    if data is None:
        raise HTTPException(status_code=404, detail="audio not found")
    return Response(content=data, media_type="audio/mpeg")


class PresenceIn(BaseModel):
    role: str
    state: str = "online"  # "online" | "speaking" | "typing"


@app.post("/api/presence")
async def set_presence(p: PresenceIn):
    """Heartbeat from a phone reporting its current activity."""
    with _lock:
        presence[p.role] = {"state": p.state, "ts": time.time()}
    return {"ok": True}


@app.get("/api/presence")
async def get_presence():
    """Current activity of both phones; stale heartbeats read as 'offline'."""
    now = time.time()
    with _lock:
        out = {}
        for r in ("caller", "receiver"):
            info = presence.get(r)
            out[r] = info["state"] if info and now - info["ts"] <= PRESENCE_TTL else "offline"
    return out


@app.get("/api/history")
async def history():
    """Return the full persisted archive from the database (nothing is cleared here)."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, role, text, audio_id, ts FROM messages ORDER BY id"
        ).fetchall()
    return {
        "messages": [
            {"id": r[0], "role": r[1], "text": r[2], "audio_id": r[3], "ts": r[4]}
            for r in rows
        ]
    }


class EndIn(BaseModel):
    role: str


@app.post("/api/end")
async def end_call(e: EndIn):
    """Either phone hangs up; the other phone sees the call end on its next poll."""
    global call_ended, call_ended_by
    with _lock:
        call_ended = True
        call_ended_by = e.role
    return {"ended": True, "by": call_ended_by}


@app.get("/api/call")
async def call_status():
    """Current call lifecycle state, polled by both phones."""
    return {"ended": call_ended, "by": call_ended_by}


def _clear_call():
    """Reset the live call (messages/audio/presence/ended). DB archive untouched."""
    global call_ended, call_ended_by
    with _lock:
        messages.clear()
        audio_store.clear()
        presence.clear()
        call_ended = False
        call_ended_by = None


@app.post("/api/reset")
async def reset():
    """Clear the live on-screen call. The database archive is left untouched."""
    _clear_call()
    return {"status": "reset"}


if __name__ == "__main__":
    # Single server hosts both the UI and the API. Serve over HTTPS when certs
    # are present so phone browsers allow microphone access (secure context).
    here = os.path.dirname(__file__)
    cert = os.path.join(here, "certs", "cert.pem")
    key = os.path.join(here, "certs", "key.pem")
    ssl = {"ssl_certfile": cert, "ssl_keyfile": key} if os.path.exists(cert) else {}
    if not ssl:
        print("⚠️  No TLS certs in ./certs — serving HTTP; phone microphone will be blocked.")
    uvicorn.run(app, host="0.0.0.0", port=8501, **ssl)
