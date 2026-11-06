# Console launch method labels review

The launch selector presents conventional method names without catalogue descriptions; `a`, `b`, and `echo_base` are suppressed only in this UI. IDs, backend catalogue and launch guards are unchanged. Unknown names remain visible verbatim, in catalogue order, with HTML escaping applied by the existing select renderer. No-op remains distinct from the translated no-method control. The initial selection remains `none` as before; selection normalization avoids a hidden value diverging from the displayed option if state is later restored or defaults become active.

Offline validation (no actual launch or model calls):

- `TMPDIR=/tmp node --test console/static/launch-methods.test.js console/static/launch-submit-i18n.test.js`: 5 passed.
- `TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1 /mnt/8t/xubuqiang/anaconda3/bin/python3 -m pytest -q -p no:cacheprovider tests/quality/test_console_method_labels_browser.py tests/quality/test_console_launch_preflight.py tests/quality/test_console_launch_models.py`: 24 passed. Chromium tests intercept every request, use synthetic GET fixtures and cover English and Chinese, defaults, hidden names, a case-sensitive unknown and escaping.
- Initial browser test run had six assertion failures due to incorrect expected existing translations for the no-method control; corrected expectations to the actual translations and reran successfully.

This is focused validation, not a full-system or live-server test. No POSTs or external network/model requests are made by the browser test.
