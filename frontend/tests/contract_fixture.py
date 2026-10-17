"""A contract-checked HTTP fixture for browser tests, never bundled with the app."""
import copy
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SPEC = json.loads((Path(__file__).parent / 'openapi.json').read_text())
SPEC_URI = 'https://tests.trust.invalid/openapi.json'
RESOURCE = Resource.from_contents({**SPEC, '$schema': 'https://json-schema.org/draft/2020-12/schema'})
REGISTRY = Registry().with_resource(SPEC_URI, RESOURCE)

def uid(number): return f'00000000-0000-4000-8000-{number:012d}'
def money(pesos): return {'value': str(pesos * 100), 'asset_code': 'COP', 'asset_scale': 2}

def resolve(schema):
    if '$ref' not in schema: return schema
    value = SPEC
    for key in schema['$ref'].removeprefix('#/').split('/'): value = value[key]
    return value

def sample(schema):
    schema = resolve(schema)
    if 'const' in schema: return schema['const']
    if 'enum' in schema: return schema['enum'][0]
    if 'oneOf' in schema: return sample(schema['oneOf'][0])
    kind = schema.get('type')
    if isinstance(kind, list): kind = kind[0]
    if kind == 'object': return {key: sample(schema['properties'][key]) for key in schema.get('required', [])}
    if kind == 'array': return [sample(schema['items']) for _ in range(schema.get('minItems', 0))]
    if kind == 'integer': return schema.get('minimum', 0)
    if kind == 'boolean': return False
    if kind == 'null': return None
    if kind == 'string':
        if schema.get('format') == 'uuid': return uid(99)
        if schema.get('format') == 'date-time': return '2026-10-17T12:00:00Z'
        if schema.get('format') == 'date': return '2026-10-17'
        if schema.get('format') == 'uri': return 'https://wallet.example.test/trust'
        if schema.get('format') == 'email': return 'cuenta@example.test'
        if schema.get('pattern') == '^(0|[1-9][0-9]*)$': return '0'
        return schema.get('examples', ['Prueba'])[0]
    raise ValueError(schema)

def entity(schema_name, **values):
    result = sample(SPEC['components']['schemas'][schema_name]); result.update(values); return result

def validate(schema, value):
    # Absolute base ensures internal OpenAPI references resolve from sub-schemas.
    validator = Draft202012Validator({'components': SPEC['components'], **schema})
    errors = list(validator.iter_errors(value))
    if errors: raise AssertionError('\n'.join(f'{list(e.path)}: {e.message}' for e in errors))

