const formAccount = TrustLoans.currentAccount(document.body.dataset.mode === 'solicitar' ? 'prestatario' : 'prestamista');
(() => {
  const D = TrustLoans, account = D.accounts[formAccount];
  document.querySelector('[data-wallet]').href = D.wallet(formAccount);
  document.querySelector('.account strong').textContent = account.name;
  document.querySelector('.account .avatar').textContent = account.name.split(' ').map(word => word[0]).slice(0, 2).join('');
  document.querySelectorAll('a[href*="portal-prestamos/"]').forEach(link => { link.href = D.portal(formAccount); });
  if (account.role === 'prestatario') {
    try {
      const amount = D.money(D.credit(formAccount));
      document.querySelector('.fieldset-intro').textContent = `Indica el mínimo y el máximo que te servirían. Ambos deben estar dentro de tu cupo de ${amount} COP.`;
      const node = document.querySelector('.context-amount');
      node.firstChild.textContent = amount;
    } catch { /* El formulario informa el error de lectura antes de usar el cupo. */ }
  }
})();
