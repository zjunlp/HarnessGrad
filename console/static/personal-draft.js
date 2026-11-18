(function(root) {
  'use strict';
  function create({load, save, readLegacy = () => null, clearLegacy = () => {}, onError, delayMs = 250}) {
    const entries = new Map();
    function entry(project) {
      if (!project) throw Error('请先选择项目。');
      if (!entries.has(project)) entries.set(project, {text: '', revision: 0, dirty: false, version: 0});
      return entries.get(project);
    }
    async function read(project) {
      const value = entry(project);
      if (!value.loaded) {
        if (!value.loading) value.loading = (async () => {
          const stored = await load(project);
          value.revision = stored.revision;
          if (!value.dirty) {
            value.text = stored.text;
            // Only an absent server draft may inherit the old origin-local draft.
            // Revision > 0 includes an intentionally cleared draft.
            if (stored.revision === 0) {
              const legacy = readLegacy(project);
              if (legacy !== null) { value.text = legacy; value.dirty = true; }
            }
          }
          value.loaded = true;
        })().finally(() => { value.loading = null; });
        await value.loading;
      }
      return {text: value.text, revision: value.revision};
    }
    function change(project, text) {
      const value = entry(project);
      value.text = text; value.dirty = true; value.version++;
      clearTimeout(value.timer);
      value.timer = setTimeout(() => flush(project).catch(onError), delayMs);
    }
    async function flush(project) {
      const value = entry(project);
      clearTimeout(value.timer);
      await read(project);
      if (!value.running) value.running = (async () => {
        while (value.dirty) {
          if (new TextEncoder().encode(value.text).byteLength > 16 * 1024) {
            throw Error('草稿最多 16 KiB；内容仍保留在当前窗口，请缩短或复制后再保存。');
          }
          const version = value.version;
          const stored = await save(project, value.text, value.revision);
          value.revision = stored.revision;
          if (version === value.version) value.dirty = false;
          // Keep old browser storage until the stable store acknowledges a save.
          clearLegacy(project);
        }
      })().finally(() => { value.running = null; });
      await value.running;
    }
    async function flushAll() {
      for (const project of entries.keys()) await flush(project);
    }
    function dispose() {
      for (const value of entries.values()) clearTimeout(value.timer);
    }
    return {read, change, flush, flushAll, dispose};
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {create};
  else root.HGPersonalDraft = {create};
})(typeof window === 'undefined' ? globalThis : window);