class Backend:
    def __init__(self, role='merchant'):
        self.role = role
        self.calls, self.failures, self.overrides = [], [], {}
        self.fail_login = False
        self.merchant = {'id': uid(1), 'business_name': 'La Esquina', 'city': 'Bucaramanga', 'initials': 'LE'}
        self.lender = {'id': uid(2), 'public_name': 'Aliado Capital'}
        self.user = entity('Me', id=uid({'merchant': 1, 'lender': 2, 'admin': 3}[role]), role=role,
            first_name='Marta', last_name='Ramírez', public_name='Aliado Capital' if role == 'lender' else 'Marta R.',
            business_name='La Esquina' if role == 'merchant' else None, city='Bucaramanga', email='cuenta@example.test',
            initials='AC' if role == 'lender' else 'LE', payout_wallet=None, collection_account=None, lender_range=None, unread_notifications=0)
        self.credit = entity('CreditProfile', eligible=True, score=742, grade='B', cupo_total=money(8000000), cupo_usado=money(0), cupo_disponible=money(8000000), reasons=[], unlocks_at=None)
        self.config = entity('Config', market_asset={'asset_code': 'COP', 'asset_scale': 2}, pct_options_bps=[1000,1500,2000], min_loan_amount=money(500000), amount_step=money(100000), cupo_cap=money(20000000), default_term_months=3, timezone='America/Bogota', dev_endpoints_enabled=False, example_sale=money(100000), example_splits=[{'pct_bps':p, 'merchant':money(100000-p*10), 'abono':money(p*10)} for p in [1000,1500,2000]])
        self.offers = [entity('LoanOffer', id=uid(10+i), lender={'id':uid(2+i), 'public_name':['Aliado Capital','Camila Moreno'][i]}, min_amount=money([5000000,3000000][i]), max_amount=money([5000000,3000000][i]), pct_bps=1000, fee_bps=500, term_months=3, proposal=None) for i in range(2)]
        self.requests = [entity('LoanRequest', id=uid(20), merchant=self.merchant, min_amount=money(3000000), max_amount=money(5000000), pct_bps=1000, credit=entity('CreditSummary', score=742, grade='B', cupo_total=money(8000000), cupo_disponible=money(8000000)))]
        self.loans = []
        self.repayments = []
        self.pct_history = []
        self.collection = None
        self.sales = [entity('SaleRow', sale_id=uid(70), amount=money(100000), merchant_amount=money(90000), abono_amount=money(10000), payout_status='completed')]

    def loan(self, status='pending_disbursement', number=30):
        terms = entity('Terms', principal=money(5000000), fee_bps=500, fee_amount=money(250000), total_repayable=money(5250000), trust_share_bps=2000, term_months=3, monthly_minimum=money(1750000), pct_initial_bps=1000, pct_min_bps=1000, pct_max_bps=2000)
        return entity('Loan', id=uid(number), status=status, origin='offer_taken', merchant=self.merchant, lender=self.lender,
            request_id=None, offer_id=uid(10), terms=terms, pct_bps=1000, allocated=money(0), delivered=money(0), outstanding=money(5250000), pct_paid_bps=0,
            period=None, is_late=False, missed_periods=0, next_action={'type':'accept' if status=='pending_acceptance' else 'disburse' if status=='pending_disbursement' else 'authorize_in_wallet' if status=='disbursing' else 'none','actor':'merchant' if status=='pending_acceptance' else 'lender' if status in ['pending_disbursement','disbursing'] else None},
            disbursement={'status':'completed' if status in ['active','paid'] else 'in_progress' if status=='disbursing' else 'not_started', 'authorization_url':'https://wallet.example.test/authorize' if status=='disbursing' else None, 'authorization_expires_at':None, 'attempt':0},
            expires_at='2026-10-18T12:00:00Z' if status.startswith('pending') else None, accepted_at=None, activated_at='2026-10-17T12:00:00Z' if status in ['active','paid'] else None, paid_at=None)

    def install(self, page): page.route('**/api/v1/**', self.handle)

    def handle(self, route):
        request = route.request
        parsed = urlsplit(request.url); query = {k:v[-1] for k,v in parse_qs(parsed.query).items()}
        body = request.post_data_json if request.post_data else None
        operation = None
        try:
            for path, methods in SPEC['paths'].items():
                match = re.fullmatch(re.sub(r'\{[^}]+\}', '([^/]+)', path), parsed.path)
                if match and request.method.lower() in methods:
                    operation = methods[request.method.lower()]; break
            assert operation, f'Unexpected endpoint: {request.method} {parsed.path}'
            name = operation['operationId']; self.calls.append((name, query, body))
            if operation.get('security') != []: assert request.headers.get('authorization') == 'Bearer test-token', 'Missing bearer token'
            if 'requestBody' in operation: validate(operation['requestBody']['content']['application/json']['schema'], body)
            for raw in operation.get('parameters', []):
                param = resolve(raw)
                if param['in'] != 'query': continue
                if param.get('required'): assert param['name'] in query, param['name']
                if param['name'] in query:
                    value = query[param['name']]; kind = resolve(param['schema']).get('type')
                    if kind == 'integer': value = int(value)
                    if kind == 'boolean': assert value in ['true','false']; value = value == 'true'
                    validate(param['schema'], value)
            status, result = self.respond(name, query, body, parsed.path)
            response = resolve(operation['responses'][str(status)])
            if 'content' in response: validate(response['content']['application/json']['schema'], result)
            route.fulfill(status=status, content_type='application/json', body=json.dumps(result))
        except Exception as error:
            self.failures.append(f'{operation and operation.get("operationId")}: {error}')
            route.fulfill(status=500, content_type='application/json', body=json.dumps({'error':{'code':'fixture_failure','message':'Falló la validación del contrato en la prueba.','details':{}}}))

    def respond(self, op, q, body, path):
        if op in self.overrides: return self.overrides[op](q, body, path)
        if op in ['login','register']:
            if self.fail_login: return 401, {'error':{'code':'unauthorized','message':'Correo o contraseña incorrectos.','details':{}}}
            return (201 if op=='register' else 200), {'access_token':'test-token','token_type':'bearer','user':self.user}
        if op=='getMe': return 200, self.user
        if op=='updateMe': self.user.update(body); return 200, self.user
        if op=='getConfig': return 200, self.config
        if op=='getMyCreditProfile': return 200, self.credit
        if op=='getMerchantCreditProfile': return 200, entity('MerchantCreditView', merchant=self.merchant, cupo_total=money(8000000), cupo_disponible=money(8000000))
        if op=='listNotifications': return 200, {'unread_count':0,'items':[],'next_offset':None}
        if op=='readAllNotifications': return 200, {'unread_count':0,'updated':0}
        if op=='listLoanOffers':
            rows = self.offers if q['scope']=='market' or self.role=='lender' else []
            return 200, {'items':rows,'next_offset':None,'count':len(rows)}
        if op=='listLoanRequests': return 200, {'items':self.requests,'next_offset':None}
        if op=='listLoans':
            fields=SPEC['components']['schemas']['LoanSummary']['properties']
            rows = [{k:v for k,v in {**l, 'principal':l['terms']['principal']}.items() if k in fields} for l in self.loans]
            return 200, {'items':rows,'next_offset':None}
        if op in ['getLoanOffer','getLoanRequest']: return 200, next(x for x in (self.offers if op=='getLoanOffer' else self.requests) if x['id']==path.split('/')[-1])
        if op in ['cancelLoanOffer','cancelLoanRequest']:
            item=next(x for x in (self.offers if op=='cancelLoanOffer' else self.requests) if x['id']==path.split('/')[-2])
            item['status']='cancelled'
            result=copy.deepcopy(item)
            if op=='cancelLoanRequest': result['merchant']=result['credit']=None
            return 200,result
        if op in ['createLoanRequest','createLoanOffer']:
            offer = op=='createLoanOffer'; result = copy.deepcopy((self.offers if offer else self.requests)[0]); result.update(body); result['id']=uid(25)
            if not offer: result['merchant']=result['credit']=None
            (self.offers if offer else self.requests).append(result)
            return 201, result
        if op=='previewLoan':
            loan = self.loan(); return 200, entity('LoanPreview', origin=loan['origin'], request_id=body.get('request_id'), offer_id=body.get('offer_id'), merchant=self.merchant, lender=self.lender, terms=loan['terms'], example={'sale':money(100000),'merchant_amount':money(90000),'abono':money(10000),'trust_cut':money(2000),'lender_net':money(8000)}, expires_in_hours=24)
        if op=='createLoan':
            loan=self.loan('pending_acceptance' if self.role=='lender' else 'pending_disbursement'); self.loans.append(loan); return 201, loan
        if op=='getLoan': return 200, next(l for l in self.loans if l['id']==path.split('/')[-1])
        if op=='getLoanPctHistory': return 200, {'items':self.pct_history}
        if op in ['acceptLoan','rejectLoan','cancelLoan','disburseLoan']:
            current=next(l for l in self.loans if l['id']==path.split('/')[-2]); status={'acceptLoan':'pending_disbursement','rejectLoan':'rejected','cancelLoan':'cancelled','disburseLoan':'disbursing'}[op]
            current.update(self.loan(status))
            if op=='disburseLoan': return 200, {'status':'disbursing','authorization_url':'https://wallet.example.test/authorize','authorization_expires_at':'2026-10-17T13:00:00Z'}
            return 200, current
        if op=='setPayoutWallet':
            self.user['payout_wallet']={'url':'https://wallet.example.test/my-wallet','public_name':'Mi wallet','asset_code':'COP','asset_scale':2}; return 200, self.user
        if op=='createCollectionAccount':
            self.collection=entity('CollectionAccount', id=uid(60),wallet_address='https://wallet.example.test/collection',qr_payload='https://pay.example.test/collection',asset=self.config['market_asset'],status='active',balance=money(450000),revoked_at=None)
            return 201, self.collection
        if op=='getCollectionAccount':
            if not self.collection: return 404, {'error':{'code':'not_found','message':'Activa tu cuenta de recaudo.','details':{}}}
            return 200, self.collection
        if op=='listSales': return 200, {'items':self.sales,'next_offset':None}
        if op=='getSale':
            result=entity('SaleDetail')
            result['sale'].update(id=uid(70),amount=money(100000),paid_at='2026-10-17T12:00:00Z',bre_b_reference='BREB-TEST-1',source='live',status='processed')
            result['repayment'].update(id=uid(71),loan_id=uid(30),abono=money(10000),trust_cut=money(2000),lender_net=money(8000),pct_applied_bps=1000)
            return 200,result
        if op=='getSalesToday': return 200, entity('SalesToday',date='2026-10-17',live=True,summary={'sales_total':money(100000),'sales_count':1,'merchant_total':money(90000),'abonado_total':money(10000)},rule={'merchant_bps':9000,'lender_bps':1000},wallet_today=money(90000),rows=self.sales,next_since='2026-10-17T12:00:00.123456Z')
        if op=='getLenderDashboard': return 200, entity('LenderDashboard', greeting={'first_name':'Marta','public_name':'Aliado Capital'},totals=[{'asset_code':'COP','asset_scale':2,'total_prestado':money(5000000),'abonos_hoy':{'gross':money(10000),'trust_cut':money(2000),'net':money(8000)},'recibido_hoy':money(8000)}],mi_rango=None)
        if op=='listRepayments': return 200, {'items':[row for row in self.repayments if not q.get('loan_id') or row['loan_id']==q['loan_id']],'next_offset':None}
        if op=='getRepaymentTrace':
            repayment=next(row for row in self.repayments if row['id']==path.split('/')[-1])
            result=entity('RepaymentTrace',id=repayment['id'],payouts=[])
            result['sale'].update(id=repayment['sale_id'],amount=money(100000),paid_at=repayment['paid_at'],bre_b_reference='BREB-TEST-1')
            result['repayment'].update(abono=repayment['abono'],trust_cut=repayment['trust_cut'],lender_net=repayment['lender_net'],pct_applied_bps=repayment['pct_applied_bps'],allocated_before=money(0),allocated_after=repayment['abono'])
            result['loan'].update(id=repayment['loan_id'],status='active',pct_bps=repayment['pct_applied_bps'],merchant=self.merchant,lender=self.lender,principal=money(5000000),total_repayable=money(5250000),allocated_total=repayment['abono'],delivered_total=repayment['abono'])
            return 200,result
        if op=='getAdminDashboard':
            return 200, entity('AdminDashboard', trust_revenue=[money(5000)],trust_pending=[money(1000)],retained_funds=[money(0)],in_flight=[money(0)])
        raise AssertionError(f'No fixture implementation for {op}')
