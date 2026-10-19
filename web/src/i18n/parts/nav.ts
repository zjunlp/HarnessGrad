/**
 * The shell: the rail, the three destinations, and the banner that says which backend is
 * live.
 *
 * The banner is the one string in the app that must never be softened in translation. It
 * exists so a screenshot cannot carry a fixture number to a slide without saying it is a
 * fixture, and the Chinese wording keeps the same force as the English.
 */
export const nav = {
  en: {
    "app.tagline": "harness self-improvement",
    "app.version": "v0.1 scaffold",
    "app.noSuchScreen": "No such screen",
    "app.noSuchScreenHint":
      "The five screens are Experiments, Run detail, Compare, Task detail and Launch.",
    "app.backToExperiments": "Back to Experiments",
    "nav.experiments": "Experiments",
    "nav.experimentsHint": "Every run, and whether its numbers can be quoted",
    "nav.compare": "Compare",
    "nav.compareHint": "Two runs side by side: which tasks moved",
    "nav.launch": "Launch",
    "nav.launchHint": "Configure a run and read the argv it would produce",
    "nav.shortcuts": "Shortcuts",
    "nav.fixtureRuns": "Fixture runs",
    "nav.changeTheme": "Change theme",
    "nav.theme": "Theme",
    "nav.themeLight": "Light",
    "nav.themeDark": "Dark",
    "nav.themeSystem": "System",
    "nav.themeCurrent": "current",
    "nav.changeLanguage": "Change language",
    "nav.language": "Language",
    "nav.languageAuto": "Automatic",
    "nav.languageAutoHint": "Follow the browser",
    "backend.checking": "Checking…",
    "backend.labelForced": "Fixtures (forced)",
    "backend.labelLive": "Live API",
    "backend.labelUnreachable": "Live API (unreachable)",
    "backend.labelFixtures": "Fixtures",
    "backend.noteForced": "Fixtures are forced by VITE_API=fixture.",
    "backend.noteChecking": "Checking for the read API at {base}/api/health…",
    "backend.noteForcedBuild":
      "VITE_API=fixture: this build reads the curated fixture subset (runs/ds-dgm, runs/ds-rrsi) and never contacts the read API.",
    "backend.noteLive":
      "Live read API at {api}: {n} run row(s) read from runs/. A run that is being written right now appears with points: 0, not hidden.",
    "backend.noteUnreachable":
      "VITE_API=live, but the read API at {api} did not answer ({detail}). Requests will fail rather than silently fall back to fixtures.",
    "backend.noteFixtures":
      "Fixtures only: the read API at {api} did not answer ({detail}), so this build falls back to a real subset of runs/ds-dgm and runs/ds-rrsi. Start it with `python3 web/server/serve.py --port 8791 --runs-root runs` for live data.",
    "nav.servingLive": "Serving {n} run row(s) from the read API.",
    "nav.servingFixtures": "Reading the curated fixture subset; the read API is not in use.",
  },
  zh: {
    "app.tagline": "harness 自我改进",
    "app.version": "v0.1 骨架",
    "app.noSuchScreen": "没有这个页面",
    "app.noSuchScreenHint": "一共五个页面:实验、run 详情、对比、题目详情、启动。",
    "app.backToExperiments": "回到实验",
    "nav.experiments": "实验",
    "nav.experimentsHint": "所有 run,以及它的数字能不能被引用",
    "nav.compare": "对比",
    "nav.compareHint": "两个 run 并排:哪些题动了",
    "nav.launch": "启动",
    "nav.launchHint": "配一个 run,并读出它会产生的 argv",
    "nav.shortcuts": "快捷入口",
    "nav.fixtureRuns": "样例 run",
    "nav.changeTheme": "切换主题",
    "nav.theme": "主题",
    "nav.themeLight": "浅色",
    "nav.themeDark": "深色",
    "nav.themeSystem": "跟随系统",
    "nav.themeCurrent": "当前",
    "nav.changeLanguage": "切换语言",
    "nav.language": "语言",
    "nav.languageAuto": "自动",
    "nav.languageAutoHint": "跟随浏览器",
    "backend.checking": "检查中…",
    "backend.labelForced": "样例(强制)",
    "backend.labelLive": "实时 API",
    "backend.labelUnreachable": "实时 API(不通)",
    "backend.labelFixtures": "样例",
    "backend.noteForced": "VITE_API=fixture 强制使用样例。",
    "backend.noteChecking": "正在检查 {base}/api/health 上的读 API…",
    "backend.noteForcedBuild":
      "VITE_API=fixture:这个构建读内置样例子集(runs/ds-dgm、runs/ds-rrsi),完全不访问读 API。",
    "backend.noteLive":
      "实时读 API 在 {api}:从 runs/ 读到 {n} 行 run 记录。正在写入的 run 会以 points: 0 出现,而不是被隐藏。",
    "backend.noteUnreachable":
      "VITE_API=live,但 {api} 上的读 API 没有响应({detail})。请求会直接失败,不会悄悄退回样例。",
    "backend.noteFixtures":
      "只有样例:{api} 上的读 API 没有响应({detail}),所以这个构建退回 runs/ds-dgm 与 runs/ds-rrsi 的真实子集。想要实时数据,用 `python3 web/server/serve.py --port 8791 --runs-root runs` 起它。",
    "nav.servingLive": "正在从读 API 提供 {n} 行 run 记录。",
    "nav.servingFixtures": "正在读内置样例子集;没有使用读 API。",
  },
} as const;
