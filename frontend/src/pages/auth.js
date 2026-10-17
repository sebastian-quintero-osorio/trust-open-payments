import { api, session } from '../lib/api.js';
import { $, $$, el, text, show, busy } from '../lib/ui.js';
import { home } from '../app.js';
import { nextPage } from '../lib/navigation.js';

export async function init() {
  const form = $('#auth-form'), view = document.body.dataset.view;
  const next = new URLSearchParams(location.search).get('next');
  if (next) $$('a[href]', document).forEach(link => {
    const url = new URL(link.href);
    if (url.origin === location.origin && url.pathname.startsWith('/autenticacion/')) { url.searchParams.set('next', nextPage(next, '/portal-prestamos/index.html')); link.href = url.href; }
  });
  form.noValidate = true;
  show('#success-panel', false);
  if (new URLSearchParams(location.search).has('expired')) {
    text('#error-summary', 'Tu sesión venció. Inicia sesión de nuevo.'); show('#error-summary');
  }
  if (view === 'recovery') {
    // There is no recovery operation in v0.1.0; never claim an email was sent.
    text('.form-heading p', 'La recuperación de contraseña todavía no está disponible. Contacta al equipo de Trust para recuperar el acceso.');
    form.hidden = true;
    return;
  }
  if (view === 'register') {
    text('label[for="name"]', 'Nombres'); $('#name').autocomplete = 'given-name';
    for (const [id, label, autocomplete] of [['last-name', 'Apellidos', 'family-name'], ['city', 'Ciudad', 'address-level2']]) {
      const field = el('div', 'field'), wrap = el('div', 'input-wrap'), input = el('input');
      input.id = id; input.name = id; input.required = true; input.autocomplete = autocomplete; input.maxLength = 100;
      input.setAttribute('aria-describedby', `${id}-error`);
      const title = el('label', '', label); title.htmlFor = id;
      const error = el('p', 'field-error'); error.id = `${id}-error`;
      wrap.append(input); field.append(title, wrap, error);
      $('#email').closest('.field').before(field);
    }
    const updateRole = () => {
      const selected = $('[name="role"]:checked')?.value;
      $('#business').disabled = selected !== 'prestatario'; show('#business-field', selected === 'prestatario');
      text('#role-help', selected === 'prestatario' ? 'Solicita crédito según tu cupo y págalo con una parte de tus ventas.' : selected === 'prestamista' ? 'Ofrece crédito y recibe pagos con una parte de las ventas del negocio.' : 'Elige el perfil con el que quieres empezar.');
    };
    $$('[name="role"]').forEach(radio => radio.addEventListener('change', updateRole)); updateRole();
  }
  $$('[data-toggle]').forEach(button => button.onclick = () => {
    const input = document.getElementById(button.dataset.toggle), visible = input.type === 'password';
    input.type = visible ? 'text' : 'password'; button.setAttribute('aria-pressed', String(visible));
    button.setAttribute('aria-label', `${visible ? 'Ocultar' : 'Mostrar'} ${input.id === 'confirm-password' ? 'confirmación de contraseña' : 'contraseña'}`);
  });
  function validate(field) {
    let message = '';
    if (field.disabled) return true;
    if (!field.value.trim()) message = 'Completa este campo para continuar.';
    else if (field.type === 'email' && field.validity.typeMismatch) message = 'Escribe un correo válido, por ejemplo nombre@correo.com.';
    else if (field.id === 'password' && view === 'register' && (field.value.length < 8 || new TextEncoder().encode(field.value).length > 72)) message = 'Usa al menos 8 caracteres y como máximo 72 bytes para tu contraseña.';
    else if (field.id === 'confirm-password' && field.value !== $('#password').value) message = 'Las contraseñas no coinciden. Revisa e inténtalo de nuevo.';
    field.setAttribute('aria-invalid', String(Boolean(message))); text(`#${field.id}-error`, message);
    return !message;
  }
  const fields = $$('input:not([type="radio"])', form);
  fields.forEach(field => {
    field.addEventListener('blur', () => { if (field.value || field.hasAttribute('aria-invalid')) validate(field); });
    field.addEventListener('input', () => { if (field.getAttribute('aria-invalid') === 'true') validate(field); });
  });
  form.addEventListener('submit', event => {
    event.preventDefault();
    const invalid = fields.filter(field => !validate(field));
    const role = $('[name="role"]:checked')?.value;
    text('#role-error', view === 'register' && !role ? 'Elige si eres prestamista o prestatario.' : '');
    if (invalid.length || (view === 'register' && !role)) {
      text('#error-summary', 'Revisa los campos indicados para continuar.'); show('#error-summary');
      (invalid[0] || $('[name="role"]')).focus(); return;
    }
    show('#error-summary', false);
    busy($('[type="submit"]', form), async () => {
      const body = { email: $('#email').value.trim(), password: $('#password').value };
      if (view === 'register') {
        Object.assign(body, { first_name: $('#name').value.trim(), last_name: $('#last-name').value.trim(), city: $('#city').value.trim(), role: role === 'prestatario' ? 'merchant' : 'lender' });
        if (body.role === 'merchant') body.business_name = $('#business').value.trim();
      }
      const result = await api(view === 'register' ? 'register' : 'login', { body });
      session.save(result.access_token);
      $('#password').value = ''; if ($('#confirm-password')) $('#confirm-password').value = '';
      location.replace(nextPage(next, home(result.user.role)));
    }, '#error-summary');
  });
}
