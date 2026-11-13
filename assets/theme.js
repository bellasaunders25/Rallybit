(() => {
  let saved;
  try { saved = localStorage.getItem('rallybit-theme'); } catch {}
  if (saved !== 'light' && saved !== 'dark') saved = null;
  const apply = theme => {
    document.documentElement.dataset.theme = theme;
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.setAttribute('aria-label', `Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`);
      button.setAttribute('aria-pressed', String(theme === 'dark'));
      const icon = button.querySelector('i');
      if (icon) icon.className = `bi bi-${theme === 'dark' ? 'sun' : 'moon'}`;
      const text = button.querySelector('[data-theme-label]');
      const label = theme === 'dark' ? 'Light mode' : 'Dark mode';
      if (text && text.textContent !== label) text.textContent = label;
    });
  };
  const preferred = () => saved === 'dark' ? 'dark' : 'light';
  apply(preferred());
  document.addEventListener('click', event => {
    if (!event.target.closest('[data-theme-toggle]')) return;
    saved = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem('rallybit-theme', saved); } catch {}
    apply(saved);
  });
  document.addEventListener('DOMContentLoaded', () => {
    apply(preferred());
    new MutationObserver(() => apply(preferred())).observe(document.body, { childList: true, subtree: true });
  });
  window.addEventListener('storage', event => { if (event.key === 'rallybit-theme') { saved = ['light','dark'].includes(event.newValue) ? event.newValue : null; apply(preferred()); } });
})();
