/* Shared locale state for the site, account dialogs, payment return and admin. */
(() => {
  'use strict';
  const messages = window.NorthstarMessages;
  const supported = ['zh-CN', 'en', 'ja', 'ko', 'ms'];
  const names = ['中文', 'English', '日本語', '한국어', 'Bahasa Melayu'];
  const values = new WeakMap();
  const listeners = new Set();
  const storageKey = 'northstar.language';
  const queryLanguage = new URLSearchParams(window.location.search).get('lang');
  let savedLanguage;
  try { savedLanguage = window.localStorage.getItem(storageKey); } catch (_) { /* Storage may be disabled. */ }
  let language = supported.includes(queryLanguage) ? queryLanguage : (supported.includes(savedLanguage) ? savedLanguage : 'zh-CN');
  const reverse = new Map(Object.entries(messages['zh-CN']).map(([key, value]) => [value, key]));
  const aliases = {
    '注册验证已失效，请重新填写邮箱和密码。': 'error.expiredSession',
    'admin authentication required': 'admin.authRequired',
    'ADMIN_PASSWORD 未配置。': 'admin.unconfigured',
    'WHOP_CHECKOUT_SIGNING_SECRET is not configured': 'payment.unconfigured',
  };

  function t(key, params = {}) {
    const template = messages[language][key] || messages['zh-CN'][key] || key;
    return template.replace(/\{(\w+)\}/g, (match, name) => Object.hasOwn(params, name) ? String(params[name]) : match);
  }

  function renderNode(node) {
    const params = values.get(node) || {};
    if (node.dataset.i18n) node.textContent = t(node.dataset.i18n, params);
    for (const attribute of ['placeholder', 'aria-label', 'title', 'content', 'alt']) {
      const key = node.getAttribute(`data-i18n-${attribute}`);
      if (key) node.setAttribute(attribute, t(key, params));
    }
  }

  function set(node, key, params = {}) {
    node.dataset.i18n = key;
    values.set(node, params);
    renderNode(node);
  }

  function clear(node) {
    delete node.dataset.i18n;
    values.delete(node);
    node.textContent = '';
  }

  function errorKey(error, fallback = 'error.account') {
    if (error && error.i18nKey) return error.i18nKey;
    const message = String(error && error.message || '');
    if (reverse.has(message)) return reverse.get(message);
    if (aliases[message]) return aliases[message];
    if (message.includes('验证码发送失败')) return 'error.delivery';
    if (/请等待 \d+ 秒后重新发送/.test(message)) return 'auth.rateLimit';
    if (error && ['TypeError', 'TimeoutError', 'AbortError'].includes(error.name)) return 'error.network';
    return fallback;
  }

  function showError(node, error, fallback) {
    set(node, errorKey(error, fallback), { status: error && error.status || '' });
  }

  function render() {
    document.documentElement.lang = language;
    document.querySelectorAll('[data-i18n], [data-i18n-placeholder], [data-i18n-aria-label], [data-i18n-title], [data-i18n-content], [data-i18n-alt]').forEach(renderNode);
    document.querySelectorAll('[data-language-select]').forEach(select => { select.value = language; });
    // Keep the selected language when following the payment return or home links.
    document.querySelectorAll('[data-language-link]').forEach(link => {
      const url = new URL(link.getAttribute('href'), window.location.origin);
      url.searchParams.set('lang', language);
      link.setAttribute('href', url.pathname + url.search + url.hash);
    });
    listeners.forEach(listener => listener(language));
  }

  function change(next) {
    if (!supported.includes(next)) return;
    language = next;
    try { window.localStorage.setItem(storageKey, language); } catch (_) { /* Private browsing can reject writes. */ }
    try {
      const url = new URL(window.location.href);
      url.searchParams.set('lang', language);
      window.history.replaceState(null, '', url.pathname + url.search + url.hash);
    } catch (_) { /* Locale selection still works without history access. */ }
    render();
  }

  function init() {
    document.querySelectorAll('[data-language-select]').forEach(select => {
      select.replaceChildren();
      supported.forEach((code, index) => {
        const option = document.createElement('option');
        option.value = code;
        option.lang = code;
        option.textContent = names[index];
        select.append(option);
      });
      select.addEventListener('change', () => change(select.value));
    });
    // Also remember a language selected through a shared ?lang= link.
    try { window.localStorage.setItem(storageKey, language); } catch (_) { /* Optional persistence. */ }
    render();
  }

  window.NorthstarI18n = {
    t, set, clear, showError, change, render,
    get language() { return language; },
    onChange(listener) { listeners.add(listener); },
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
