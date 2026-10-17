#!/usr/bin/env python3
"""Start the full service with a temporary HTTPS URL and a private local API."""
import argparse
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent.parent


def tunnel_url(lines, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            line = lines.get(timeout=max(0.01, deadline - time.monotonic()))
        except queue.Empty:
            break
        if line is None:
            raise RuntimeError("El túnel terminó antes de obtener la URL HTTPS.")
        match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com\b", line)
        if match:
            return match[0]
    raise RuntimeError("No se obtuvo la URL del túnel en 60 segundos. Comprueba tu conexión.")


def stop(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Valida credenciales y configuración sin abrir el túnel ni ejecutar pagos")
    args = parser.parse_args()
    if not (ROOT / ".env").is_file():
        raise RuntimeError("Crea open-payments/.env a partir de .env.example y configura tus credenciales.")
    if not shutil.which("docker"):
        raise RuntimeError("Falta Docker con el plugin Compose.")
    compose = ["docker", "compose", "--env-file", str(ROOT / ".env"), "-f", "compose.yaml", "-f", "compose.demo.yaml"]
    env = os.environ.copy()
    env.update(PUBLIC_BASE_URL="https://demo-preflight.example", DEMO_MODE="true", COMPOSE_PROFILES="", ALLOWED_RETURN_URL_PREFIXES=os.environ.get("DEMO_RETURN_URL", ""))
    subprocess.run(compose + ["config", "--quiet"], cwd=ROOT, env=env, check=True)
    # Validate secrets without starting workers or making any financial request.
    subprocess.run(compose + ["run", "--rm", "--no-deps", "--build", "op-service", "check-config"], cwd=ROOT, env=env, check=True)
    if args.check:
        return
    cloudflared = shutil.which("cloudflared")
    if not cloudflared:
        bundled = ROOT / "bin" / "cloudflared"
        if bundled.is_file() and os.access(bundled, os.X_OK):
            cloudflared = str(bundled)
    if not cloudflared:
        raise RuntimeError("Falta cloudflared. Instalación: https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/")
    tunnel = None
    service = None
    try:
        # cloudflared receives no wallet secrets. Only the gateway is tunneled.
        tunnel = subprocess.Popen([cloudflared, "tunnel", "--url", "http://127.0.0.1:8081", "--no-autoupdate"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        lines = queue.Queue()

        def read_lines():
            for line in tunnel.stdout:
                if ".trycloudflare.com" in line:
                    lines.put(line)
            lines.put(None)

        threading.Thread(target=read_lines, daemon=True).start()
        public_url = tunnel_url(lines)
        env["PUBLIC_BASE_URL"] = public_url
        print(f"URL pública de esta sesión: {public_url}", flush=True)
        print("API privada: http://127.0.0.1:8080 · Ctrl+C detiene la demo.", flush=True)
        print("El retorno sin frontend es /v1/health; consulta el estado del pago para confirmar su resultado.", flush=True)
        subprocess.run(compose + ["config", "--quiet"], cwd=ROOT, env=env, check=True)
        service = subprocess.Popen(compose + ["up", "--build", "--abort-on-container-exit"], cwd=ROOT, env=env)
        while service.poll() is None:
            if tunnel.poll() is not None:
                raise RuntimeError("El túnel se desconectó. Reinicia la demo antes de nuevas autorizaciones.")
            time.sleep(0.5)
        if service.returncode:
            raise RuntimeError("Docker Compose no pudo iniciar o mantener la demo; revisa los errores anteriores.")
    finally:
        stop(service)
        stop(tunnel)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except (RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
