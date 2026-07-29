"""EcoVoice launcher — starts the all-in-one server and an mDNS alias.

Run with:  uv run main.py   (or: python main.py)

Starts child processes:
  • FastAPI server (UI + Parakeet ASR API) over HTTPS on :8501
  • An mDNS alias so the other phone can open  https://ecovoice.local:8501
    instead of typing the laptop's IP address.
Press Ctrl+C once to stop everything.
"""
import os
import sys
import shutil
import socket
import signal
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
MDNS_NAME = "ecovoice.local"
UI_PORT = 8501


def lan_ip():
    """The laptop's LAN address, advertised under the friendly mDNS name."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def start_mdns_alias(procs):
    """Publish ecovoice.local -> LAN IP via avahi, if available."""
    avahi = shutil.which("avahi-publish")
    ip = lan_ip()
    if not avahi:
        print(f"ℹ️  avahi-publish not found — phones can use https://{ip}:{UI_PORT} directly.")
        return
    # -a: publish an address record; -R: keep it registered while this runs.
    procs.append(subprocess.Popen([avahi, "-a", "-R", MDNS_NAME, ip], cwd=HERE))
    print(f"📛 Published {MDNS_NAME} -> {ip}. Phones can open https://{MDNS_NAME}:{UI_PORT}")


def main():
    # One server hosts both the UI and the API (HTTPS handled in server.py).
    server = [sys.executable, "server.py"]

    print(f"🚀 Starting EcoVoice on https://localhost:{UI_PORT} ... Ctrl+C to stop.")
    procs = [subprocess.Popen(server, cwd=HERE)]
    critical = list(procs)  # the server; the mDNS alias is optional
    start_mdns_alias(procs)

    def shutdown(*_):
        print("\n🛑 Shutting down EcoVoice...")
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # If a critical service (backend or UI) exits on its own, tear everything down.
    while True:
        for p in critical:
            if p.poll() is not None:
                print(f"⚠️  A core service exited (code {p.returncode}); stopping the rest.")
                shutdown()
        try:
            critical[0].wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


if __name__ == "__main__":
    main()
