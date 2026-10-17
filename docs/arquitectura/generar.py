"""Genera SVG y PNG de la arquitectura simplificada. Requiere cairosvg."""
from html import escape
from pathlib import Path

import cairosvg

HERE = Path(__file__).resolve().parent
W, H = 1920, 1080
BG, INK, MUTED = '#F8FAF8', '#183E39', '#617872'
TEAL, PURPLE, LINE = '#168576', '#77689D', '#D9E4DE'
parts = []


def add(value):
    parts.append(value)


def rect(x, y, w, h, fill, stroke='none', r=20):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}"/>')


def text(x, y, value, size=22, color=INK, weight=400, anchor='start'):
    add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}" text-anchor="{anchor}">{escape(value)}</text>')


def path(d, color=TEAL, marker=None, dash=False, both=False, width=2):
    attrs = f' marker-end="url(#{marker})"' if marker else ''
    if both:
        attrs += f' marker-start="url(#{marker})"'
    if dash:
        attrs += ' stroke-dasharray="6 8"'
    add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"{attrs}/>')


def icon(kind, x, y, color):
    add(f'<g transform="translate({x} {y})" fill="none" stroke="{color}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">')
    if kind == 'browser':
        add('<rect x="2" y="4" width="42" height="34" rx="5"/><path d="M2 15 H44 M10 10 H11 M17 10 H18 M16 23 L11 27 L16 31 M30 23 L35 27 L30 31"/>')
    elif kind == 'backend':
        add('<rect x="3" y="2" width="40" height="16" rx="4"/><rect x="3" y="26" width="40" height="16" rx="4"/><path d="M11 10 H12 M20 10 H35 M11 34 H12 M20 34 H35 M23 18 V26"/>')
    elif kind == 'payments':
        add('<path d="M4 13 H41 L31 3 M41 13 L31 23 M42 32 H5 L15 22 M5 32 L15 42"/>')
    elif kind == 'wallet':
        add('<path d="M6 12 V8 Q6 3 11 3 H36 V12 M6 12 H38 Q43 12 43 17 V36 Q43 41 38 41 H8 Q3 41 3 36 V17 Q3 12 6 12 M43 23 H30 V32 H43"/><circle cx="35" cy="27.5" r="1"/>')
    elif kind == 'db':
        add('<ellipse cx="22" cy="8" rx="17" ry="6"/><path d="M5 8 V33 C5 41 39 41 39 33 V8 M5 20 C5 28 39 28 39 20"/>')
    add('</g>')


def card(x, title, tech, detail, kind, external=False):
    color = PURPLE if external else TEAL
    rect(x, 361, 360, 300, '#E7EEE9' if not external else '#EEEBF2', r=24)
    rect(x, 354, 360, 300, 'white', LINE if not external else '#E0DCE9', r=24)
    rect(x+28, 382, 76, 76, '#EAF5EF' if not external else '#F1EEF8', r=22)
    icon(kind, x+43, 397, color)
    text(x+28, 510, title, 32, INK, 700)
    text(x+28, 550, tech, 22, color)
    text(x+28, 612, detail, 21, MUTED)


def database(x):
    path(f'M{x+180} 661 V733', '#A1B8AE')
    add(f'<circle cx="{x+180}" cy="733" r="4" fill="{TEAL}"/>')
    rect(x+30, 746, 300, 78, 'white', LINE, r=18)
    icon('db', x+52, 764, TEAL)
    text(x+116, 794, 'PostgreSQL', 25, INK, 600)


add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-labelledby="title desc">')
add('<title id="title">Arquitectura de Trust — vista simplificada</title>')
add('<desc id="desc">Frontend web conectado al backend FastAPI por REST. El backend usa PostgreSQL y la API interna del servicio Go op-service. El servicio Go usa su propia persistencia PostgreSQL y se conecta por HTTPS a wallets Open Payments. El navegador permite autorizar en la wallet. El webhook de vuelta al backend corresponde al simulador QR de Puntored/Bre-B. Se representa el modo OP_CLIENT=http, sin afirmar un despliegue o validación bancaria real.</desc>')
add('<defs>')
for name, color in [('arrow', TEAL), ('purple', PURPLE)]:
    add(f'<marker id="{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M2 1 L8 5 L2 9" fill="none" stroke="{color}" stroke-width="1.5"/></marker>')
add('</defs><g font-family="Liberation Sans, Arial, sans-serif">')
rect(0, 0, W, H, BG, r=0)

rect(80, 72, 36, 6, TEAL, r=3)
text(132, 84, 'TRUST', 21, TEAL, 700)
text(80, 163, 'Arquitectura de la aplicación', 56, INK, 700)
text(80, 207, 'De la experiencia web a los pagos.', 26, MUTED)
rect(1571, 116, 269, 46, '#EBF1EC', r=23)
text(1705, 146, 'Integración HTTP', 21, MUTED, 600, 'middle')

rect(56, 319, 1328, 363, '#EDF3EE', r=32)
text(82, 308, 'APLICACIÓN TRUST', 16, MUTED, 700)
text(1460, 337, 'EXTERNO', 16, PURPLE, 700)

# Navegación al consentimiento. El retorno se omite en esta vista resumida.
path('M260 354 V274 H1790 V354', PURPLE, 'purple', dash=True)
rect(754, 253, 412, 42, BG, r=0)
text(960, 282, 'Autorización en la wallet', 22, PURPLE, 500, 'middle')

card(80, 'Frontend', 'HTML · CSS · JavaScript', 'Portal de créditos y ventas', 'browser')
card(540, 'Backend', 'Python · FastAPI', 'Créditos y reparto de ventas', 'backend')
card(1000, 'Servicio de pagos', 'Go · op-service', 'Desembolsos y transferencias', 'payments')
card(1460, 'Wallets', 'Open Payments', 'Autorización y pagos', 'wallet', external=True)

for start, end, label in [(440, 540, 'REST'), (900, 1000, 'API interna'), (1360, 1460, 'HTTPS')]:
    text((start+end)/2, 462, label, 18, MUTED, 500, 'middle')
    path(f'M{start+8} 485 H{end-8}', TEAL, 'arrow', both=True, width=2.2)

text(950, 565, 'Webhook*', 17, MUTED, 500, 'middle')
path('M992 584 H908', TEAL, 'arrow')
database(540)
database(1000)

path('M80 923 H1840', LINE, width=1)
add(f'<circle cx="91" cy="968" r="5" fill="{TEAL}"/>')
text(111, 976, '* Puntored / Bre-B: simulador QR.', 23, INK)
text(1840, 976, 'Basado en el código del repositorio', 20, MUTED, anchor='end')
add('</g></svg>')

svg = '\n'.join(parts)
(HERE / 'arquitectura-trust.svg').write_text(svg, encoding='utf-8')
cairosvg.svg2png(bytestring=svg.encode(), write_to=str(HERE / 'arquitectura-trust.png'), scale=2)
print('Generados: arquitectura-trust.svg y arquitectura-trust.png (3840 × 2160).')
