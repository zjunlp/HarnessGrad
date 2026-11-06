/* Only explicitly marked UI is translated; API and user content is never inspected. */
(() => {
  'use strict';
  // Failed writes live only for this document. Never seed storage with defaults:
  // a temporarily inaccessible saved preference must not be overwritten.
  const memory = new Map();
  const storage = window.HGStorage = {
    getItem(key) {
      if (memory.has(key)) return memory.get(key);
      try { return localStorage.getItem(key); } catch (_) { return null; }
    },
    setItem(key, value) {
      try { localStorage.setItem(key, value); memory.delete(key); return true; }
      catch (_) { memory.set(key, String(value)); return false; }
    },
    removeItem(key) {
      try { localStorage.removeItem(key); memory.delete(key); return true; }
      catch (_) { memory.set(key, null); return false; }
    },
    warn() {
      let notice = document.getElementById('hg-storage-warning');
      if (!notice) {
        notice = document.createElement('div');
        notice.id = 'hg-storage-warning';
        notice.setAttribute('role', 'status');
        notice.style.cssText = 'position:fixed;bottom:8px;right:8px;z-index:9999;background:#fff3cd;color:#332600;padding:8px;border:1px solid #806600';
        document.body.appendChild(notice);
      }
      notice.textContent = document.documentElement.lang === 'zh-CN' ? '浏览器存储不可用；更改仅在当前页面有效。' : 'Browser storage is unavailable; changes apply only on this page.';
    },
    save(key, value) { const ok = this.setItem(key, value); if (!ok) this.warn(); return ok; }
  };
  const key = 'hg.language';
  const saved = storage.getItem(key);
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
    const ok = value === 'auto' ? storage.removeItem(key) : storage.setItem(key, value);
    if (!ok) {
      alert(language === 'zh' ? '无法保存语言设置：浏览器存储不可用。' : 'Cannot save language: browser storage is unavailable.');
      return;
    }
    location.reload();
  }
  document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en';
  window.HGI18n = { t, apply, language, choice, setLanguage };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => apply()); else apply();
})();
