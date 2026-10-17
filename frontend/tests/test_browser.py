"""Run: playwright-python -m unittest discover -s tests -p 'test_browser.py' -v"""
import json
import os
import unittest
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from contract_fixture import Backend, uid, money, entity

BASE = os.environ.get('TRUST_TEST_URL', 'http://127.0.0.1:5173')
ARTIFACTS = Path('test-results')

class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ARTIFACTS.mkdir(exist_ok=True)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()
        class QuietHandler(SimpleHTTPRequestHandler):
            def log_message(self, *args): pass
        cls.reference_server = ThreadingHTTPServer(('127.0.0.1',0), partial(QuietHandler,directory=str(Path(__file__).resolve().parents[1])))
        threading.Thread(target=cls.reference_server.serve_forever,daemon=True).start()
        cls.reference_url = f'http://127.0.0.1:{cls.reference_server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop()
        cls.reference_server.shutdown(); cls.reference_server.server_close()

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width':1440, 'height':1000}, locale='es-CO', timezone_id='America/Bogota', reduced_motion='reduce')
        self.context.tracing.start(screenshots=True, snapshots=True)
        self.page = self.context.new_page(); self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.on('response', lambda response: self.errors.append(f'Asset {response.status}: {response.url}') if response.status >=400 and '/api/v1/' not in response.url else None)
        self.backend = Backend(); self.backend.install(self.page)

    def tearDown(self):
        self.page.screenshot(path=str(ARTIFACTS / f'{self._testMethodName}.png'), full_page=True)
        self.context.tracing.stop(path=str(ARTIFACTS / f'{self._testMethodName}.zip'))
        self.context.close()
        self.assertEqual(self.backend.failures, [], '\n'.join(self.backend.failures))
        self.assertEqual(self.errors, [], '\n'.join(self.errors))

    def authenticated(self, role='merchant'):
        self.backend.role = role
        other = Backend(role); self.backend.user = other.user
        self.context.add_init_script("sessionStorage.setItem('trust.session.v1', 'test-token')")

    def visit(self, path):
        self.page.goto(BASE + path)
        expect(self.page.locator('main')).to_be_visible()
        expect(self.page.locator('#app-error')).to_have_count(0)

    def test_login_failure_then_real_role_redirect(self):
        self.backend.fail_login = True
        self.visit('/autenticacion/index.html')
        self.page.get_by_label('Correo electrónico').fill('cuenta@example.test')
        self.page.get_by_label('Contraseña', exact=True).fill('incorrecta')
        self.page.get_by_role('button', name='Iniciar sesión', exact=True).click()
        expect(self.page.locator('#error-summary')).to_have_text('Correo o contraseña incorrectos.')
        self.backend.fail_login = False
        self.page.get_by_role('button', name='Iniciar sesión', exact=True).click()
        expect(self.page).to_have_url(BASE + '/portal-prestamos/index.html')
        expect(self.page.locator('.offer-row')).to_have_count(2)
        expect(self.page.locator('.credit-amount')).to_contain_text('$8.000.000')
        self.assertEqual(self.page.evaluate('localStorage.length'), 0)

    def test_registration_contract_and_password_validation(self):
        self.visit('/autenticacion/registro.html')
        self.page.get_by_role('radio', name='Soy prestatario').check()
        for label,value in [('Nombres','Marta'),('Apellidos','Ramírez'),('Ciudad','Bucaramanga'),('Nombre del negocio','La Esquina'),('Correo electrónico','cuenta@example.test'),('Contraseña','segura123'),('Confirmar contraseña','segura123')]: self.page.get_by_label(label, exact=True).fill(value)
        self.page.get_by_role('button', name='Crear cuenta', exact=True).click()
        expect(self.page).to_have_url(BASE + '/portal-prestamos/index.html')
        body=next(body for op,_,body in self.backend.calls if op=='register')
        self.assertEqual(body['role'],'merchant'); self.assertEqual(body['city'],'Bucaramanga')
        self.assertNotIn('confirm-password',body)

    def test_protected_route_and_role_guard(self):
        self.page.goto(BASE + '/wallet/index.html')
        expect(self.page).to_have_url(BASE + '/autenticacion/index.html?next=%2Fwallet%2Findex.html')
        self.assertEqual(self.backend.calls, [])
        self.authenticated('lender')
        self.page.goto(BASE + '/crear-prestamo/solicitar.html')
        expect(self.page).to_have_url(BASE + '/portal-prestamos/prestamista.html')
        expect(self.page.locator('.offer-row')).to_have_count(1)
        self.assertFalse(any(op=='getMyCreditProfile' for op,_,_ in self.backend.calls))

    def test_request_validation_and_publication(self):
        self.authenticated(); self.visit('/crear-prestamo/solicitar.html')
        self.page.get_by_label('Monto máximo', exact=True).fill('9.000.000')
        self.page.get_by_role('button', name='Revisar solicitud').click()
        expect(self.page.locator('#amount-max-error')).to_have_text('El monto supera tu cupo disponible.')
        self.page.get_by_label('Monto máximo', exact=True).fill('5.000.000')
        self.page.get_by_role('button', name='Revisar solicitud').click()
        expect(self.page.locator('#review-step')).to_be_visible()
        self.page.get_by_role('button', name='Publicar solicitud', exact=True).click()
        expect(self.page).to_have_url(BASE + '/portal-prestamos/index.html#historial')
        body=next(body for op,_,body in self.backend.calls if op=='createLoanRequest')
        self.assertEqual(body, {'min_amount':money(3000000),'max_amount':money(5000000),'pct_bps':1000})

    def test_offer_publication_uses_fixed_fee(self):
        self.authenticated('lender'); self.visit('/crear-prestamo/ofrecer.html')
        self.page.get_by_label('Cargo fijo sobre el capital', exact=True).fill('5,25')
        self.page.locator('#term').fill('25')
        self.page.get_by_role('button', name='Revisar oferta').click()
        expect(self.page.locator('#term-error')).to_contain_text('1 y 24')
        self.page.locator('#term').fill('3')
        self.page.get_by_role('button', name='Revisar oferta').click()
        self.page.get_by_role('button', name='Publicar oferta', exact=True).click()
        expect(self.page).to_have_url(BASE + '/portal-prestamos/prestamista.html#historial')
        body=next(body for op,_,body in self.backend.calls if op=='createLoanOffer')
        self.assertEqual(body['fee_bps'],525); self.assertEqual(body['term_months'],3)

    def test_preview_create_waits_for_disbursement(self):
        self.authenticated(); self.visit('/detalle-prestamo/oferta.html?id='+uid(10))
        self.page.get_by_role('button', name='Ver condiciones completas').click()
        expect(self.page.locator('#detail-note')).to_contain_text('$5.250.000')
        self.page.locator('#accept-terms').check()
        self.page.locator('#primary-action').click()
        expect(self.page.locator('#confirm-dialog')).to_be_visible()
        expect(self.page.locator('#confirmation-terms')).to_contain_text('Plazo3 meses')
        self.page.locator('#commit-action').click()
        expect(self.page).to_have_url(BASE + '/detalle-prestamo/oferta.html?loan_id='+uid(30))
        expect(self.page.locator('#detail-state')).to_have_text('Por desembolsar')
        expect(self.page.locator('#movement-section')).to_be_hidden()
        self.assertFalse(any(op=='disburseLoan' for op,_,_ in self.backend.calls))

    def test_accept_and_reject_pending_loan(self):
        self.authenticated(); self.backend.loans=[self.backend.loan('pending_acceptance')]
        self.visit('/detalle-prestamo/oferta.html?loan_id='+uid(30))
        self.page.locator('#accept-terms').check(); self.page.locator('#primary-action').click(); self.page.locator('#commit-action').click()
        expect(self.page.locator('#detail-state')).to_have_text('Por desembolsar')
        expect(self.page.locator('#movement-section')).to_be_hidden()
        self.backend.loans=[self.backend.loan('pending_acceptance')]
        self.page.reload(); self.page.locator('#reject-action').click(); self.page.locator('#commit-action').click()
        expect(self.page.locator('#detail-state')).to_have_text('Rechazado')

    def test_lender_disbursement_opens_wallet(self):
        self.authenticated('lender'); self.backend.loans=[self.backend.loan()]
        self.page.route('https://wallet.example.test/authorize', lambda route: route.fulfill(content_type='text/html', body='<h1>Autorización de wallet</h1>'))
        self.visit('/detalle-prestamo/oferta.html?loan_id='+uid(30))
        self.page.locator('#accept-terms').check(); self.page.locator('#primary-action').click(); self.page.locator('#commit-action').click()
        expect(self.page).to_have_url('https://wallet.example.test/authorize')
        body=next(body for op,_,body in self.backend.calls if op=='disburseLoan'); self.assertEqual(body,{'client':'web'})

    def test_detail_split_uses_current_percentage_and_keeps_progress_markup(self):
        self.authenticated(); self.backend.loans=[self.backend.loan('active')]
        self.backend.loans[0]['pct_bps']=2000
        self.visit('/detalle-prestamo/oferta.html?loan_id='+uid(30))
        expect(self.page.locator('#detail-share')).to_have_text('20 %')
        expect(self.page.locator('#split-business')).to_have_text('$80.000')
        expect(self.page.locator('#split-loan')).to_have_text('$20.000')
        expect(self.page.locator('#split-business-bar')).to_have_attribute('style','width: 80%;')
        expect(self.page.locator('#progress-signed > span')).to_have_text('3')
        expect(self.page.locator('#amount-label')).to_have_text('Capital desembolsado')
        self.backend.loans[0]['pct_bps']=1250
        self.page.reload()
        expect(self.page.locator('#split-business')).to_have_text('87,5 %')
        expect(self.page.locator('#split-loan')).to_have_text('12,5 %')
        self.page.set_viewport_size({'width':360,'height':800})
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),360)

    def test_owners_can_cancel_publications_without_creating_loans(self):
        self.authenticated('lender')
        self.visit('/detalle-prestamo/oferta.html?id='+uid(10))
        expect(self.page.locator('#proposal-editor')).to_have_count(0)
        self.page.get_by_role('button',name='Cancelar publicación',exact=True).click()
        self.page.locator('#cancel-confirm').click()
        self.assertFalse(any(op=='cancelLoanOffer' for op,_,_ in self.backend.calls))
        self.page.get_by_role('button',name='Cancelar publicación',exact=True).click()
        self.page.locator('#commit-action').click()
        expect(self.page).to_have_url(BASE+'/portal-prestamos/prestamista.html#historial')
        expect(self.page.locator('#panel-historial')).to_be_visible()
        self.assertEqual(self.backend.offers[0]['status'],'cancelled')
        self.backend.role='merchant'; self.backend.user=Backend().user
        self.visit('/detalle-prestamo/solicitud.html?id='+uid(20))
        expect(self.page.locator('#amount-label')).to_have_text('Monto solicitado')
        expect(self.page.locator('#purpose-title')).to_have_text('Sobre esta solicitud')
        self.page.get_by_role('button',name='Cancelar publicación',exact=True).click()
        self.page.locator('#commit-action').click()
        expect(self.page).to_have_url(BASE+'/portal-prestamos/index.html#historial')
        self.assertEqual(self.backend.requests[0]['status'],'cancelled')
        self.assertFalse(any(op=='createLoan' for op,_,_ in self.backend.calls))

    def test_parties_can_cancel_pending_agreements_but_not_active_loans(self):
        self.authenticated()
        for role in ['merchant','lender']:
            self.backend.role=role; self.backend.user=Backend(role).user
            for status in ['pending_acceptance','pending_disbursement']:
                self.backend.loans=[self.backend.loan(status)]
                self.visit('/detalle-prestamo/oferta.html?loan_id='+uid(30))
                self.page.get_by_role('button',name='Cancelar acuerdo',exact=True).click()
                self.page.locator('#commit-action').click()
                expect(self.page.locator('#detail-state')).to_have_text('Cancelado')
                expect(self.page.locator('#movement-section')).to_be_hidden()
                expect(self.page.locator('#cancel-loan')).to_be_hidden()
            self.backend.loans=[self.backend.loan('active')]
            self.visit('/detalle-prestamo/oferta.html?loan_id='+uid(30))
            expect(self.page.locator('#cancel-loan')).to_be_hidden()
            expect(self.page.locator('#reject-action')).to_be_hidden()
            expect(self.page.locator('#primary-action')).to_be_hidden()

    def test_detail_return_opens_the_matching_pending_filter(self):
        self.authenticated()
        for status,inbox in [('pending_acceptance','pending'),('pending_disbursement','waiting'),('rejected','resolved')]:
            self.backend.loans=[self.backend.loan(status)]
            self.visit('/detalle-prestamo/oferta.html?loan_id='+uid(30))
            self.page.locator('#history-link').click()
            expect(self.page).to_have_url(BASE+'/portal-prestamos/index.html?pendientes='+inbox+'#recibidas')
            expect(self.page.locator('[data-received-filter="'+inbox+'"]')).to_have_attribute('aria-pressed','true')
            expect(self.page.locator('#inbox-list .received-card')).to_have_count(1)
            expect(self.page.locator('#inbox-list')).to_contain_text(uid(30))

    def test_responding_shows_next_pending_and_updates_counter(self):
        self.authenticated(); self.backend.loans=[self.backend.loan('pending_acceptance',i) for i in [30,31]]
        self.visit('/detalle-prestamo/oferta.html?loan_id='+uid(30))
        expect(self.page.locator('.pending-count')).to_have_text('2')
        self.page.locator('#accept-terms').check()
        self.page.locator('#primary-action').click(); self.page.locator('#commit-action').click()
        expect(self.page.locator('#response-message')).to_have_text('Aceptaste las condiciones. El prestamista debe autorizar el desembolso.')
        expect(self.page.locator('.pending-count')).to_have_text('1')
        expect(self.page.locator('#movement-section')).to_be_hidden()
        self.page.get_by_role('link',name='Revisar siguiente pendiente',exact=True).click()
        expect(self.page).to_have_url(BASE+'/detalle-prestamo/oferta.html?loan_id='+uid(31))
        expect(self.page.locator('#detail-state')).to_have_text('Por aceptar')
        expect(self.page.locator('#accept-terms')).not_to_be_checked()

    def test_return_does_not_trust_query_status(self):
        self.authenticated('lender'); self.backend.loans=[self.backend.loan('disbursing')]
        self.visit('/op/return?loan_id='+uid(30)+'&op_status=completed&client=web')
        expect(self.page.locator('#return-status')).to_contain_text('confirmando')
        self.backend.loans=[self.backend.loan('active')]
        expect(self.page.locator('#return-status')).to_have_text('Al día', timeout=8000)

    def test_wallet_return_survives_web_login(self):
        self.backend.role='lender'; self.backend.user=Backend('lender').user
        self.backend.loans=[self.backend.loan('pending_disbursement')]
        target='/op/return?loan_id='+uid(30)+'&op_status=completed&client=web'
        self.page.goto(BASE+target)
        expect(self.page.locator('#auth-form')).to_be_visible()
        self.page.get_by_label('Correo electrónico').fill('cuenta@example.test')
        self.page.get_by_label('Contraseña',exact=True).fill('segura123')
        self.page.get_by_role('button',name='Iniciar sesión',exact=True).click()
        expect(self.page).to_have_url(BASE+target)
        expect(self.page.locator('#return-status')).to_have_text('Por desembolsar')

    def test_expired_session_restores_requested_loan_after_login(self):
        self.backend.role='lender'; self.backend.user=Backend('lender').user
        self.backend.loans=[self.backend.loan('active')]
        self.page.goto(BASE+'/autenticacion/index.html')
        self.page.evaluate("sessionStorage.setItem('trust.session.v1', 'test-token')")
        self.backend.overrides['getMe']=lambda *_: (401,{'error':{'code':'unauthorized','message':'La sesión venció.','details':{}}})
        target='/detalle-prestamo/oferta.html?loan_id='+uid(30)
        self.page.goto(BASE+target)
        expect(self.page.locator('#error-summary')).to_have_text('Tu sesión venció. Inicia sesión de nuevo.')
        self.assertIsNone(self.page.evaluate("sessionStorage.getItem('trust.session.v1')"))
        del self.backend.overrides['getMe']
        self.page.get_by_label('Correo electrónico').fill('cuenta@example.test')
        self.page.get_by_label('Contraseña',exact=True).fill('segura123')
        self.page.get_by_role('button',name='Iniciar sesión',exact=True).click()
        expect(self.page).to_have_url(BASE+target)
        expect(self.page.locator('#detail-state')).to_have_text('Al día')

    def test_mobile_wallet_return_without_web_session_links_to_native_app(self):
        self.visit('/op/return?loan_id='+uid(30)+'&op_status=completed&client=mobile')
        expect(self.page.locator('#return-status')).to_have_text('Vuelve a la app de Trust para consultar el estado confirmado del préstamo.')
        expect(self.page.get_by_role('link',name='Abrir en Trust')).to_have_attribute('href','trust://loans/'+uid(30))
        self.assertEqual(self.backend.calls,[])

    def test_wallet_link_qr_and_live_deduplication(self):
        self.authenticated(); self.visit('/wallet/index.html')
        self.page.get_by_label('Vincular mi wallet').fill('$wallet.example.test/my-wallet')
        self.page.get_by_role('button', name='Vincular wallet', exact=True).click()
        self.page.get_by_role('button', name='Activar mi QR').click()
        expect(self.page.locator('#payment-qr')).to_be_visible()
        expect(self.page.locator('#payment-qr')).to_have_attribute('src', re.compile('^data:image/'))
        expect(self.page.locator('#wallet-balance')).to_have_text('$450.000')
        self.page.get_by_role('button',name='Ocultar saldo').click()
        expect(self.page.locator('#wallet-balance')).to_have_text('••••••')
        expect(self.page.locator('#movement-rows tr')).to_have_count(2)
        self.page.wait_for_timeout(2800)
        expect(self.page.locator('#movement-rows tr')).to_have_count(2)
        self.assertTrue(any(op=='getSalesToday' and q.get('since')=='2026-10-17T12:00:00.123456Z' for op,q,_ in self.backend.calls))

    def test_admin_pagination_filter_export_detail(self):
        self.authenticated('admin'); self.backend.loans=[self.backend.loan('active',30+i) for i in range(8)]
        self.visit('/administrador/prestamos.html')
        expect(self.page.locator('#loan-rows tr')).to_have_count(6)
        self.page.get_by_role('button', name='Siguiente',exact=True).click()
        expect(self.page.locator('#loan-rows tr')).to_have_count(2)
        self.page.get_by_label('Buscar préstamo',exact=True).fill(uid(37))
        expect(self.page.locator('#loan-rows tr')).to_have_count(1)
        with self.page.expect_download() as info: self.page.get_by_role('button',name='Exportar listado').click()
        self.assertIn(uid(37),Path(info.value.path()).read_text())
        self.page.get_by_role('link',name='Ver detalle de '+uid(37)).click()
        expect(self.page.locator('#loan-detail')).to_contain_text('Capital desembolsado')
        self.page.locator('#back-loans').click()
        expect(self.page.locator('#loan-search')).to_have_value(uid(37))

    def test_mobile_layouts_navigation_and_errors(self):
        self.authenticated(); self.page.set_viewport_size({'width':390,'height':844})
        for path in ['/portal-prestamos/index.html','/crear-prestamo/solicitar.html','/detalle-prestamo/oferta.html?id='+uid(10),'/wallet/index.html']:
            self.visit(path)
            self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),390,path)
            self.page.get_by_role('button',name='Abrir menú',exact=True).click()
            expect(self.page.locator('#sidebar')).to_have_class(re.compile('is-open'))
            self.page.keyboard.press('Escape')
            expect(self.page.get_by_role('button',name='Abrir menú',exact=True)).to_be_focused()

    def test_network_error_does_not_show_demo_data(self):
        self.authenticated()
        self.backend.overrides['getMe']=lambda *_: (401, {'error':{'code':'unauthorized','message':'Sesión vencida.','details':{}}})
        self.page.goto(BASE+'/wallet/index.html')
        expect(self.page).to_have_url(BASE+'/autenticacion/index.html?expired=1&next=%2Fwallet%2Findex.html')
        expect(self.page.locator('#error-summary')).to_contain_text('sesión venció')

    def test_business_conflict_keeps_confirmation_open(self):
        self.authenticated(); self.backend.loans=[self.backend.loan('pending_acceptance')]
        self.backend.overrides['acceptLoan']=lambda *_: (409,{'error':{'code':'active_loan_exists','message':'Ya tienes un crédito activo.','details':{}}})
        self.visit('/detalle-prestamo/oferta.html?loan_id='+uid(30))
        self.page.locator('#accept-terms').check(); self.page.locator('#primary-action').click(); self.page.locator('#commit-action').click()
        expect(self.page.locator('#confirm-error')).to_have_text('Ya tienes un crédito activo.')
        expect(self.page.locator('#confirm-dialog')).to_be_visible()
        expect(self.page.locator('#detail-state')).to_have_text('Por aceptar')
        expect(self.page.locator('#commit-action')).to_be_enabled()

    def test_lender_wallet_does_not_invent_balance_or_collection(self):
        self.authenticated('lender'); self.visit('/wallet/prestamista.html')
        expect(self.page.locator('#wallet-balance')).to_have_text('—')
        expect(self.page.locator('.qr-section')).to_be_hidden()
        self.assertFalse(any(op in ['getCollectionAccount','getSalesToday','getMyCreditProfile'] for op,_,_ in self.backend.calls))
        expect(self.page.locator('#wallet-metrics')).to_contain_text('$5.000.000 COP')
        expect(self.page.locator('#wallet-metrics article')).to_have_count(3)
        expect(self.page.locator('#lender-summary')).to_be_visible()
        expect(self.page.locator('[data-earned]')).to_have_text('—')

    def test_lender_repayment_updates_to_received_wallet_currency(self):
        self.authenticated('lender'); self.page.clock.install()
        repayment=entity('Repayment',id=uid(71),sale_id=uid(70),loan_id=uid(30),merchant=self.backend.merchant,lender=self.backend.lender,abono=money(10000),trust_cut=money(2000),lender_net=money(8000),pct_applied_bps=1000,delivery={'status':'in_progress','received':None})
        self.backend.repayments=[repayment]
        self.visit('/wallet/prestamista.html')
        expect(self.page.locator('#movement-rows')).to_contain_text('$8.000 COP')
        expect(self.page.locator('#movement-rows')).to_contain_text('En proceso')
        repayment['delivery']={'status':'completed','received':{'value':'199','asset_code':'USD','asset_scale':2}}
        self.page.clock.fast_forward(30100)
        expect(self.page.locator('#movement-rows')).to_contain_text('1,99 USD')
        expect(self.page.locator('#movement-rows')).to_contain_text('Entregado')
        expect(self.page.locator('#wallet-balance')).to_have_text('—')

    def test_lender_detail_navigation_and_pending_badge(self):
        self.authenticated('lender'); self.backend.loans=[self.backend.loan()]
        self.visit('/detalle-prestamo/oferta.html?loan_id='+uid(30))
        expect(self.page.locator('.pending-count')).to_have_text('1')
        expect(self.page.locator('#counterparty-role')).to_have_text('Prestatario')
        expect(self.page.locator('.main-nav a').first).to_have_attribute('href','/portal-prestamos/prestamista.html')
        self.page.locator('.main-nav a').first.click()
        expect(self.page).to_have_url(BASE+'/portal-prestamos/prestamista.html')

    def test_notifications_read_and_backend_message_is_plain_text(self):
        self.authenticated()
        notification=entity('Notification',id=uid(80),type='loan_active',title='<img src=x onerror=alert(1)>',body='Crédito confirmado.',data={'loan_id':uid(30)},read_at=None)
        self.backend.overrides['listNotifications']=lambda *_: (200,{'unread_count':int(notification['read_at'] is None),'items':[notification],'next_offset':None})
        def read(*_):
            notification['read_at']='2026-10-17T13:00:00Z'
            return 200, notification
        self.backend.overrides['readNotification']=read
        self.visit('/portal-prestamos/index.html')
        self.page.get_by_role('button',name='Notificaciones: 1 sin leer').click()
        expect(self.page.locator('.app-notifications')).to_contain_text('<img src=x onerror=alert(1)>')
        expect(self.page.locator('.app-notifications img')).to_have_count(0)
        self.page.get_by_role('button',name='Marcar como leída',exact=True).click()
        expect(self.page.get_by_role('button',name='Marcar como leída',exact=True)).to_have_count(0)
        self.page.get_by_role('dialog').get_by_role('button',name='Cerrar',exact=True).click()
        expect(self.page.get_by_role('button',name='Notificaciones: 0 sin leer')).to_have_count(1)

    def test_paginated_admin_notifications_link_to_admin_details(self):
        self.authenticated('admin'); self.backend.loans=[self.backend.loan('active')]
        notices=[entity('Notification',id=uid(80+i),type='loan_active',title='Aviso '+str(i),body='Crédito confirmado.',data={'loan_id':uid(30)},read_at=None) for i in range(2)]
        def notices_page(q,*_):
            offset=int(q.get('offset',0))
            return 200,{'unread_count':2,'items':[notices[offset]],'next_offset':1 if offset==0 else None}
        self.backend.overrides['listNotifications']=notices_page
        self.visit('/administrador/index.html')
        self.page.get_by_role('button',name='Notificaciones: 2 sin leer').click()
        expect(self.page.locator('.app-notifications article')).to_have_count(2)
        self.page.get_by_role('link',name='Ver préstamo',exact=True).last.click()
        expect(self.page).to_have_url(BASE+'/administrador/prestamo.html?id='+uid(30))
        expect(self.page.get_by_role('heading',name='Detalle del préstamo',exact=True)).to_be_visible()

    def test_api_pagination_and_empty_market(self):
        self.authenticated()
        offsets=[]
        def offers(q,*_):
            if q['scope']=='mine': return 200,{'items':[],'count':0,'next_offset':None}
            offset=int(q.get('offset',0)); offsets.append(offset)
            return 200,{'items':[self.backend.offers[0 if offset==0 else 1]],'count':2,'next_offset':200 if offset==0 else None}
        self.backend.overrides['listLoanOffers']=offers
        self.visit('/portal-prestamos/index.html')
        expect(self.page.locator('.offer-row')).to_have_count(2); self.assertEqual(offsets,[0,200])
        self.backend.overrides['listLoanOffers']=lambda *_: (200,{'items':[],'count':0,'next_offset':None})
        self.page.get_by_role('button',name='Todas',exact=True).click()
        expect(self.page.locator('#market-empty')).to_be_visible()
        expect(self.page.locator('#offer-count')).to_have_text('0')

    def test_recovery_does_not_claim_email_was_sent(self):
        self.visit('/autenticacion/olvide-contrasena.html')
        expect(self.page.locator('.form-heading p')).to_contain_text('todavía no está disponible')
        expect(self.page.locator('#auth-form')).to_be_hidden()
        self.assertEqual(self.backend.calls,[])

    def test_admin_dashboard_has_no_fictitious_metrics(self):
        self.authenticated('admin'); self.visit('/administrador/index.html')
        expect(self.page.locator('#kpis .kpi')).to_have_count(4)
        expect(self.page.locator('#kpis')).to_contain_text('Préstamos desembolsados')
        expect(self.page.locator('#flujo')).to_contain_text('Información no disponible')
        expect(self.page.locator('.money-layout .destination')).to_have_count(2)
        expect(self.page.locator('.matching-layout #funnel li')).to_have_count(4)
        self.page.get_by_role('button',name='Cómo se distribuye').click()
        expect(self.page.get_by_role('dialog')).to_contain_text('Ingresos de Trust: $5.000 COP')
        self.page.get_by_role('button',name='Volver al panel',exact=True).click()
        self.page.set_viewport_size({'width':390,'height':844})
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),390)
        self.assertNotIn('184,32', self.page.locator('main').inner_text())

    def test_admin_detail_preserves_panels_and_reads_actual_distribution(self):
        self.authenticated('admin'); self.backend.loans=[self.backend.loan('active')]
        self.backend.repayments=[entity('Repayment',id=uid(71),sale_id=uid(70),loan_id=uid(30),merchant=self.backend.merchant,lender=self.backend.lender,abono=money(10000),trust_cut=money(2000),lender_net=money(8000),pct_applied_bps=1000,delivery={'status':'completed','received':money(8000)})]
        self.visit('/administrador/prestamo.html?id='+uid(30))
        expect(self.page.locator('.loan-agreement > div')).to_have_count(2)
        expect(self.page.locator('.loan-party .party-heading')).to_have_count(2)
        expect(self.page.locator('.sales-origin strong')).to_have_text('$100.000')
        expect(self.page.locator('.distribution-destinations > div').first).to_contain_text('$90.000')
        expect(self.page.locator('.payments-table tbody')).to_contain_text('$2.000')
        expect(self.page.locator('.loan-timeline li > div')).not_to_have_count(0)
        self.page.set_viewport_size({'width':360,'height':800})
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),360)

    def test_admin_unfunded_agreement_has_no_repayment_figures(self):
        self.authenticated('admin'); self.backend.loans=[self.backend.loan()]
        self.visit('/administrador/prestamo.html?id='+uid(30))
        expect(self.page.locator('.unfunded-panel')).to_be_visible()
        expect(self.page.locator('.repayment-panel')).to_have_count(0)
        expect(self.page.locator('.agreement-amount')).to_contain_text('Capital acordado')

    def test_admin_visible_rows_load_full_terms_and_retry_failed_detail(self):
        self.authenticated('admin'); self.backend.loans=[self.backend.loan('active',i) for i in range(30,38)]
        self.backend.loans[-1]['terms']['term_months']=19
        failing=True
        def detail(q,body,path):
            if failing and path.endswith(uid(30)):
                return 403,{'error':{'code':'forbidden','message':'No se puede consultar este préstamo.','details':{}}}
            return 200,next(loan for loan in self.backend.loans if path.endswith(loan['id']))
        self.backend.overrides['getLoan']=detail
        self.visit('/administrador/prestamos.html')
        expect(self.page.locator('.loan-financial-terms').nth(1)).to_have_text('5 % cargo fijo · 3 meses')
        expect(self.page.locator('.loan-outstanding').nth(1)).to_have_text('Saldo pendiente $5.250.000')
        retry=self.page.get_by_role('button',name='Reintentar condiciones de '+uid(30),exact=True)
        expect(retry).to_be_visible()
        self.assertEqual(len([op for op,_,_ in self.backend.calls if op=='getLoan']),6)
        failing=False; retry.click()
        expect(self.page.locator('.loan-financial-terms').first).to_have_text('5 % cargo fijo · 3 meses')
        self.page.locator('#loan-search').fill(uid(37))
        expect(self.page.locator('#loan-rows tr')).to_have_count(1)
        expect(self.page.locator('.loan-financial-terms')).to_have_text('5 % cargo fijo · 19 meses')
        self.assertEqual(len([op for op,_,_ in self.backend.calls if op=='getLoan']),8)
        self.page.set_viewport_size({'width':360,'height':800})
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),360)

    def test_admin_search_ignores_accents_and_stage_resets_state(self):
        self.authenticated('admin'); self.backend.loans=[self.backend.loan('active',30),self.backend.loan('pending_disbursement',31)]
        for loan in self.backend.loans: loan['merchant']={**loan['merchant'],'business_name':'Café Águila'}
        self.visit('/administrador/prestamos.html')
        self.page.locator('#loan-search').fill('CAFE AGUILA')
        expect(self.page.locator('#loan-rows tr')).to_have_count(2)
        self.page.locator('#loan-state').select_option('active')
        expect(self.page.locator('#loan-rows tr')).to_have_count(1)
        self.page.locator('[data-type="ofrecido"]').click()
        expect(self.page.locator('#loan-state')).to_have_value('todos')
        expect(self.page.locator('#loan-rows')).to_contain_text(uid(31))

    def test_history_type_resets_payment_state_and_keeps_search(self):
        self.authenticated(); self.backend.loans=[self.backend.loan('active')]
        self.visit('/portal-prestamos/index.html#historial')
        self.page.locator('#history-search').fill('ALIADO')
        self.page.locator('#history-state').select_option('pagado')
        expect(self.page.locator('#history-empty')).to_be_visible()
        self.page.locator('#history-type').select_option('solicitado')
        expect(self.page.locator('#history-state')).to_have_value('todos')
        self.page.locator('#history-type').select_option('firmado')
        expect(self.page.locator('#history-search')).to_have_value('ALIADO')
        expect(self.page.locator('.history-table tbody tr')).to_have_count(1)

    def test_visual_brand_panel_matches_reference_pixels(self):
        reference=self.context.new_page()
        reference.goto(self.reference_url+'/mockups/autenticacion/index.html')
        # Compare the product panel at identical screen coordinates. The review
        # toolbar is an editorial annotation and is excluded from both views.
        reference.locator('.review-bar').evaluate('(node) => node.remove()')
        self.visit('/autenticacion/index.html')
        expected=reference.locator('.brand-panel').screenshot(animations='disabled')
        actual=self.page.locator('.brand-panel').screenshot(animations='disabled')
        (ARTIFACTS/'brand-reference.png').write_bytes(expected); (ARTIFACTS/'brand-application.png').write_bytes(actual)
        self.assertEqual(actual,expected,'The brand panel differs from its original mockup.')
        reference.close()

    def test_mobile_all_roles_and_long_ids(self):
        self.authenticated('lender'); self.page.set_viewport_size({'width':360,'height':800})
        for path in ['/portal-prestamos/prestamista.html','/crear-prestamo/ofrecer.html','/detalle-prestamo/solicitud.html?id='+uid(20),'/wallet/prestamista.html']:
            self.visit(path)
            self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),360,path)
        self.backend.role='admin'; self.backend.user=Backend('admin').user; self.backend.loans=[self.backend.loan('active')]
        for path in ['/administrador/prestamos.html','/administrador/prestamo.html?id='+uid(30)]:
            self.visit(path)
            self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),360,path)

    def test_all_views_at_reference_breakpoints(self):
        output=ARTIFACTS/'layout-review'; output.mkdir(exist_ok=True)
        for width in [320,820,1920]:
            self.page.set_viewport_size({'width':width,'height':900})
            for path in ['index','registro','olvide-contrasena']:
                self.visit('/autenticacion/'+path+'.html')
                self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),width,path)
                self.page.screenshot(path=str(output/f'auth-{path}-{width}.png'),full_page=True)
        self.authenticated()
        self.backend.loans=[self.backend.loan('active')]
        self.backend.respond('createCollectionAccount',{},None,'')
        scenarios={
            'merchant':['/portal-prestamos/index.html','/crear-prestamo/solicitar.html','/detalle-prestamo/oferta.html?id='+uid(10),'/detalle-prestamo/oferta.html?loan_id='+uid(30),'/wallet/index.html'],
            'lender':['/portal-prestamos/prestamista.html','/crear-prestamo/ofrecer.html','/detalle-prestamo/solicitud.html?id='+uid(20),'/wallet/prestamista.html'],
            'admin':['/administrador/index.html','/administrador/prestamos.html','/administrador/prestamo.html?id='+uid(30)],
        }
        for role,paths in scenarios.items():
            self.backend.role=role; self.backend.user=Backend(role).user
            for width in [320,820,1920]:
                self.page.set_viewport_size({'width':width,'height':900})
                for index,path in enumerate(paths):
                    with self.subTest(role=role,width=width,path=path):
                        self.visit(path)
                        self.page.screenshot(path=str(output/f'{role}-{index}-{width}.png'),full_page=True)
                        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),width,path)
                        if role=='merchant' and path=='/wallet/index.html':
                            poster=self.page.locator('#qr-poster').bounding_box()
                            for logo in self.page.locator('.qr-brand img').all():
                                bounds=logo.bounding_box()
                                self.assertGreaterEqual(bounds['x'],poster['x'])
                                self.assertLessEqual(bounds['x']+bounds['width'],poster['x']+poster['width'])
                            if width==1920:
                                self.page.emulate_media(media='print')
                                expect(self.page.locator('#qr-poster')).to_be_visible()
                                expect(self.page.locator('.topbar')).to_be_hidden()
                                expect(self.page.locator('.wallet-overview')).to_be_hidden()
                                self.page.pdf(path=str(output/'merchant-qr.pdf'),format='A4',print_background=True)
                                self.page.emulate_media(media='screen')
    def test_lender_range_is_saved_and_sent_to_market_filter(self):
        self.authenticated('lender'); self.visit('/portal-prestamos/prestamista.html')
        self.page.get_by_role('button',name='Mi rango',exact=True).click()
        self.page.get_by_label('Monto mínimo (COP)',exact=True).fill('1.000.000')
        self.page.get_by_label('Monto máximo (COP)',exact=True).fill('6.000.000')
        self.page.get_by_role('button',name='Guardar rango').click()
        expect(self.page.get_by_role('dialog')).to_have_count(0)
        self.page.get_by_role('button',name='Dentro de mi rango',exact=True).click()
        expect(self.page.get_by_role('button',name='Dentro de mi rango',exact=True)).to_be_enabled()
        self.assertEqual(self.backend.user['lender_range'],{'min':money(1000000),'max':money(6000000)})
        self.assertTrue(any(op=='listLoanRequests' and q.get('in_my_range')=='true' for op,q,_ in self.backend.calls))

    def test_history_fetches_full_terms_and_excludes_unfunded_loans(self):
        self.authenticated(); self.backend.loans=[self.backend.loan('active',30),self.backend.loan('pending_disbursement',31)]
        self.visit('/portal-prestamos/index.html#historial')
        expect(self.page.locator('.history-table tbody tr')).to_have_count(1)
        expect(self.page.locator('.history-table tbody')).to_contain_text('5 %')
        self.page.get_by_role('tab',name=re.compile('Pendientes')).click()
        self.page.get_by_role('button',name='Esperando respuesta',exact=True).click()
        expect(self.page.locator('#inbox-list')).to_contain_text('3 meses')

    def test_sale_trace_and_movement_filters(self):
        self.authenticated(); self.visit('/wallet/index.html')
        self.page.get_by_role('button',name='Salidas',exact=True).click()
        expect(self.page.locator('#movement-rows tr')).to_have_count(1)
        self.page.get_by_role('button',name='Abono al crédito',exact=True).click()
        expect(self.page.get_by_role('dialog')).to_contain_text('Parte de Trust: $2.000 COP')
        self.page.get_by_role('dialog').get_by_role('button',name='Cerrar',exact=True).click()
        self.page.get_by_role('button',name='Entradas',exact=True).click()
        expect(self.page.locator('#movement-rows tr')).to_have_count(1)

    def test_publication_error_is_retryable_and_not_successful(self):
        self.authenticated(); self.visit('/crear-prestamo/solicitar.html')
        self.backend.overrides['createLoanRequest']=lambda *_: (409,{'error':{'code':'open_request_exists','message':'Ya tienes una solicitud abierta.','details':{}}})
        self.page.get_by_role('button',name='Revisar solicitud').click()
        self.page.get_by_role('button',name='Publicar solicitud',exact=True).click()
        expect(self.page.locator('#app-error')).to_have_text('Ya tienes una solicitud abierta.')
        expect(self.page.locator('#review-step')).to_be_visible()
        expect(self.page.get_by_role('button',name='Publicar solicitud',exact=True)).to_be_enabled()

    def test_preview_response_is_discarded_if_amount_changed_during_request(self):
        self.authenticated(); self.visit('/detalle-prestamo/oferta.html?id='+uid(10))
        pending=[]
        self.page.route('**/api/v1/loans/preview',lambda route: pending.append(route))
        with self.page.expect_request('**/api/v1/loans/preview'):
            self.page.get_by_role('button',name='Ver condiciones completas').click()
        self.page.get_by_label('Monto del acuerdo (COP)').fill('3.000.000')
        self.assertEqual(len(pending),1)
        self.backend.handle(pending[0])
        expect(self.page.get_by_role('button',name='Ver condiciones completas')).to_be_enabled()
        expect(self.page.locator('#primary-action')).to_be_hidden()
        expect(self.page.locator('#agreement-form')).to_be_hidden()
        self.assertFalse(any(op=='createLoan' for op,_,_ in self.backend.calls))

    def test_wallet_refreshes_settlement_balance_score_and_revoked_qr(self):
        self.authenticated(); self.page.clock.install()
        self.backend.user['payout_wallet']={'url':'https://wallet.example.test/merchant','public_name':'La Esquina','asset_code':'COP','asset_scale':2}
        self.backend.respond('createCollectionAccount',{},None,'')
        self.backend.sales[0]['payout_status']='in_progress'
        today=self.backend.respond('getSalesToday',{},None,'')[1]
        self.backend.overrides['getSalesToday']=lambda q,*_: (200,{**today,'rows':[] if q.get('since') else self.backend.sales})
        self.visit('/wallet/index.html')
        expect(self.page.locator('#payment-qr')).to_be_visible()
        expect(self.page.locator('#movement-rows')).to_contain_text('En proceso')
        self.backend.sales[0]['payout_status']='completed'; self.backend.collection['balance']=money(600000)
        self.backend.collection['status']='revoked'; self.backend.collection['revoked_at']='2026-10-17T14:00:00Z'
        self.backend.credit['score']=400
        self.page.clock.fast_forward(30100)
        expect(self.page.locator('#wallet-balance')).to_have_text('$600.000')
        expect(self.page.locator('#movement-rows')).to_contain_text('Entregado')
        expect(self.page.locator('#score-value')).to_have_text('400')
        expect(self.page.locator('.qr-section')).to_be_hidden()
        expect(self.page.locator('.wallet-state')).to_have_text('Revocada')

    def test_notifications_recover_after_initial_network_failure(self):
        self.authenticated(); self.page.clock.install()
        attempts=[]
        def flaky(route):
            attempts.append(1)
            if len(attempts)==1: route.abort()
            else: route.fallback()
        self.page.route('**/api/v1/notifications?*',flaky)
        self.visit('/portal-prestamos/index.html')
        self.page.get_by_role('button',name='Notificaciones',exact=True).click()
        expect(self.page.locator('#notifications-error')).to_contain_text('No pudimos conectar')
        self.page.clock.fast_forward(30100)
        expect(self.page.locator('#notifications-error')).to_be_hidden()
        expect(self.page.locator('.app-notifications')).to_have_text('No tienes notificaciones.')
        self.assertGreaterEqual(len(attempts),2)

    def test_polling_pauses_and_resumes_for_cached_page_lifecycle(self):
        self.page.clock.install(); self.visit('/autenticacion/index.html')
        self.page.evaluate("""async () => {
          const {polling} = await import('/lib/ui.js'); window.ticks = 0;
          window.stopTicks = polling(async () => { window.ticks++; }, 1000);
        }""")
        self.page.clock.fast_forward(1100)
        self.assertEqual(self.page.evaluate('window.ticks'),1)
        self.page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted:true}))")
        self.page.clock.fast_forward(5000)
        self.assertEqual(self.page.evaluate('window.ticks'),1)
        self.page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))")
        self.assertEqual(self.page.evaluate('window.ticks'),2)
        self.page.evaluate('window.stopTicks()'); self.page.clock.fast_forward(5000)
        self.assertEqual(self.page.evaluate('window.ticks'),2)

    def test_disbursement_poll_preserves_consent_and_stops_after_confirmation(self):
        self.authenticated('lender'); self.page.clock.install(); self.backend.loans=[self.backend.loan('disbursing')]
        self.visit('/detalle-prestamo/oferta.html?loan_id='+uid(30))
        self.page.locator('#accept-terms').check()
        self.page.clock.fast_forward(2100)
        expect(self.page.locator('#accept-terms')).to_be_checked()
        self.backend.loans=[self.backend.loan('active')]
        self.page.clock.fast_forward(2100)
        expect(self.page.locator('#detail-state')).to_have_text('Al día')
        previous=len([op for op,_,_ in self.backend.calls if op=='getLoan'])
        self.page.clock.fast_forward(5000)
        self.assertEqual(len([op for op,_,_ in self.backend.calls if op=='getLoan']),previous)

import re
if __name__=='__main__': unittest.main()
