# Console storage boundary review

The shared console scripts now route browser preference reads and writes through `HGStorage` in `i18n.js`. Read failures use defaults without writing them; failed writes are kept in document-local memory and report that changes cannot persist. Language retains its existing alert and reloads only after a successful save; auto removes the saved choice. A failed language selection leaves the active choice unchanged. Memory is not shared across navigations or refreshes.

Audit: direct production `localStorage` operations in console static JavaScript were confined to `i18n.js`, `shell.js`, and `sidebar.js`; the latter two now use the boundary. The language bootstrap remains the sole native-storage caller. No server changes, new dependencies, real models, POST requests or external services were used.

Validation from this worktree with temporary files under `/tmp`:

- `TMPDIR=/tmp python -m pytest -q -p no:cacheprovider tests/quality/test_console_storage_browser.py tests/quality/test_console_nav.py` — 18 passed (offline Chromium with all traffic intercepted; synthetic blocked-storage and normal cases on six console pages).
- `node --test console/static/i18n.test.js` — 3 passed.

The browser fixture is synthetic and is not a full application or backend integration test. Initial browser test runs failed because the fixture attempted to click settings while the sidebar was collapsed, then assumed every page has a sidebar toggle; both fixture issues were corrected before the passing run.
