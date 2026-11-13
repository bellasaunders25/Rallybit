(() => {
  const picker = document.querySelector('[data-workspace-server]');
  picker?.addEventListener('change', () => {
    const id = picker.value;
    window.location.assign(/^\d{15,22}$/.test(id) ? `/dashboard/overview.php?id=${id}` : '/dashboard/');
  });
  document.addEventListener('keydown', event => {
    const element = event.target;
    if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey ||
        element?.closest('input, textarea, select, [contenteditable="true"]')) return;
    const search = document.querySelector('.workspace-search input');
    if (search) { event.preventDefault(); search.focus(); }
  });
})();
