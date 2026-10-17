#!/usr/bin/env python3
"""Run the local frontend proxy and ../backend with matching wallet return URLs."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT.parent / 'backend'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    default_python = BACKEND / '.venv/bin/python'
    parser.add_argument('--backend-python', default=str(default_python) if default_python.exists() else sys.executable)
    parser.add_argument('--port', type=int, default=5173)
    parser.add_argument('--api-port', type=int, default=8000)
    parser.add_argument('--seed', action='store_true', help='Create demo users; requires DEV_ENDPOINTS_ENABLED=true')
    args = parser.parse_args()
    if args.port == args.api_port:
        parser.error('Frontend and API need different ports.')
    web_url, api_url = f'http://127.0.0.1:{args.port}', f'http://127.0.0.1:{args.api_port}'
    # Settings reads backend/.env for DB/auth/OP. Only local URL wiring is overridden.
    env = {**os.environ, 'APP_WEB_URL': web_url, 'PUBLIC_API_URL': api_url}
    processes = []

    def interrupt(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    try:
        api = subprocess.Popen([args.backend_python, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1',
                                '--port', str(args.api_port), '--workers', '1'], cwd=BACKEND, env=env)
        processes.append(api)
        deadline = time.monotonic() + 30
        while True:
            if api.poll() is not None:
                raise RuntimeError('El backend no arrancó. Revisa dependencias, Postgres y backend/.env (DATABASE_URL y JWT_SECRET).')
            try:
                with urlopen(api_url + '/api/v1/health', timeout=1) as response:
                    if json.load(response)['db'] == 'ok':
                        break
            except (URLError, TimeoutError):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError('El backend no respondió con una base de datos disponible en 30 segundos.')
            time.sleep(.2)
        if args.seed:
            request = Request(api_url + '/api/v1/dev/seed', data=b'{}', headers={'Content-Type': 'application/json'})
            with urlopen(request, timeout=60) as response:
                result = json.load(response)
            print(f'Demo preparada: {len(result["users"])} usuarios. Contraseña: DEMO_PASSWORD del backend.', flush=True)
        processes.append(subprocess.Popen([sys.executable, str(ROOT / 'scripts/serve.py'), '--port', str(args.port),
                                           '--api', api_url], cwd=ROOT))
        print(f'Frontend: {web_url} · API: {api_url}/docs · Retorno de wallet: {web_url}/op/return', flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(.5)
        raise RuntimeError('Uno de los servidores se detuvo; revisa su salida.')
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == '__main__':
    sys.exit(main())
