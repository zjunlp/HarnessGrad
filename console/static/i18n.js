/* Only explicitly marked UI is translated; API and user content is never inspected. */
(() => {
  'use strict';
  const key = 'hg.language';
  let saved = null;
  try { saved = localStorage.getItem(key); } catch (_) { /* storage may be disabled */ }
  const choice = saved === 'en' || saved === 'zh' ? saved : 'auto';
  const languages = (navigator.languages && navigator.languages.length) ? navigator.languages : [navigator.language || 'en'];
  const preferred = languages.find(value => /^(zh|en)(?:-|$)/i.test(value || '')) || 'en';
  const language = choice === 'auto' ? (/^zh(?:-|$)/i.test(preferred) ? 'zh' : 'en') : choice;
  const dictionaries = [window.HG_SHELL_I18N, window.HG_RUNS_I18N, window.HG_LAUNCH_I18N, window.HG_DETAIL_I18N].filter(Boolean);
  function t(k) {
    for (const dict of dictionaries) if (dict[language] && Object.prototype.hasOwnProperty.call(dict[language], k)) return dict[language][k];
    return k;
  }
  function apply(root = document) {
    root.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
    for (const attr of ['placeholder', 'title', 'aria-label']) {
      root.querySelectorAll('[data-i18n-' + attr + ']').forEach(el => el.setAttribute(attr, t(el.getAttribute('data-i18n-' + attr))));
    }
  }
  function setLanguage(value) {
    if (!['auto', 'en', 'zh'].includes(value)) return;
    try {
      if (value === 'auto') localStorage.removeItem(key); else localStorage.setItem(key, value);
    } catch (_) {
      alert(language === 'zh' ? '无法保存语言设置：浏览器存储不可用。' : 'Cannot save language: browser storage is unavailable.');
      return;
    }
    location.reload();
  }
  document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en';
  window.HGI18n = { t, apply, language, choice, setLanguage };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => apply()); else apply();
})();
