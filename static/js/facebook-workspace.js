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
