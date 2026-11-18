'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const desktop = Boolean(window.__TAURI__);
  let ordinary = Boolean(window.HG_CONNECTION?.ordinary);
  if (desktop) document.body.classList.add('desktop');
  document.body.classList.toggle('ordinary', ordinary);
  if (!desktop) {
    for (const id of ['drag', 'minimize', 'pin']) {
      $(id).disabled = true;
      $(id).title = '仅桌面壳支持此操作';
    }
  }
  let token = window.HG_CONNECTION?.token || sessionStorage.getItem('hg-personal-token') || '';
  let project = '', instance = '', cursor = 0, total = 0, composing = false, selection = 0;
  $('feedback').disabled = true; $('save-draft').disabled = true;
  let collapseTimer;
  const windowController = window.HGPersonalWindow.create({
    invoke: action => desktop ? window.__TAURI__.core.invoke('window_action', {action}).then(value => {
      ordinary = value === true;
      document.body.classList.toggle('ordinary', ordinary);
    }) : Promise.resolve(),
    render: next => {
      const visibleMode = ordinary ? 'details' : next;
      document.body.dataset.mode = visibleMode;
      $('workspace').hidden = visibleMode !== 'details';
      $('expand').setAttribute('aria-expanded', String(visibleMode !== 'bar'));
    },
    focusInput: () => $('feedback').focus(),
    onError: () => {
      $('bar-sub').textContent = '窗口暂未响应，请查看本地记录';
      $('connection-state').textContent = '窗口操作未完成；草稿和证据保留，可关闭小窗后重开。';
    }
  });
  const native = action => windowController.action(action);
  const showMode = (next, focus = false) => windowController.show(next, focus);
  function notify(message) { $('bubble').textContent = message; $('bubble').hidden = false; if (!ordinary && windowController.mode() !== 'details') native('bubble'); }
  async function api(path, value) {
    const response = await fetch('/api/personal/' + path, {
      method: value === undefined ? 'GET' : 'POST',
      headers: {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
      ...(value === undefined ? {} : {body: JSON.stringify(value)})
    });
    if (response.status === 401) { $('pair').hidden = false; showMode('details'); throw Error('请连接自己的本地服务。'); }
    if (response.status === 409) throw Error('草稿已在另一窗口更新。当前内容保留，请先复制，再重新加载。');
    if (!response.ok) throw Error('操作未完成：请核对项目、输入格式或本地服务。');
    return response.json();
  }
  function guarded(fn) { return async event => { try { await fn(event); } catch (error) { notify(error.message); } }; }
  function selected() { if (!project) throw Error('请先在记录与设置中选择项目。'); return project; }
  const drafts = window.HGPersonalDraft.create({
    load: id => api(`projects/${id}/draft`),
    save: (id, text, revision) => api('draft', {project_id: id, text, revision}),
    readLegacy: id => { try { return localStorage.getItem('hg-personal-draft:' + id); } catch (_) { return null; } },
    clearLegacy: id => { try { localStorage.removeItem('hg-personal-draft:' + id); } catch (_) {} },
    onError: error => notify(error.message)
  });
  function saveDraft() { drafts.change(selected(), $('feedback').value); }
  function capabilities(value) {
    const labels = {
      'codex.executable_version': 'Codex 命令行', 'personal.synthetic_import': '示例记录导入',
      'codex.existing_session_observation': '会话记录读取', 'codex.hooks': '自动事件接入',
      'codex.harness_writes': 'Harness 修改'
    };
    const statuses = {ready:'可用', unavailable:'不可用', unchecked:'未验证', refused:'受限', invalid:'需检查'};
    $('capabilities').replaceChildren();
    for (const check of value.checks) {
      const row = document.createElement('div'), scope = document.createElement('span'), status = document.createElement('span');
      scope.textContent = labels[check.scope] || check.scope; scope.title = check.detail;
      status.textContent = statuses[check.status] || check.status; status.dataset.status = check.status;
      row.append(scope, status); $('capabilities').append(row);
    }
  }
  async function connect() {
    const health = await api('health'); instance = health.instance;
    $('pair').hidden = true;
    $('connection-state').textContent = '已连接本地服务 · 草稿保存在本机，暂不执行 Harness 修改。';
    capabilities(health.capabilities);
    const previous = project;
    $('projects').replaceChildren(new Option('请选择项目', ''));
    for (const row of health.projects) $('projects').add(new Option(row.root, row.id));
    project = health.projects.some(row => row.id === previous) ? previous : (health.projects[0]?.id || '');
    $('projects').value = project;
    await selectProject();
  }
  async function selectProject() {
    project = $('projects').value; cursor = 0; total = 0;
    const id = project, generation = ++selection;
    $('feedback').disabled = true; $('save-draft').disabled = true; $('feedback').value = '';
    $('events').replaceChildren(); $('event-count').textContent = '0 条'; $('more').hidden = true;
    $('scope').textContent = project ? $('projects').selectedOptions[0].textContent : '未选择项目';
    $('sessions').textContent = '';
    if (project) {
      const draft = await drafts.read(id);
      if (generation !== selection) return;
      $('feedback').value = draft.text; $('feedback').disabled = false; $('save-draft').disabled = false;
      // A legacy draft is migrated only after the stable store acknowledges it.
      drafts.flush(id).catch(error => notify(error.message));
      const data = await api(`projects/${id}/sessions`);
      if (generation !== selection) return;
      $('sessions').textContent = `${data.sessions.length} 个明确导入的合成会话`;
      await loadEvents(id, generation);
    }
  }
  async function loadEvents(id = selected(), generation = selection) {
    const data = await api(`projects/${id}/events?after=${cursor}&limit=50`);
    if (id !== project || generation !== selection) return;
    cursor = data.next_after; total = data.total;
    for (const row of data.events) {
      const item = document.createElement('li'), heading = document.createElement('strong');
      const text = document.createElement('p'), meta = document.createElement('small');
      heading.textContent = `${row.role} · 合成证据`; text.textContent = row.text;
      meta.textContent = `${row.session_id} / ${row.event_id} · ${row.timestamp}${row.truncated ? ' · 内容已截断' : ''}`;
      item.append(heading, text, meta); $('events').append(item);
    }
    $('event-count').textContent = `${$('events').children.length} / ${total} 条`;
    $('more').hidden = $('events').children.length >= total;
  }
  // Hover may already have expanded the panel: clicking must focus, not toggle it shut.
  $('expand').onclick = () => windowController.open();
  $('details').onclick = () => showMode('details');
  $('collapse').onclick = () => { $('bubble').hidden = true; showMode('bar'); };
  $('minimize').onclick = () => native('minimize');
  $('drag').onmousedown = () => native('drag');
  $('pin').onclick = () => native('pin');
  $('ordinary').onclick = async () => { if (await native('ordinary')) showMode('details'); };
  $('close-window').onclick = guarded(async () => {
    await drafts.flushAll();
    if (desktop) await native('close'); else showMode('bar');
  });
  $('expand').onmouseenter = () => { clearTimeout(collapseTimer); if (!ordinary) windowController.hover(); };
  document.querySelector('.widget').onmouseleave = () => {
    clearTimeout(collapseTimer);
    collapseTimer = setTimeout(() => { if (windowController.mode() === 'input' && !windowController.dragging() && $('bubble').hidden && !document.querySelector('.widget').contains(document.activeElement)) showMode('bar'); }, 700);
  };
  $('feedback').oninput = guarded(saveDraft);
  $('feedback').oncompositionstart = () => { composing = true; };
  $('feedback').oncompositionend = () => { composing = false; };
  $('feedback').onkeydown = event => {
    if (window.HGPersonalWindow.shouldSubmit(event, composing)) { event.preventDefault(); $('save-draft').click(); }
  };
  $('save-draft').onclick = guarded(async () => {
    saveDraft(); await drafts.flush(selected());
    notify('草稿已保存在本机，可在服务重启后恢复。\n尚未生成提案或修改 Harness。');
  });
  document.addEventListener('keydown', event => {
    if (window.HGPersonalWindow.shouldCollapse(event, composing)) {
      if (project && !$('feedback').disabled) saveDraft();
      $('bubble').hidden = true; showMode('bar');
    }
  });
  $('projects').onchange = guarded(selectProject);
  $('pair').onsubmit = guarded(async event => { event.preventDefault(); token = $('token').value.trim(); await connect(); sessionStorage.setItem('hg-personal-token', token); $('token').value = ''; });
  $('connection-file').onchange = guarded(async event => {
    const file = event.target.files[0]; if (!file) return;
    if (file.size > 16384) throw Error('连接文件超过限制。');
    const value = JSON.parse(await file.text());
    if (value.schema !== 'harnessgrad.personal.connection.v1' || new URL(value.url).origin !== location.origin || !/^[a-f0-9]{64}$/.test(value.token)) throw Error('请选择当前服务自己的连接文件。');
    token = value.token; await connect(); sessionStorage.setItem('hg-personal-token', token); event.target.value = '';
  });
  $('add-project').onsubmit = guarded(async event => { event.preventDefault(); const row = await api('projects', {root: $('project-root').value}); project = row.id; await connect(); $('project-root').value = ''; });
  $('import-example').onclick = guarded(async () => {
    const result = await api('import', {project_id: selected(), document: await api('example')});
    await selectProject(); notify(`合成证据导入完成：新增 ${result.added} 条，重复 ${result.duplicates} 条。\n没有生成提案或应用修改。`);
  });
  $('import-file').onchange = guarded(async event => {
    const file = event.target.files[0]; if (!file) return;
    if (file.size > 1024 * 1024) throw Error('导入文件不得超过 1 MiB。');
    const result = await api('import', {project_id: selected(), raw_document: await file.text()});
    await selectProject(); notify(`合成证据已导入：${result.added} 条。`); event.target.value = '';
  });
  $('more').onclick = guarded(() => loadEvents());
  $('probe').onclick = guarded(async () => capabilities(await api('probe', {})));
  const previewCopy = {
    idle: ['等待反馈', '示例预览：等待下一条反馈。'], input: ['输入反馈', '示例预览：输入反馈，当前仅保存本地草稿。'],
    working: ['处理中', '示例预览：后台正在选择证据。\n没有真实学习任务在运行。'],
    question: ['需要补充', '示例问题：请选择相关会话。\n当前仅预览问题状态，暂未接入回复处理。'],
    done: ['记录完成', '示例预览：记录完成。\n未生成提案、未复测、未应用修改。'],
    failed: ['暂未解决', '示例预览：未找到足够证据，等待补充。']
  };
  $('previews').onclick = guarded(async event => {
    const state = event.target.dataset.state; if (!state) return;
    await api('demo', {project_id: selected(), state});
    $('bar-title').textContent = 'HarnessGrad';
    $('bar-sub').textContent = '示例预览 · ' + previewCopy[state][0];
    notify(previewCopy[state][1]); if (state === 'input') showMode('input', true);
  });
  $('stop-service').onclick = guarded(async () => {
    await drafts.flushAll();
    await api('shutdown', {instance}); $('connection-state').textContent = '本服务已停止；已有 Codex 不受影响。'; notify('本服务已停止。关闭小窗不会关闭 Codex。');
  });
  connect().catch(error => { $('connection-state').textContent = error.message; });
  if (desktop) native('status').then(() => { if (ordinary) showMode('details'); });
  else showMode('details');
})();
