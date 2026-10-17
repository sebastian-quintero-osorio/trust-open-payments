const menuButton = document.querySelector('#open-menu');
const sidebar = document.querySelector('#sidebar');
const overlay = document.querySelector('#menu-overlay');
const workspace = document.querySelector('.workspace');
const mobileLayout = matchMedia('(max-width: 700px)');
function closeMenu(restoreFocus = true) {
  sidebar.classList.remove('is-open');
  overlay.hidden = true;
  document.body.classList.remove('menu-open');
  menuButton.setAttribute('aria-expanded', 'false');
  workspace.inert = false;
  if (restoreFocus && mobileLayout.matches) menuButton.focus();
}
menuButton.addEventListener('click', () => {
  sidebar.classList.add('is-open');
  overlay.hidden = false;
  document.body.classList.add('menu-open');
  menuButton.setAttribute('aria-expanded', 'true');
  workspace.inert = true;
  document.querySelector('#close-menu').focus();
});
document.querySelector('#close-menu').addEventListener('click', () => closeMenu());
overlay.addEventListener('click', () => closeMenu());
sidebar.querySelectorAll('a, [data-pending]').forEach(item => item.addEventListener('click', () => {
  if (mobileLayout.matches) closeMenu();
}));
mobileLayout.addEventListener('change', () => closeMenu(false));
document.addEventListener('keydown', event => {
  if (!sidebar.classList.contains('is-open')) return;
  if (event.key === 'Escape') closeMenu();
  if (event.key === 'Tab') {
    const items = [...sidebar.querySelectorAll('a, button')].filter(item => item.getClientRects().length);
    if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1).focus(); }
    else if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0].focus(); }
  }
});

const notice = document.querySelector('#preview-notice');
document.querySelectorAll('[data-pending]').forEach(button => {
  button.addEventListener('click', () => {
    notice.hidden = false;
    document.querySelector('#preview-message').textContent = `${button.dataset.pending}: este flujo corresponde a un próximo mockup.`;
  });
});
document.querySelector('#dismiss-notice').addEventListener('click', () => { notice.hidden = true; });

const scope = document.querySelector('#scope-dialog');
document.querySelector('#scope-button').addEventListener('click', () => scope.showModal());
document.querySelector('#close-scope').addEventListener('click', () => scope.close());
document.querySelector('#back-to-mockup').addEventListener('click', () => scope.close());
