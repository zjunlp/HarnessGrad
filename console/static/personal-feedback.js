(function(root) {
  'use strict';
  function create({submit, newKey}) {
    const pending = new Map();
    async function send(value) {
      const copy = JSON.parse(JSON.stringify(value)), signature = JSON.stringify(copy);
      let entry = pending.get(copy.project_id);
      if (entry?.running && entry.signature !== signature) throw Error('上一条反馈仍在提交，请稍后再试。');
      if (!entry || entry.signature !== signature) {
        entry = {signature, payload: {...copy, idempotency_key: newKey()}};
        pending.set(copy.project_id, entry);
      }
      if (!entry.running) entry.running = Promise.resolve().then(() => submit(entry.payload)).then(result => {
        if (pending.get(copy.project_id) === entry) pending.delete(copy.project_id);
        return result;
      }).finally(() => { entry.running = null; });
      return entry.running;
    }
    return {send};
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {create};
  else root.HGPersonalFeedback = {create};
})(typeof window === 'undefined' ? globalThis : window);
