(() => {
  "use strict";
  const en = {
    title: "Export evidence",
    intro:
      "Describe the concerns and explicitly identify the reported result. This read-only export does not run experiments or models.",
    scope:
      "All attempts in this Console’s experiment directory are included. Other directories are outside this export.",
    loading: "Loading the configured directory…",
    count: "Attempts in scope",
    reported: "Reported result and declared budget",
    run: "Run",
    round: "Round",
    rule: "Selection rule",
    budgetRounds: "Budget: rounds",
    budgetTrials: "Budget: trials",
    budgetSource: "Budget source",
    concerns: "Concerns",
    add: "Add concern",
    remove: "Remove",
    id: "Concern ID",
    concern: "Concern",
    status: "Measurement status",
    measured: "Measured",
    unmeasured: "Unmeasured",
    failed: "Failed",
    stopped: "Stopped",
    invalidStatus: "Invalid",
    training: "Training",
    exam: "Exam",
    accuracy: "Accuracy",
    score: "Score",
    reward: "Reward",
    pass_rate: "Pass rate",
    jsonLabel: "Author manifest JSON",
    reason: "Reason not measured",
    resultRun: "Result run",
    resultRound: "Result round",
    baselineRun: "Baseline run (optional)",
    baselineRound: "Baseline round",
    advanced: "Advanced author manifest JSON",
    advancedHint:
      "Generate from the form or load an author draft, then edit it to describe rejected attempts, attempt annotations or variation_groups. When enabled, this JSON is sent instead of the form.",
    generate: "Generate JSON from form",
    load: "Load JSON file",
    useJson: "Use JSON for export",
    privacy:
      "Review before sharing: the bundle inventories the configured directory, including failed and unmeasured attempts. Author text and identifiers may contain private information. Raw trajectories, task text and patch contents are withheld, but filtering cannot detect every secret.",
    download: "Export and download",
    choose: "Choose…",
    none: "None",
    bad: "Complete the required fields and choose existing rounds.",
    catalogError: "Could not load the full attempt catalog.",
    invalid:
      "Invalid manifest or missing/invalid records. Check the draft and referenced rounds.",
    limit:
      "The configured directory or export exceeds a server limit. The scope was not reduced.",
    busy: "Another export is in progress. Try again.",
    unavailable: "Evidence export is unavailable.",
    network: "Could not reach the evidence service.",
    jsonError: "Invalid JSON author draft.",
    tooLarge: "The author draft exceeds the 1 MiB request limit.",
    working: "Exporting…",
    success: "Download started.",
    empty: "No attempts found.",
  };
  const zh = {
    title: "导出证据",
    intro:
      "填写关注点并明确指定报告的结果。此操作只读取记录，不会运行实验或模型。",
    scope:
      "导出包含当前 Console 实验目录中的全部尝试，其他目录不在本次范围内。",
    loading: "正在读取配置目录…",
    count: "范围内的尝试",
    reported: "报告结果与声明预算",
    run: "实验",
    round: "轮次",
    rule: "结果选择规则",
    budgetRounds: "预算：轮数",
    budgetTrials: "预算：试验数",
    budgetSource: "预算来源",
    concerns: "关注点",
    add: "添加关注点",
    remove: "移除",
    id: "关注点 ID",
    concern: "关注点",
    status: "测量状态",
    measured: "已测量",
    unmeasured: "未测量",
    failed: "失败",
    stopped: "已停止",
    invalidStatus: "无效",
    training: "训练",
    exam: "测试",
    accuracy: "准确率",
    score: "得分",
    reward: "奖励",
    pass_rate: "通过率",
    jsonLabel: "作者清单 JSON",
    reason: "未测量原因",
    resultRun: "结果实验",
    resultRound: "结果轮次",
    baselineRun: "基线实验（可选）",
    baselineRound: "基线轮次",
    advanced: "高级作者清单 JSON",
    advancedHint:
      "从表单生成或加载作者草稿，再编辑以说明被拒绝的尝试、尝试注释或 variation_groups。启用后将提交此 JSON，而非表单。",
    generate: "从表单生成 JSON",
    load: "加载 JSON 文件",
    useJson: "使用 JSON 导出",
    privacy:
      "分享前请检查：证据包清点配置目录中的所有尝试，包括失败和未测量的尝试。作者文字和标识符可能包含隐私信息。原始轨迹、任务文本和补丁内容不会导出，但过滤无法发现所有秘密。",
    download: "导出并下载",
    choose: "请选择…",
    none: "无",
    bad: "请填写必填项并选择存在的轮次。",
    catalogError: "无法读取完整尝试目录。",
    invalid: "清单无效，或记录缺失 / 无效。请检查草稿及引用轮次。",
    limit: "配置目录或导出超过服务器限制；导出范围未缩小。",
    busy: "已有导出正在进行，请稍后重试。",
    unavailable: "证据导出不可用。",
    network: "无法连接证据服务。",
    jsonError: "作者草稿 JSON 无效。",
    tooLarge: "作者草稿超过 1 MiB 请求上限。",
    working: "正在导出…",
    success: "已开始下载。",
    empty: "未发现尝试。",
  };
  const t = window.HGI18n.language === "zh" ? zh : en;
  const $ = (id) => document.getElementById(id);
  document.querySelectorAll("[data-e]").forEach((el) => {
    el.textContent = t[el.dataset.e];
  });
  document.title = "HarnessGrad · " + t.title;
  $("json").setAttribute("aria-label", t.jsonLabel);
  const statusLabels = {
    measured: t.measured,
    failed: t.failed,
    stopped: t.stopped,
    invalid: t.invalidStatus,
    unmeasured: t.unmeasured,
  };
  const scoreKindLabels = {
    training: t.training,
    exam: t.exam,
    accuracy: t.accuracy,
    score: t.score,
    reward: t.reward,
    pass_rate: t.pass_rate,
  };
  function labelFor(labels, value) {
    return Object.prototype.hasOwnProperty.call(labels, value)
      ? labels[value]
      : value;
  }
  let runs = [],
    ready = false;
  const message = (text) => {
    $("message").textContent = text;
  };
  function option(select, value, label) {
    const el = document.createElement("option");
    el.value = value;
    el.textContent = label;
    select.appendChild(el);
  }
  function populateRun(select, optional = false) {
    select.replaceChildren();
    option(select, "", optional ? t.none : t.choose);
    runs.forEach((run) => option(select, run.run_id, run.run_id));
  }
  function populateRound(runSelect, roundSelect, wanted = "") {
    roundSelect.replaceChildren();
    option(roundSelect, "", t.choose);
    const run = runs.find((x) => x.run_id === runSelect.value);
    (run?.rounds || []).forEach((x) => {
      const score = typeof x.score === "number" && Number.isFinite(x.score)
        ? new Intl.NumberFormat(window.HGI18n.language, { maximumSignificantDigits: 6 }).format(x.score)
        : "—";
      if (Number.isSafeInteger(x.round) && x.round >= 0)
        option(
          roundSelect,
          String(x.round),
          `${x.round} · ${score}${x.score_kind ? " (" + labelFor(scoreKindLabels, x.score_kind) + ")" : ""}`,
        );
    });
    roundSelect.value = String(wanted);
  }
  function pair(run, round, optional = false) {
    populateRun(run, optional);
    run.addEventListener("change", () => populateRound(run, round));
    populateRound(run, round);
  }
  function field(label, control, wide = false) {
    const wrapper = document.createElement("label");
    if (wide) wrapper.className = "wide";
    const span = document.createElement("span");
    span.textContent = label;
    wrapper.append(span, control);
    return wrapper;
  }
  function input(cls, tag = "input") {
    const el = document.createElement(tag);
    el.className = cls;
    return el;
  }
  function addConcern() {
    const box = document.createElement("div");
    box.className = "concern";
    const head = document.createElement("div");
    head.className = "concern-head";
    const strong = document.createElement("strong");
    strong.textContent = t.concern;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "btn btn--sm";
    remove.textContent = t.remove;
    remove.addEventListener("click", () => box.remove());
    head.append(strong, remove);
    const grid = document.createElement("div");
    grid.className = "concern-grid";
    const id = input("id"),
      concern = input("concern-text", "textarea"),
      status = input("status", "select"),
      reason = input("reason", "textarea"),
      rr = input("result-run", "select"),
      rn = input("result-round", "select"),
      br = input("baseline-run", "select"),
      bn = input("baseline-round", "select");
    option(status, "measured", t.measured);
    option(status, "unmeasured", t.unmeasured);
    pair(rr, rn);
    pair(br, bn, true);
    const reasonField = field(t.reason, reason, true),
      resultFields = [
        field(t.resultRun, rr),
        field(t.resultRound, rn),
        field(t.baselineRun, br),
        field(t.baselineRound, bn),
      ];
    const sync = () => {
      const un = status.value === "unmeasured";
      reasonField.hidden = !un;
      resultFields.forEach((f) => (f.hidden = un));
    };
    status.addEventListener("change", sync);
    sync();
    grid.append(
      field(t.id, id),
      field(t.status, status),
      field(t.concern, concern, true),
      reasonField,
      ...resultFields,
    );
    box.append(head, grid);
    $("concerns").appendChild(box);
  }
  function value(root, cls) {
    return root.querySelector("." + cls).value.trim();
  }
  function point(run, round) {
    if (
      !run ||
      round === "" ||
      !runs
        .find((x) => x.run_id === run)
        ?.rounds.some((x) => String(x.round) === round)
    )
      throw Error("invalid");
    return { run_id: run, round: Number(round) };
  }
  function draft() {
    const submission = point(
      $("submission-run").value,
      $("submission-round").value,
    );
    const rule = $("rule").value.trim(),
      source = $("budget-source").value.trim();
    const rounds = $("budget-rounds").value,
      trials = $("budget-trials").value;
    if (
      !rule ||
      !source ||
      rounds === "" ||
      trials === "" ||
      !Number.isSafeInteger(Number(rounds)) ||
      Number(rounds) < 0 ||
      !Number.isSafeInteger(Number(trials)) ||
      Number(trials) < 1
    )
      throw Error("invalid");
    const requests = [...document.querySelectorAll(".concern")].map((box) => {
      const id = value(box, "id"),
        concern = value(box, "concern-text");
      if (!id || !concern) throw Error("invalid");
      if (value(box, "status") === "unmeasured") {
        const reason = value(box, "reason");
        if (!reason) throw Error("invalid");
        return { id, concern, status: "unmeasured", runs: [], reason };
      }
      const result = point(
        value(box, "result-run"),
        value(box, "result-round"),
      );
      const req = {
        id,
        concern,
        runs: [result.run_id],
        result: { ...result, metric: "score" },
      };
      const baseline = value(box, "baseline-run");
      if (baseline) {
        const b = point(baseline, value(box, "baseline-round"));
        req.runs = [...new Set([b.run_id, result.run_id])];
        req.vs_baseline = { baseline_run: b.run_id, baseline_round: b.round };
      }
      return req;
    });
    if (!requests.length) throw Error("invalid");
    return {
      schema: "harnessgrad.evidence.v1",
      submission,
      requests,
      attempts: [],
      selection: { reported: submission, rule, rejected: [] },
      budget: { rounds: Number(rounds), trials: Number(trials), source },
    };
  }
  $("budget-trials").min = "1";
  pair($("submission-run"), $("submission-round"));
  addConcern();
  $("add").addEventListener("click", addConcern);
  $("generate").addEventListener("click", () => {
    try {
      $("json").value = JSON.stringify(draft(), null, 2);
      message("");
    } catch (_) {
      message(t.bad);
    }
  });
  $("load").addEventListener("change", async (e) => {
    try {
      const file = e.target.files[0];
      if (!file) return;
      if (file.size > 1048562) {
        message(t.tooLarge);
        return;
      }
      const text = await file.text();
      JSON.parse(text);
      $("json").value = text;
      $("use-json").checked = true;
      message("");
    } catch (_) {
      message(t.jsonError);
    }
  });
  $("form").noValidate = true;
  $("form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!ready) {
      message(t.catalogError);
      return;
    }
    let manifest;
    try {
      manifest = $("use-json").checked ? JSON.parse($("json").value) : draft();
      if (!manifest || typeof manifest !== "object" || Array.isArray(manifest))
        throw Error();
    } catch (_) {
      message($("use-json").checked ? t.jsonError : t.bad);
      return;
    }
    const body = $("use-json").checked
      ? '{\"manifest\":' + $("json").value + "}"
      : JSON.stringify({ manifest });
    if (new TextEncoder().encode(body).length > 1048576) {
      message(t.tooLarge);
      return;
    }
    $("export").disabled = true;
    message(t.working);
    try {
      const res = await fetch("/api/evidence/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });
      if (!res.ok) {
        let payload = {};
        try {
          payload = await res.json();
        } catch (_) {}
        const errors = {
          "evidence-limit-exceeded": t.limit,
          "evidence-busy": t.busy,
          "invalid-manifest": t.invalid,
          "evidence-unavailable": t.unavailable,
        };
        message(errors[payload.error] || t.unavailable);
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "evidence-export.json";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      message(t.success);
    } catch (_) {
      message(t.network);
    } finally {
      $("export").disabled = false;
    }
  });
  $("scope").textContent = t.loading;
  fetch("/api/evidence/catalog")
    .then(async (response) => {
      if (!response.ok) {
        let error = {};
        try {
          error = await response.json();
        } catch (_) {}
        throw Error(error.error || "");
      }
      return response.json();
    })
    .then((data) => {
      if (
        !Array.isArray(data.runs) ||
        data.scope?.kind !== "configured_runs_root" ||
        data.scope.run_count !== data.runs.length
      )
        throw Error();
      runs = data.runs;
      ready = true;
      $("scope").textContent =
        `${t.scope} ${t.count}: ${data.scope.run_count}.`;
      $("catalog").replaceChildren();
      if (!runs.length) $("catalog").textContent = t.empty;
      runs.forEach((run) => {
        const span = document.createElement("span");
        span.textContent = `${run.run_id} · ${labelFor(statusLabels, run.status)} · ${run.rounds.length} ${t.round}`;
        $("catalog").appendChild(span);
      });
      pair($("submission-run"), $("submission-round"));
      document.querySelectorAll(".concern").forEach((box) => {
        pair(
          box.querySelector(".result-run"),
          box.querySelector(".result-round"),
        );
        pair(
          box.querySelector(".baseline-run"),
          box.querySelector(".baseline-round"),
          true,
        );
      });
      const params = new URLSearchParams(location.search),
        run = params.get("run"),
        round = params.get("round");
      if (run && runs.some((x) => x.run_id === run)) {
        $("submission-run").value = run;
        populateRound($("submission-run"), $("submission-round"), round ?? "");
        const first = document.querySelector(".concern");
        first.querySelector(".result-run").value = run;
        populateRound(
          first.querySelector(".result-run"),
          first.querySelector(".result-round"),
          round ?? "",
        );
      }
    })
    .catch((error) => {
      $("scope").textContent =
        error.message === "evidence-limit-exceeded" ? t.limit : t.catalogError;
    });
})();
