(function(root) {
  'use strict';
  function shouldSubmit(event, composing) {
    return event.key === 'Enter' && !event.shiftKey && !event.isComposing && !composing;
  }
  function shouldCollapse(event, composing) {
    return event.key === 'Escape' && !event.isComposing && !composing;
  }
  function create({invoke, render, focusInput, onError, timeoutMs = 5000}) {
    let mode = 'bar', epoch = 0, queue = Promise.resolve(), dragging = false;
    const actions = new Set(['bar', 'input', 'details', 'bubble', 'focus', 'drag', 'pin', 'minimize', 'close', 'ordinary', 'status']);
    async function execute(action) {
      let timer;
      try {
        if (!actions.has(action)) throw Error('unknown window action');
        const operation = Promise.resolve().then(() => invoke(action));
        if (action === 'drag' || action === 'close') await operation;
        else await Promise.race([operation, new Promise((_, reject) => {
          timer = setTimeout(() => reject(Error('window response timed out')), timeoutMs);
        })]);
        return true;
      } catch (_) {
        onError();
        return false;
      } finally {
        clearTimeout(timer);
      }
    }
    function action(value) {
      if (value === 'drag') {
        if (dragging) return Promise.resolve(false);
        dragging = true;
        return execute(value).finally(() => { dragging = false; });
      }
      const result = queue.then(() => execute(value));
      queue = result.then(() => undefined);
      return result;
    }
    async function show(next, focus = false) {
      if (!['bar', 'input', 'details'].includes(next)) throw Error('unknown window mode');
      const revision = ++epoch;
      mode = next;
      render(next);
      if (!await action(next)) return false;
      if (focus && revision === epoch) {
        if (!await action('focus')) return false;
        if (revision === epoch) focusInput();
      }
      return true;
    }
    return {show, action, mode: () => mode, dragging: () => dragging,
      hover: () => mode === 'bar' && !dragging ? show('input') : Promise.resolve(true),
      open: () => show('input', true)};
  }
  const api = {create, shouldSubmit, shouldCollapse};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.HGPersonalWindow = api;
})(typeof window === 'undefined' ? globalThis : window);
