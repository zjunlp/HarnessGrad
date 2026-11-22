# Personal harness assistant

This optional Tauri shell displays the independent Python personal service.
The current A+B1+B2 foundation supports project selection, synthetic evidence,
persistent drafts, explicit feedback submission, session search, window controls
and bounded model sessions. On a platform with safe host reads, an opted-in
service can generate a read-only proposal from feedback. Adoption remains future
work. State previews and the model demo are explicitly labelled fixtures.

See the [architecture](../../docs/personal-evolution.md) and
[validation summary](../../docs/validation/personal-foundation.md).

## Run from a checkout

Use Python 3.12+, Rust/Tauri 2 and the OS webview
([prerequisites](https://v2.tauri.app/start/prerequisites/)). `Cargo.lock` pins the
desktop dependencies; no Node frontend build is required. Python runtime
`dependencies=[]` stays unchanged.

The project must exist. Choose a state directory outside both the checkout and
project, and an external build target. These Windows paths are examples:

```powershell
python -B -m harnessgrad.cli personal demo --state-root C:\hg-state --project C:\my-project
python -B -m harnessgrad.cli personal serve --state-root C:\hg-state --background
cargo build --locked --manifest-path desktop/personal/Cargo.toml --target-dir C:\hg-build --jobs 2
python -B -m harnessgrad.cli personal desktop --state-root C:\hg-state --executable C:\hg-build\debug\harnessgrad-personal.exe
```

`demo` imports five synthetic messages and reports duplicates on repeat. For an
empty project setup use `personal project` with the same project/state arguments.
Import only an explicitly synthetic JSON fixture through the UI or
`personal import --state-root ... --project-id ... --input ...`; its format is
provided by `harnessgrad.personal.fixtures.example()`. Daily Codex history is not
scanned. The selected project is not modified.

Add `--ordinary` to `personal desktop` for an OS-managed window. The ordinary
window has a 440 × 560 logical pixel minimum client area when the display can
fit it. Without a native
shell, run `personal serve` in the foreground, open its printed loopback URL and
select that instance's `connection.json` on the connection form.

## Window controls

Drag the project mark on the left. Click the title to focus input; hover expansion
preserves the other application's input focus. Open **记录与设置** for details.
After IME composition finishes, Enter saves, Shift+Enter inserts a newline and
Escape collapses the panel. The minus button minimizes to the taskbar.

Use **关闭小窗** next to **收起** at the top of the details view. Closing the small
window leaves the service and Codex running. A launcher without
arguments may use a `personal-connection.path` sidecar beside the executable,
containing the absolute path to the selected connection file. One shell is
allowed per state directory. Placement is saved after a 200 ms settling interval;
closing immediately after a drag may retain the previous settled position.

The floating assistant uses **小绿**, the cat avatar and mint palette from the
original floating assistant concept. HarnessGrad remains the product name in
the window title and details. The palette is scoped to the personal page;
window controls and feedback behavior are unchanged.

## Drafts and state

Drafts live in the state directory's SQLite database, keyed by project, so they
survive service port changes. Autosave waits 250 ms after input; each draft is
limited to 16 KiB of UTF-8 text. Explicit save, the page's close button and its
stop-service button wait for acknowledgement. Revision conflicts retain the
editor's text; copy it before reloading. A forced exit before acknowledgement
can lose pending input.

Legacy browser drafts migrate only when readable at the current origin and no
stable draft exists. The old copy is removed after a successful save; an
intentionally cleared stable draft does not resurrect old text.

SQLite schema v6 accepts transactional upgrades from known v1–v5 databases,
preserving evidence, feedback, model ledgers and cursors. Exit the old service
normally before reopening its state with this version; old programs cannot read v6.

## Submit feedback and select evidence

Enter still saves a draft. Select the feedback type and click **提交反馈** to
record it locally; the acknowledgement is not a generated proposal or an applied
change. Draft text remains available after submission. A new project can submit
feedback without attaching history.

In **记录与设置**, select one imported session, then search its text or set
exclusive sequence bounds. The newest matching window appears first, including
late corrections and final results; **读取更早的选定证据** loads older windows.
Highlight text inside a source textarea and add the selected fragment, or add
the whole event when nothing is highlighted. Review/remove excerpts in
**本次选定证据** before submitting. Reopen an entry from **反馈历史** to see its
original feedback and any saved excerpts in the proposal card.

Search uses optional SQLite FTS5 trigram support when available; short queries
and missing extensions use selected-session substring matching. The service
preserves source identity, digest, original role and truncation flags. It accepts
at most 32 excerpts and 128 KiB excerpt text per submission. No daily Codex history
is read and no host file is modified by B2.

The service binds `127.0.0.1` on an ephemeral port. Its private `connection.json`
contains the instance ID and bearer token. The shell injects that token into page
memory and restricts navigation to the selected origin; browser pairing keeps it
in tab session storage, not the URL. Use these instance-checked controls:

```powershell
python -B -m harnessgrad.cli personal status --state-root C:\hg-state
python -B -m harnessgrad.cli personal inspect --state-root C:\hg-state --codex C:\native\codex.exe
python -B -m harnessgrad.cli personal stop --state-root C:\hg-state
```

The optional `--codex` selects a version probe; it does not establish protocol or
write capability. `--managed-hooks` declares policy only. Stop authenticates the
instance rather than killing a process by PID/port. After a forced process exit,
inspect the owned instance before manually removing a stale connection record.

## Model configuration and offline demo

The model layer reuses `HG_METHOD_BASE_URL`, `HG_METHOD_MODEL`,
`HG_METHOD_API_KEY` and `HG_METHOD_TIMEOUT_S`. Configuration preview selects an
explicit environment file, with process values taking precedence; it makes no
request and persists no key. The demo uses a scripted client regardless of real
model environment settings:

```powershell
python -B -m harnessgrad.cli personal model-config --env-file C:\hg-config\model.env
python -B -m harnessgrad.cli personal model-demo --state-root C:\hg-state --project C:\my-project
```

The demo resumes two stages under one quota and refuses a third call. It reports
`fixture=true`, `applied=false` and zero real model calls.

## Generate a read-only proposal

On a platform that supports B5's safe reads, start the service with a selected
model configuration file and select a project that has a root `AGENTS.md`:

```bash
python -B -m harnessgrad.cli personal serve --state-root /path/to/state --proposal-env-file /path/to/model.env
```

The file must explicitly supply `HG_METHOD_BASE_URL`, `HG_METHOD_MODEL` and
`HG_METHOD_API_KEY`. Open the service page or its paired shell. Check the
model destination and click **提交并生成提案**. The page opens the proposal
card and shows the Diff once the background task finishes. If 小绿 asks a
question, use **回复并继续** to resume the same task. Use **取消任务** to close a
pending question or stop later stages of an active proposal. Reopen the same
Diff from **反馈历史**. The operation leaves project files unchanged.

Windows uses safe reads on fixed local drives. The production page, SSE reconnect,
reply, Diff, reload and cancellation were checked in Windows Chrome with the
native Windows service and a scripted model. The Windows desktop shell was not
part of that browser check.

## Supported scope

Historical stage-A native interaction was accepted on Windows x86-64, one monitor
at 100% scaling. B2 feedback submission was additionally observed at 125% scaling;
the validation summary records remaining interaction checks. The sample is opaque.
Physical multi-monitor/DPI changes, transparent composition,
other operating systems and automatic fallback remain unverified. The validation
summary separates native observations, browser checks and offline controls.
