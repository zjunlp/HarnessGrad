(function(root) {
  'use strict';
  const active = new Set(['collecting', 'planning', 'proposing', 'checking']);
  const messages = {
    collecting: '正在读取项目指令', planning: '正在根据反馈制定计划',
    proposing: '正在生成候选', checking: '正在复核原文件并生成 Diff',
    ready: '提案已生成 · 未应用、未复测', needs_input: '需要补充信息',
    no_change: '目前无需修改', failed: '未生成提案', interrupted: '任务已中断，尚未生成提案',
    cancelled: '任务已取消 · 未修改项目'
  };
  const errors = {
    unavailable: '当前平台无法安全读取项目指令。',
    invalid_path: '请确认项目根目录有普通的 AGENTS.md 文件。',
    refused: '项目指令读取被拒绝。请确认文件路径、权限以及是否存在链接。',
    stale_snapshot: '生成期间原文件发生变化，请核对文件后提交新反馈。',
    unsupported_surface: '选定的项目文件不在当前支持范围内。',
    proposal_failed: '请检查项目文件和模型配置，再提交新的反馈。',
    proposal_busy: '小绿正在处理另一条反馈，稍后可从这条记录开始生成。',
    proposal_unconfigured: '本地服务未配置提案生成。',
    proposal_unavailable: '当前平台不支持安全读取项目指令。',
    proposal_inactive: '本地服务正在停止。',
    clarification_exhausted: '本次补充机会已用完。可以补齐信息后提交新反馈。',
    proposal_reply_unavailable: '这条问题暂不可回复。',
    proposal_reply_conflict: '这条问题已有不同的回复，原回复已保留。',
    proposal_cancel_unavailable: '未找到可取消的任务。'
  };

  function lastEventId(frames, prior) {
    let current = prior;
    for (const match of frames.matchAll(/^id: (\d+)\r?$/gm)) {
      const next = Number(match[1]);
      if (Number.isSafeInteger(next) && next > current) current = next;
    }
    return current;
  }

  function create({read, start, reply, cancel, updates, render,
    wait = () => new Promise(resolve => setTimeout(resolve, 350))}) {
    let version = 0, controller;
    function reset() { version++; controller?.abort(); render(null); }
    async function open(project, feedback, action, initial = null) {
      const current = ++version;
      controller?.abort();
      const abort = controller = new AbortController();
      let proposal = initial, after = 0, readFailures = 0;
      const display = (extra = {}) => render({project, feedback, proposal, ...extra});
      display({loading: true});
      try {
        ({proposal} = await action(project, feedback));
        while (current === version) {
          display();
          if (!active.has(proposal?.status)) break;
          if (updates) {
            try {
              after = lastEventId(await updates(project, feedback, after, abort.signal), after);
            } catch (_) { await wait(); }
          } else await wait();
          if (current !== version) break;
          try {
            ({proposal} = await read(project, feedback));
            readFailures = 0;
          } catch (error) {
            if (++readFailures >= 3) throw error;
            await wait();
          }
        }
      } catch (error) {
        if (current === version) display({error: errors[error.code] || error.message});
      }
    }
    return {reset, show: (project, feedback, generate = false) => open(project, feedback, generate ? start : read),
      reply: (project, feedback, question, answer, current) => open(project, feedback,
        (id, receipt) => reply(id, receipt, question, answer), current),
      cancel: (project, feedback, current) => open(project, feedback, cancel, current)};
  }

  function describe(proposal) {
    if (!proposal) return {message: '反馈已保存，尚未开始生成提案。'};
    const {status, result} = proposal;
    const question = status === 'needs_input' ? result?.question_id || '' : '';
    const waiting = Boolean(question);
    const legacy = status === 'needs_input' && !waiting;
    return {message: legacy ? '这条提案已结束，无法继续回复' : (messages[status] || '提案状态暂不可用'),
      detail: status === 'cancelled' ? '这条反馈已保留，未生成 Diff。'
        : [errors[result?.code], result?.question].filter(Boolean).join(' '),
      path: result?.paths?.join('、') || '', diff: result?.diff || '', fixture: result?.fixture === true,
      question, cancellable: active.has(status) || waiting};
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = {create, describe, lastEventId};
  else root.HGPersonalProposal = {create, describe};
})(typeof window === 'undefined' ? globalThis : window);
