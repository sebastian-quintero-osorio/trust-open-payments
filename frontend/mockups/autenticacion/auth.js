(() => {
  'use strict';
  const form = document.querySelector('#auth-form');
  const view = document.body.dataset.view;
  const summary = document.querySelector('#error-summary');
  const password = document.querySelector('#password');
  const confirmation = document.querySelector('#confirm-password');
  const radios = [...form.querySelectorAll('[name="role"]')];
  // These views only simulate submission. Credentials never leave this page.
  form.noValidate = true;

  function showError(field, message) {
    field.setAttribute('aria-invalid', String(Boolean(message)));
    document.getElementById(`${field.id}-error`).textContent = message;
  }

  function validate(field) {
    if (field.disabled) return true;
    let message = '';
    if (!field.value.trim()) message = 'Completa este campo para continuar.';
    else if (field.type === 'email' && field.validity.typeMismatch) message = 'Escribe un correo válido, por ejemplo nombre@correo.com.';
    else if (field.id === 'password' && view === 'register' && field.value.length < 8) message = 'Usa al menos 8 caracteres para tu contraseña.';
    else if (field === confirmation && field.value !== password.value) message = 'Las contraseñas no coinciden. Revisa e inténtalo de nuevo.';
    showError(field, message);
    return !message;
  }

  function validateRole() {
    if (!radios.length) return true;
    const valid = radios.some(radio => radio.checked);
    radios.forEach(radio => radio.setAttribute('aria-invalid', String(!valid)));
    document.querySelector('#role-error').textContent = valid ? '' : 'Elige si eres prestamista o prestatario.';
    return valid;
  }

  function updateRole() {
    const role = form.querySelector('[name="role"]:checked')?.value;
    const business = document.querySelector('#business');
    business.disabled = role !== 'prestatario';
    document.querySelector('#business-field').hidden = business.disabled;
    if (business.disabled) showError(business, '');
    document.querySelector('#role-help').textContent = role === 'prestatario'
      ? 'Solicita crédito según tu cupo y págalo con una parte de tus ventas.'
      : role === 'prestamista' ? 'Ofrece crédito y recibe pagos con una parte de las ventas del negocio.'
        : 'Elige el perfil con el que quieres empezar.';
  }

  radios.forEach(radio => radio.addEventListener('change', () => {
    updateRole();
    validateRole();
  }));
  if (radios.length) updateRole();

  document.querySelectorAll('[data-toggle]').forEach(button => {
    button.addEventListener('click', () => {
      const input = document.getElementById(button.dataset.toggle);
      const visible = input.type === 'password';
      input.type = visible ? 'text' : 'password';
      button.setAttribute('aria-pressed', String(visible));
      const label = input.id === 'confirm-password' ? 'confirmación de contraseña' : 'contraseña';
      button.setAttribute('aria-label', `${visible ? 'Ocultar' : 'Mostrar'} ${label}`);
    });
  });

  const fields = [...form.querySelectorAll('input:not([type="radio"])')];
  fields.forEach(field => {
    field.addEventListener('blur', () => { if (field.value || field.hasAttribute('aria-invalid')) validate(field); });
    field.addEventListener('input', () => {
      if (field.getAttribute('aria-invalid') === 'true') validate(field);
      if (field === password && confirmation?.value) validate(confirmation);
    });
  });

  form.addEventListener('submit', event => {
    event.preventDefault();
    const roleValid = validateRole();
    const invalid = fields.filter(field => !validate(field));
    summary.hidden = roleValid && invalid.length === 0;
    if (!summary.hidden) {
      (!roleValid ? radios[0] : invalid[0]).focus();
      return;
    }

    const detail = document.querySelector('#success-detail');
    if (view === 'register') {
      const role = form.querySelector('[name="role"]:checked').value;
      detail.textContent = `Perfil seleccionado: ${role}.`;
      document.querySelector('#profile-portal').href = `../portal-prestamos/${role === 'prestamista' ? 'prestamista' : 'index'}.html`;
    } else if (view === 'recovery') {
      detail.textContent = 'Esta es una vista de demostración. No se ha enviado ningún correo.';
    }

    fields.filter(field => field.autocomplete.includes('password')).forEach(field => {
      field.value = '';
      field.type = 'password';
    });
    document.querySelectorAll('[data-toggle]').forEach(button => {
      button.setAttribute('aria-pressed', 'false');
      button.setAttribute('aria-label', button.dataset.toggle === 'password' ? 'Mostrar contraseña' : 'Mostrar confirmación de contraseña');
    });
    document.querySelector('#form-content').hidden = true;
    document.querySelector('#success-panel').hidden = false;
    document.querySelector('#success-title').focus();
  });

  document.querySelector('#retry-recovery')?.addEventListener('click', () => {
    document.querySelector('#success-panel').hidden = true;
    document.querySelector('#form-content').hidden = false;
    document.querySelector('#email').focus();
    document.querySelector('#email').select();
  });
})();
