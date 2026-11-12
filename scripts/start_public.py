"""Publish only the read-only snapshot origin through an outbound tunnel."""

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
address = Path(os.environ.get("RPA_PUBLIC_ADDRESS_FILE", str(ROOT / ".local/public-address.json")))
port = int(os.environ.get("RPA_PUBLIC_PORT", "8082"))
provider = os.environ.get("RPA_TUNNEL_PROVIDER", "ssh")
log_path = ROOT / ".local/public-tunnel.log"
processes = []


def cleanup():
    address.unlink(missing_ok=True)
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def stop(*_):
    cleanup()
    raise SystemExit(0)


def healthy(url):
    try:
        with urlopen(url + "/health", timeout=3) as response:
            return response.status == 200 and json.load(response).get("ok") is True
    except (OSError, ValueError):
        return False


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
try:
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "MY.public:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-access-log",
        ]
    )
    processes.append(server)
    for _ in range(40):
        if server.poll() is not None:
            raise RuntimeError("Сервис публикаций не запустился")
        if healthy(f"http://127.0.0.1:{port}"):
            break
        time.sleep(0.25)
    else:
        raise RuntimeError("Сервис публикаций не отвечает")
    if provider == "cloudflare":
        binary = ROOT / ".local/bin/cloudflared"
        command = [
            str(binary),
            "tunnel",
            "--no-autoupdate",
            "--protocol",
            "http2",
            "--url",
            f"http://127.0.0.1:{port}",
        ]
        pattern = r"https://[a-z0-9-]+\.trycloudflare\.com"
    elif provider == "ssh":
        command = [
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentityAgent=none",
            "-o",
            "PubkeyAuthentication=no",
            "-o",
            f"UserKnownHostsFile={ROOT / '.local/tunnel-known-hosts'}",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=15",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ExitOnForwardFailure=yes",
            "-R",
            f"80:127.0.0.1:{port}",
            "nokey@localhost.run",
        ]
        pattern = r"https://[a-z0-9-]+\.lhr\.life"
    else:
        raise RuntimeError("Неизвестный RPA_TUNNEL_PROVIDER")
    with log_path.open("w") as log:
        tunnel = subprocess.Popen(command, stdout=log, stderr=log)
        processes.append(tunnel)
        deadline = time.monotonic() + 90
        current_url = ""
        while server.poll() is None and tunnel.poll() is None:
            matches = re.findall(pattern, log_path.read_text())
            url = matches[-1] if matches else ""
            if url and url != current_url and healthy(url):
                address.parent.mkdir(parents=True, exist_ok=True)
                address.write_text(
                    json.dumps(
                        {
                            "url": url,
                            "server_pid": server.pid,
                            "tunnel_pid": tunnel.pid,
                            "manager_pid": os.getpid(),
                            "temporary": True,
                            "provider": provider,
                        }
                    )
                )
                address.chmod(0o600)
                current_url = url
                print("Read-only publication service ready.", flush=True)
            if not current_url and time.monotonic() > deadline:
                raise RuntimeError("Внешняя ссылка не отвечает. Проверьте .local/public-tunnel.log")
            time.sleep(1)
finally:
    cleanup()
