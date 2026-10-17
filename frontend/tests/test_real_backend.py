"""Playwright + actual FastAPI/Postgres. No browser routes or API response fixtures.

Run with a Python 3.12 environment containing backend and tests/requirements.txt
dependencies. TRUST_E2E_PG_URL selects a Postgres maintenance connection; each
run creates and drops ONLY its own uniquely named database. OP uses FakeOpClient.
"""
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import psycopg
import yaml
from jsonschema import Draft202012Validator, FormatChecker
from playwright.sync_api import expect, sync_playwright
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT.parent / 'backend'
ARTIFACTS = ROOT / 'test-results' / 'real-backend'
SPEC = yaml.safe_load((ROOT.parent / 'trust-api.openapi.yaml').read_text())


def money(pesos):
    return {'value': str(pesos * 100), 'asset_code': 'COP', 'asset_scale': 2}


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class RealBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        cls.pg_url = os.environ.get('TRUST_E2E_PG_URL', 'postgresql://trust:trust@127.0.0.1:5433/postgres')
        cls.db_name = 'trust_frontend_e2e_' + uuid.uuid4().hex[:12]
        with psycopg.connect(cls.pg_url, autocommit=True) as connection:
            connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.db_name)))
        cls.addClassCleanup(cls.drop_database)
        api_port, web_port = free_port(), free_port()
        cls.api_url, cls.base = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{web_port}'
        # A temporary cwd prevents backend/.env from pointing tests at real services.
        temporary = tempfile.TemporaryDirectory(prefix='trust-e2e-')
        cls.addClassCleanup(temporary.cleanup)
        conn = conninfo_to_dict(make_conninfo(cls.pg_url, dbname=cls.db_name))
        from sqlalchemy.engine import URL
        database_url = URL.create('postgresql+psycopg', username=conn.get('user'), password=conn.get('password'),
                                  host=conn.get('host'), port=int(conn.get('port', 5432)), database=cls.db_name,
                                  query={k: v for k, v in conn.items() if k not in {'user', 'password', 'host', 'port', 'dbname'}})
        env = {k: v for k, v in os.environ.items() if k in {'PATH', 'HOME', 'LANG', 'LD_LIBRARY_PATH'}}
        env.update(DATABASE_URL=database_url.render_as_string(hide_password=False), JWT_SECRET=secrets.token_hex(32),
                   APP_WEB_URL=cls.base, PUBLIC_API_URL=cls.api_url, OP_CLIENT='fake', OP_FAKE_AUTO_COMPLETE='true',
                   DEV_ENDPOINTS_ENABLED='true', WORKER_ENABLED='true', WORKER_INTERVAL_SECONDS='0.2',
                   TRUST_REVENUE_WALLET_ADDRESS='https://ilp.interledger-test.dev/trust-e2e',
                   DEMO_PASSWORD='trust2026', PYTHONUNBUFFERED='1')
        cls.start_server('api', [sys.executable, '-m', 'uvicorn', 'app.main:app', '--app-dir', str(BACKEND),
                               '--host', '127.0.0.1', '--port', str(api_port), '--workers', '1'], temporary.name, env,
                         cls.api_url + '/api/v1/health')
        cls.start_server('frontend', [sys.executable, str(ROOT / 'scripts/serve.py'), '--port', str(web_port),
                                    '--api', cls.api_url], ROOT, os.environ.copy(), cls.base)
        cls.http = httpx.Client(base_url=cls.base, timeout=30)
        cls.addClassCleanup(cls.http.close)
        response = cls.http.post('/api/v1/dev/seed', json={})
        response.raise_for_status()
        cls.users = {u['role']: u for u in response.json()['users'] if u['email'] in
                     ['marta@demo.trust', 'camilo@demo.trust', 'admin@demo.trust']}
        cls.pw = sync_playwright().start()
        cls.addClassCleanup(cls.pw.stop)
        cls.browser = cls.pw.chromium.launch()
        cls.addClassCleanup(cls.browser.close)

    @classmethod
    def drop_database(cls):
        with psycopg.connect(cls.pg_url, autocommit=True) as connection:
            connection.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(cls.db_name)))

    @classmethod
    def start_server(cls, name, command, cwd, env, url):
        log = (ARTIFACTS / f'{name}.log').open('w')
        cls.addClassCleanup(log.close)
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)

        def stop():
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        cls.addClassCleanup(stop)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and process.poll() is None:
            try:
                response = httpx.get(url, timeout=1)
                if response.status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(.1)
        raise RuntimeError(f'{name} failed to start: {(ARTIFACTS / (name + ".log")).read_text()[-3000:]}')

    def setUp(self):
        self.contexts, self.errors, self.calls = [], [], []

    def tearDown(self):
        for role, context, har in self.contexts:
            for i, page in enumerate(context.pages):
                page.screenshot(path=str(ARTIFACTS / f'{self._testMethodName}-{role}-{i}.png'))
            context.tracing.stop(path=str(ARTIFACTS / f'{self._testMethodName}-{role}.zip'))
            context.close()
        (ARTIFACTS / f'{self._testMethodName}-contracts.json').write_text(json.dumps(self.calls, indent=2))
        self.assertEqual(self.errors, [], '\n'.join(self.errors))

    def validate(self, schema, value):
        Draft202012Validator({'components': SPEC['components'], **schema}, format_checker=FormatChecker()).validate(value)

    def inspect_request(self, entry):
        request, response = entry['request'], entry['response']
        path = urlsplit(request['url']).path
        if not path.startswith('/api/v1/'):
            return
        # Navigating away can cancel an in-flight GET before a response exists.
        if response['status'] <= 0:
            return
        try:
            route, methods = next((route, methods) for route, methods in SPEC['paths'].items()
                                  if re.fullmatch(re.sub(r'\{[^}]+\}', '[^/]+', route), path)
                                  and request['method'].lower() in methods)
            definition = methods[request['method'].lower()]
            names = re.findall(r'\{([^}]+)\}', route)
            values = re.fullmatch(re.sub(r'\{[^}]+\}', '([^/]+)', route), path).groups()
            path_params = dict(zip(names, values))
            query = parse_qs(urlsplit(request['url']).query)
            for parameter in definition.get('parameters', []):
                if '$ref' in parameter:
                    parameter = SPEC['components']['parameters'][parameter['$ref'].split('/')[-1]]
                name, kind = parameter['name'], parameter['in']
                value = path_params.get(name) if kind == 'path' else query.get(name, [None])[-1]
                if value is None:
                    self.assertFalse(parameter.get('required', False), f'Missing {name}')
                    continue
                schema = parameter['schema']
                if schema.get('type') == 'integer':
                    value = int(value)
                elif schema.get('type') == 'boolean':
                    self.assertIn(value, ['true', 'false'])
                    value = value == 'true'
                self.validate(schema, value)
            self.calls.append({'operation': definition['operationId'], 'status': response['status']})
            if 'requestBody' in definition:
                self.validate(definition['requestBody']['content']['application/json']['schema'], json.loads(request['postData']['text']))
            if definition.get('security', SPEC.get('security')):
                headers = {h['name'].lower(): h['value'] for h in request['headers']}
                self.assertTrue(headers.get('authorization', '').startswith('Bearer '))
            spec = definition['responses'].get(str(response['status']))
            self.assertIsNotNone(spec, f'Unexpected {response["status"]} for {definition["operationId"]}')
            if '$ref' in spec:
                spec = SPEC['components']['responses'][spec['$ref'].split('/')[-1]]
            content = spec.get('content', {}).get('application/json')
            if content:
                self.validate(content['schema'], json.loads(response['content']['text']))
            self.assertLess(response['status'], 500, f'{request["method"]} {path}: {response["status"]}')
        except Exception as error:
            self.errors.append(f'{request["method"]} {path}: {error}')

    def page(self, role, mobile=False):
        har = ARTIFACTS / f'{self._testMethodName}-{role}.har'
        context = self.browser.new_context(viewport={'width': 390 if mobile else 1440, 'height': 844 if mobile else 1000},
                                           locale='es-CO', timezone_id='America/Bogota', reduced_motion='reduce',
                                           record_har_path=har, record_har_content='embed')
        self.contexts.append((role, context, har))
        context.tracing.start(screenshots=True, snapshots=True)
        # Observe the REAL fetch response before application-triggered navigation
        # destroys Chromium's response buffer. No routing, fulfilling or replacement
        # of requests/responses; the original Response is returned unchanged.
        context.expose_binding('__auditTrustResponse', lambda source, entry: self.inspect_request(entry))
        context.add_init_script("""(() => {
          const fetch = window.fetch.bind(window);
          window.fetch = async (...args) => {
            const request = new Request(...args);
            const response = await fetch(...args);
            if (new URL(request.url).pathname.startsWith('/api/v1/')) {
              await window.__auditTrustResponse({
                request: {url: request.url, method: request.method,
                  headers: [...request.headers].map(([name, value]) => ({name, value})),
                  postData: {text: await request.text()}},
                response: {status: response.status, content: {text: await response.clone().text()}}
              });
            }
            return response;
          };
        })();""")
        page = context.new_page()
        page.on('pageerror', lambda error: self.errors.append(str(error)))
        page.on('response', lambda response: self.errors.append(f'Asset {response.status}: {response.url}')
                if response.status >= 400 and '/api/v1/' not in response.url else None)
        return page

    def login(self, page, role):
        page.goto(self.base + '/autenticacion/index.html')
        page.get_by_label('Correo electrónico').fill(self.users[role]['email'])
        page.get_by_label('Contraseña', exact=True).fill('trust2026')
        page.get_by_role('button', name='Iniciar sesión', exact=True).click()
        expect(page).to_have_url(re.compile('/(portal-prestamos|administrador)/'))

    def api(self, page, method, path, body=None):
        # Inspection only, except explicit local simulator setup below. Business actions use the UI.
        token = page.evaluate("sessionStorage.getItem('trust.session.v1')")
        response = self.http.request(method, '/api/v1' + path, headers={'Authorization': 'Bearer ' + token}, json=body)
        response.raise_for_status()
        return response.json()

    def confirm(self, page):
        page.locator('#accept-terms').check()
        page.locator('#primary-action').click()
        page.locator('#commit-action').click()

    def test_loan_lifecycle_sales_repayments_and_admin(self):
        merchant, lender, admin = self.page('merchant'), self.page('lender'), self.page('admin')
        self.login(merchant, 'merchant')
        expect(merchant.locator('.credit-amount')).to_contain_text('$6.000.000')
        self.login(lender, 'lender')

        # Publish a request; the lender proposes terms; the merchant accepts; cancel before funding.
        merchant.goto(self.base + '/crear-prestamo/solicitar.html')
        merchant.get_by_label('Monto mínimo', exact=True).fill('1.000.000')
        merchant.get_by_label('Monto máximo', exact=True).fill('2.000.000')
        merchant.locator('#repayment').select_option('1500')
        merchant.get_by_role('button', name='Revisar solicitud').click()
        merchant.get_by_role('button', name='Publicar solicitud', exact=True).click()
        expect(merchant).to_have_url(re.compile('#historial$'))
        request = self.api(merchant, 'GET', '/loan-requests?scope=mine')['items'][0]
        lender.reload()
        lender.locator(f'a[href$="id={request["id"]}"]').click()
        lender.get_by_role('button', name='Ver condiciones completas').click()
        self.confirm(lender)
        expect(lender.locator('#detail-state')).to_have_text('Por aceptar')
        loan_id = parse_qs(urlsplit(lender.url).query)['loan_id'][0]
        detail = '/detalle-prestamo/oferta.html?loan_id=' + loan_id
        merchant.goto(self.base + detail)
        self.confirm(merchant)
        expect(merchant.locator('#detail-state')).to_have_text('Por desembolsar')
        merchant.get_by_role('button', name='Cancelar acuerdo', exact=True).click()
        merchant.locator('#commit-action').click()
        expect(merchant.locator('#detail-state')).to_have_text('Cancelado')

        # Publish and take a new offer, using real marketplace data and preview terms.
        lender.goto(self.base + '/crear-prestamo/ofrecer.html')
        lender.get_by_label('Monto mínimo', exact=True).fill('1.000.000')
        lender.get_by_label('Monto máximo', exact=True).fill('2.000.000')
        lender.locator('#repayment').select_option('1500')
        lender.get_by_label('Cargo fijo sobre el capital', exact=True).fill('5')
        lender.get_by_role('button', name='Revisar oferta').click()
        lender.get_by_role('button', name='Publicar oferta', exact=True).click()
        expect(lender).to_have_url(re.compile('#historial$'))
        offer = next(o for o in self.api(lender, 'GET', '/loan-offers?scope=mine')['items'] if o['fee_bps'] == 500)
        merchant.goto(self.base + '/portal-prestamos/index.html')
        merchant.locator(f'a[href$="id={offer["id"]}"]').click()
        merchant.locator('#proposal-amount').fill('2.000.000')
        merchant.get_by_role('button', name='Ver condiciones completas').click()
        expect(merchant.locator('#detail-note')).to_contain_text('$2.100.000')
        self.confirm(merchant)
        expect(merchant.locator('#detail-state')).to_have_text('Por desembolsar')
        loan_id = parse_qs(urlsplit(merchant.url).query)['loan_id'][0]
        detail = '/detalle-prestamo/oferta.html?loan_id=' + loan_id

        # Reject once at the wallet; a retry must still succeed and preserve the frontend session.
        lender.goto(self.base + detail)
        self.confirm(lender)
        expect(lender.get_by_role('heading', name='Autorizar pago')).to_be_visible()
        lender.get_by_role('link', name='Rechazar', exact=True).click()
        expect(lender).to_have_url(re.compile(re.escape(self.base) + '/op/return\\?'))
        expect(lender.locator('#return-status')).to_have_text('Por desembolsar', timeout=15000)
        lender.get_by_role('link', name='Ver préstamo').click()
        self.confirm(lender)
        lender.get_by_role('link', name='Aprobar', exact=True).click()
        expect(lender.locator('#return-status')).to_have_text('Al día', timeout=15000)
        self.assertEqual(self.api(lender, 'GET', '/loans/' + loan_id)['status'], 'active')
        lender.screenshot(path=str(ARTIFACTS / 'wallet-return.png'), full_page=True)

        merchant.goto(self.base + '/wallet/index.html')
        expect(merchant.locator('#payment-qr')).to_have_attribute('src', re.compile('^data:image/'))
        merchant.get_by_label('Cambiar wallet vinculada', exact=True).fill('https://ilp.interledger-test.dev/change-blocked')
        merchant.get_by_role('button', name='Vincular wallet', exact=True).click()
        expect(merchant.locator('#app-error')).to_have_text('No puedes cambiar la wallet mientras tengas un crédito en curso.')
        expect(merchant.locator('#wallet-metrics')).to_contain_text('0 cobros de hoy', timeout=15000)
        simulated = self.http.post('/api/v1/dev/sales/simulate', json={
            'merchant_id': self.users['merchant']['id'], 'amount': money(48000)})
        simulated.raise_for_status()
        expect(merchant.locator('#wallet-metrics')).to_contain_text('$48.000', timeout=15000)
        expect(merchant.locator('#wallet-metrics')).to_contain_text('$7.200')
        expect(merchant.locator('#wallet-metrics')).to_contain_text('$40.800')
        merchant.get_by_role('button', name='Venta por QR Bre-B', exact=True).first.click()
        expect(merchant.get_by_role('dialog')).to_contain_text('Abono: $7.200 COP')
        merchant.get_by_role('dialog').get_by_role('button', name='Cerrar').click()
        merchant.screenshot(path=str(ARTIFACTS / 'merchant-live-sale.png'))
        lender.goto(self.base + '/wallet/prestamista.html')
        expect(lender.locator('#wallet-metrics')).to_contain_text('$7.200', timeout=15000)
        repayment = next(r for r in self.api(lender, 'GET', '/repayments?loan_id=' + loan_id)['items'])
        self.assertEqual(repayment['abono'], money(7200))
        self.assertEqual(int(repayment['trust_cut']['value']) + int(repayment['lender_net']['value']), 720000)
        lender.locator('#movement-rows tr').filter(has_text=repayment['id']).get_by_role('button').click()
        expect(lender.get_by_role('dialog')).to_contain_text('Venta: $48.000 COP')
        lender.get_by_role('dialog').get_by_role('button', name='Cerrar').click()
        lender.get_by_role('button', name=re.compile('^Notificaciones')).click()
        lender.get_by_role('button', name='Marcar todas como leídas', exact=True).click()
        expect(lender.get_by_role('button', name='Notificaciones: 0 sin leer')).to_be_visible()
        lender.get_by_role('dialog').get_by_role('button', name='Cerrar', exact=True).click()

        close = self.http.post(f'/api/v1/dev/loans/{loan_id}/close-period')
        close.raise_for_status()
        self.login(admin, 'admin')
        expect(admin.locator('#kpis')).to_contain_text('Préstamos desembolsados')
        admin.goto(self.base + '/administrador/prestamo.html?id=' + loan_id)
        expect(admin.locator('.distribution-panel')).to_contain_text('$48.000', timeout=15000)
        expect(admin.locator('.payments-panel')).to_contain_text('$7.200')
        expect(admin.locator('.percentage-history-panel')).to_contain_text('15 % →')
        admin.screenshot(path=str(ARTIFACTS / 'admin-loan.png'), full_page=True)
        admin.goto(self.base + '/administrador/prestamos.html')
        expect(admin.locator('tbody')).to_contain_text(loan_id)
        with admin.expect_download() as download:
            admin.locator('#export-loans').click()
        download.value.save_as(ARTIFACTS / 'loans.csv')
        self.assertIn(loan_id, (ARTIFACTS / 'loans.csv').read_text())
        # The final repayment is capped at the remaining balance; later sales
        # must go entirely to the merchant, and the UI must show the paid state.
        settled = self.http.post('/api/v1/dev/sales/simulate', json={
            'merchant_id': self.users['merchant']['id'], 'amount': money(20000000)})
        settled.raise_for_status()
        merchant.goto(self.base + detail)
        expect(merchant.locator('#detail-state')).to_have_text('Pagado')
        self.assertEqual(self.api(merchant, 'GET', '/loans/' + loan_id)['outstanding'], money(0))
        later = self.http.post('/api/v1/dev/sales/simulate', json={
            'merchant_id': self.users['merchant']['id'], 'amount': money(100000)})
        later.raise_for_status()
        today = self.api(merchant, 'GET', '/sales/today')
        self.assertEqual(today['rule']['merchant_bps'], 10000)
        self.assertEqual(today['summary']['abonado_total'], money(2100000))

    def test_registration_wallet_role_guards_and_mobile(self):
        for role in ['merchant', 'lender']:
            with self.subTest(role=role):
                page = self.page(role, mobile=True)
                page.goto(self.base + '/autenticacion/registro.html')
                page.get_by_role('radio', name='Soy prestatario' if role == 'merchant' else 'Soy prestamista').check()
                email = f'{role}-{uuid.uuid4().hex[:8]}@example.com'
                for label, value in [('Nombres', 'Prueba'), ('Apellidos', 'Integración'), ('Ciudad', 'Bogotá'),
                                     ('Correo electrónico', email), ('Contraseña', 'E2eTrust2026'),
                                     ('Confirmar contraseña', 'E2eTrust2026')]:
                    page.get_by_label(label, exact=True).fill(value)
                if role == 'merchant':
                    page.get_by_label('Nombre del negocio').fill('Comercio de integración')
                page.get_by_role('button', name='Crear cuenta', exact=True).click()
                expect(page).to_have_url(re.compile('/portal-prestamos/'))
                page.goto(self.base + '/administrador/index.html')
                expect(page).to_have_url(re.compile('/portal-prestamos/'))
                page.goto(self.base + '/wallet/' + ('index' if role == 'merchant' else 'prestamista') + '.html')
                page.get_by_label('Vincular mi wallet', exact=True).fill('https://ilp.interledger-test.dev/' + email.split('@')[0])
                page.get_by_role('button', name='Vincular wallet', exact=True).click()
                expect(page.locator('.form-surface [role="status"]')).to_have_text('Wallet vinculada.')
                if role == 'merchant':
                    page.get_by_role('button', name='Activar mi QR').click()
                    expect(page.locator('#payment-qr')).to_have_attribute('src', re.compile('^data:image/'))
                else:
                    expect(page.get_by_role('button', name='Activar mi QR')).to_be_hidden()
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 390)
                page.screenshot(path=str(ARTIFACTS / f'wallet-mobile-{role}.png'), full_page=True)
                page.locator('#open-menu').click()
                page.get_by_role('button', name='Cerrar sesión').click()
                expect(page).to_have_url(self.base + '/autenticacion/index.html')
                self.assertIsNone(page.evaluate("sessionStorage.getItem('trust.session.v1')"))

    def test_invalid_session_and_login_error(self):
        page = self.page('expired-session')
        page.goto(self.base + '/autenticacion/index.html')
        page.evaluate("sessionStorage.setItem('trust.session.v1', 'expired-invalid-token')")
        page.goto(self.base + '/wallet/index.html')
        expect(page.locator('#error-summary')).to_have_text('Tu sesión venció. Inicia sesión de nuevo.')
        self.assertIsNone(page.evaluate("sessionStorage.getItem('trust.session.v1')"))
        page.get_by_label('Correo electrónico').fill(self.users['merchant']['email'])
        page.get_by_label('Contraseña', exact=True).fill('incorrect-password')
        page.get_by_role('button', name='Iniciar sesión', exact=True).click()
        expect(page.locator('#error-summary')).to_contain_text(re.compile('correo|contraseña', re.I))
        page.get_by_label('Contraseña', exact=True).fill('trust2026')
        page.get_by_role('button', name='Iniciar sesión', exact=True).click()
        expect(page).to_have_url(self.base + '/wallet/index.html')
        expect(page.locator('#payment-qr')).to_have_attribute('src', re.compile('^data:image/'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
