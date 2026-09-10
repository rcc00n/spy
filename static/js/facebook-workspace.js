(() => {
  'use strict';
  const reader = document.querySelector('#fb-reader');
  const content = reader?.querySelector('[data-reader-content]');
  let controller, trigger, toastTimer;
  const toast = message => {
    const target = document.querySelector('[data-toast]');
    if (!target) return;
    clearTimeout(toastTimer);
    target.textContent = message;
    target.hidden = false;
    toastTimer = setTimeout(() => { target.hidden = true; }, 3600);
  };
  const reloadInPlace = () => {
    try { sessionStorage.setItem('fb-restore-scroll', JSON.stringify({url: location.href, y: window.scrollY})); } catch (_) {}
    location.reload();
  };
  async function openReader(href, origin) {
    controller?.abort();
    controller = new AbortController();
    const currentController = controller;
    const url = new URL(href, location.href);
    url.searchParams.set('panel', '1');
    if (!reader.open) {
      trigger = origin;
      reader.showModal();
      document.body.style.overflow = 'hidden';
    }
    content.replaceChildren();
    const loading = document.createElement('div');
    loading.className = 'fb-reader-loading';
    loading.setAttribute('role', 'status');
    loading.textContent = 'Loading discussion…';
    loading.append(document.createElement('span'), document.createElement('span'));
    content.append(loading);
    try {
      const response = await fetch(url, {signal: currentController.signal, credentials: 'same-origin'});
      if (!response.ok || response.redirected) throw new Error('Discussion unavailable');
      const html = await response.text();
      if (currentController.signal.aborted || !reader.open) return;
      const parsed = new DOMParser().parseFromString(html, 'text/html');
      const discussion = parsed.querySelector('[data-discussion-id]');
      if (!discussion) throw new Error('Discussion unavailable');
      content.replaceChildren(discussion);
      reader.scrollTop = 0;
    } catch (error) {
      if (error.name === 'AbortError') return;
      const message = document.createElement('p');
      message.className = 'fb-reader-loading';
      message.textContent = 'Could not load this discussion. ';
      const fallback = document.createElement('a');
      url.searchParams.delete('panel');
      fallback.href = url.href;
      fallback.textContent = 'Open the full page →';
      message.append(fallback);
      content.replaceChildren(message);
    }
  }
  document.addEventListener('click', event => {
    const link = event.target.closest('[data-reader-link], [data-panel-link]');
    if (!link || !reader || !reader.showModal || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (link.hasAttribute('data-panel-link') && !reader.contains(link)) return;
    event.preventDefault();
    openReader(link.href, link);
  });
  reader?.querySelector('[data-close-reader]').addEventListener('click', () => reader.close());
  reader?.addEventListener('click', event => {
    if (event.target === reader && event.clientX < reader.getBoundingClientRect().left) reader.close();
  });
  reader?.addEventListener('close', () => {
    controller?.abort();
    document.body.style.overflow = '';
