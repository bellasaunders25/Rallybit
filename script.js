const include = async (selector, path) => {
  const target = document.querySelector(selector);
  if (!target) return;
  try {
    const response = await fetch(path, { cache: 'no-cache' });
    if (!response.ok) throw new Error(`${response.status}`);
    target.innerHTML = await response.text();
  } catch (error) {
    console.warn(`Unable to load ${path}`, error);
  }
};

const initialiseNavigation = () => {
  const toggle = document.querySelector('[data-nav-toggle]');
  const nav = document.querySelector('[data-nav]');
  const header = document.querySelector('[data-header]');

  const updateHeader = () => header?.classList.toggle('is-scrolled', window.scrollY > 8);
  updateHeader();
  window.addEventListener('scroll', updateHeader, { passive: true });

  if (nav) {
    const currentPath = location.pathname.replace(/\/$/, '/index.html');
    nav.querySelectorAll('a').forEach(link => {
      const linkPath = new URL(link.href, location.origin).pathname.replace(/\/$/, '/index.html');
      if (linkPath === currentPath && !link.classList.contains('nav-dashboard')) link.classList.add('is-active');
    });
  }

  if (!toggle || !nav) return;
  toggle.addEventListener('click', () => {
    const open = nav.classList.toggle('is-open');
    toggle.setAttribute('aria-expanded', String(open));
  });
  nav.querySelectorAll('a').forEach(link => link.addEventListener('click', () => {
    nav.classList.remove('is-open');
    toggle.setAttribute('aria-expanded', 'false');
  }));
  document.addEventListener('click', event => {
    if (!nav.contains(event.target) && !toggle.contains(event.target)) {
      nav.classList.remove('is-open');
      toggle.setAttribute('aria-expanded', 'false');
    }
  });
};

const initialisePage = async () => {
  await Promise.all([
    include('#header-placeholder', '/include/header.html'),
    include('#footer-placeholder', '/include/footer.html')
  ]);
  initialiseNavigation();
  document.querySelectorAll('[data-year]').forEach(el => el.textContent = new Date().getFullYear());
  document.body.classList.add('ready');
};

document.addEventListener('DOMContentLoaded', initialisePage);
