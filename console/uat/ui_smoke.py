#!/usr/bin/env python3
"""控制台的浏览器 smoke：只测"用户眼睛能看到的那一层"。

为什么不用静态检查：最近三个 bug 全部通过了静态检查 ——

  * 设置弹窗"关不掉"：JS 里 `el.hidden = true` 生效，属性是 true，但 `.scrim { display:grid }`
    盖住了浏览器的 `[hidden]` 规则。查属性 = 通过，查画面 = 没关。
  * 侧栏拖拽后压住正文：`.app` 的 grid 轨道没变，只有 `.side` 变宽。
  * 删除一段 DOM 后旧绑定抛错，整个 `bind()` 中断，文件树显示"读取失败"。

所以这里一律用 `getComputedStyle` / `getBoundingClientRect` 判定，不读属性。

唯一读源码的例外是 `submit.js` 里 `renderResult` 的重复定义：两份定义字节相同，第二份
静默替换第一份，浏览器里观察不到任何差别（改错一份等于没改，页面照旧）—— 只有数一数
源码里的定义次数才看得出来。

    python3 console/uat/ui_smoke.py --url http://127.0.0.1:4484
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
RUN = "appworld-study-sica_ci-20261019T1816Z"


def visible(page, selector: str) -> str:
    return page.eval_on_selector(selector, "e => getComputedStyle(e).display")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:4460")
    args = ap.parse_args()
    base = args.url.rstrip("/")
    failures: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))

        def check(ok: bool, label: str) -> None:
            print(("PASS " if ok else "FAIL ") + label)
            if not ok:
                failures.append(label)

        # ── 设置：打开 → 换分节 → 关闭，按显示状态判定 ──────────────────────
        for path in ("/", "/launch.html", "/harness.html"):
            page.goto(base + path, wait_until="networkidle")
            page.wait_for_timeout(700)
            page.click("#shell-open-settings")
            page.wait_for_timeout(400)
            check(visible(page, "#sh-set") != "none", f"{path} 设置能打开")
            page.click("#sh-set-nav button[data-section='models']")
            page.wait_for_timeout(300)
            check(page.eval_on_selector_all(".settings__row", "e => e.length") >= 3,
                  f"{path} 模型分节有三类端点")
            page.click("#sh-set-close")
            page.wait_for_timeout(400)
            check(visible(page, "#sh-set") == "none", f"{path} 设置关得掉（显示状态）")

        # ── 主题：深色下不能留浅底控件 ────────────────────────────────────
        # 侧栏折叠把手曾经在深色主题里是个白块：tokens.css 的 `--hg-alias-*` 只在
        # `body[data-jl-dark]` 下翻色，而主题开关只翻了 `:root[data-theme]`。
        # 这一条按**计算出来的亮度**判定，跟上面设置弹窗那条同一个道理。
        page.goto(base + "/", wait_until="networkidle")
        page.wait_for_timeout(700)
        page.click("#shell-open-settings")
        page.wait_for_timeout(400)
        page.click("#sh-theme button[data-theme='dark']")
        page.wait_for_timeout(400)
        check(page.evaluate("() => document.documentElement.dataset.theme") == "dark", "深色主题切得动")
        tone = page.eval_on_selector("#sidebar-toggle", """e => {
            const m = getComputedStyle(e).backgroundColor.match(/\d+(\.\d+)?/g).map(Number);
            return (m[0] + m[1] + m[2]) / 3;
        }""")
        check(tone < 110, f"深色下侧栏把手不是浅底（亮度 {tone:.0f}）")
        page.click("#sh-theme button[data-theme='light']")
        page.wait_for_timeout(300)
        page.click("#sh-set-close")
        page.wait_for_timeout(300)
        check(page.evaluate("() => document.documentElement.dataset.theme") == "light", "切回浅色")

        # ── 实验列表：九列一张表，密度、徽标、对齐、不溢出 ──────────────────
        # 这一节是"设计系统迁移"的回归口：表格混用两套样式、某一行把表撑宽 90px 导致最右
        # 的"更新"列被切到可视区外、行被折行撑到 79px —— 静态检查全都看不出来。
        page.goto(base + "/#/runs", wait_until="networkidle")
        page.wait_for_timeout(1500)
        check(page.eval_on_selector_all("#runs-table tbody tr[data-run]", "e => e.length") > 5,
              "实验列表渲染出行")
        check(page.eval_on_selector("#runs-table table", "e => e.className") == "tbl runs",
              "实验列表用设计系统的表（.tbl.runs）")
        overflow = page.evaluate("""() => {
            const b = document.querySelector('#runs-table');
            return b.scrollWidth - b.clientWidth;
        }""")
        check(overflow <= 1, f"实验列表不横向溢出（{overflow}px）")
        heights = page.eval_on_selector_all("#runs-table tbody tr[data-run]",
                                           "els => els.map(e => e.getBoundingClientRect().height)")
        check(max(heights) < 64, f"行高密（{max(heights):.0f}px，不折行）")
        badges = page.eval_on_selector_all("#runs-table .run-status .badge", """els => els.map(e => {
            const r = e.getBoundingClientRect(), c = getComputedStyle(e);
            return {h: Math.round(r.height), clip: e.scrollWidth > Math.ceil(r.width) + 1, color: c.color};
        })""")
        check(len(badges) > 5, f"状态用徽标（{len(badges)} 个）")
        check(len({b["h"] for b in badges}) == 1, "徽标高度一致")
        check(not any(b["clip"] for b in badges), "徽标文字不被裁掉")
        nums = page.eval_on_selector_all("#runs-table tbody td.num", """els => els.slice(0, 5).map(e =>
            [getComputedStyle(e).textAlign, getComputedStyle(e).fontVariantNumeric])""")
        check(all(t == "right" for t, _ in nums), "数字列右对齐")
        check(all("tabular-nums" in v for _, v in nums), "数字列等宽数字")
        active = page.eval_on_selector("#runs-table tbody tr[data-active='true']", """e => {
            const c = getComputedStyle(e);
            return {shadow: c.boxShadow, border: c.borderLeftWidth};
        }""")
        check("inset" in active["shadow"] and active["border"] == "0px",
              "选中行的左强调线不吃列宽（inset 阴影，不加 border）")
        search = page.eval_on_selector("#f-q", """e => ({cls: e.className, inline: e.getAttribute("style"),
            w: Math.round(e.getBoundingClientRect().width)})""")
        check("input" in search["cls"] and not search["inline"], "筛选输入框走设计系统控件（无行内样式）")
        check(search["w"] == 200, f"筛选输入框宽度按控件（{search['w']}px）")

        # ── 版本：run 有选项、轮次跟着 run 变 ──────────────────────────────
        page.goto(base + "/?mode=harness#/version", wait_until="networkidle")
        page.wait_for_timeout(1600)
        options = page.eval_on_selector_all("#h-run option", "els => els.map(e => e.value)")
        check(len(options) > 1, f"版本页 run 列表非空（{len(options)} 条）")
        # 两个原生 select 现在是隐藏的数据持有者：轮次和 run 都从选择器里点。
        run_rows = page.eval_on_selector_all("#h-picker [data-run]", "els => els.map(e => e.dataset.run)")
        check(RUN in run_rows, "选择器里能找到那个 study run")
        page.click("#h-tree-btn")
        page.wait_for_timeout(400)
        page.click(f"#h-picker [data-run='{RUN}']")
        page.wait_for_timeout(1800)
        rounds = page.eval_on_selector_all("#h-picker [data-round]", "e => e.length")
        check(rounds > 1, f"选 run 后轮次出现（{rounds} 条）")
        check(page.eval_on_selector_all("#h-edit-btn", "e => e.length") == 1,
              "版本页有「编辑这一版」")

        # 起点选择器：族 → run → 轮次，选完 URL 要跟着走。
        # 选择器是开关：已经开着就别再点一下（点了会关，测试就自相矛盾）。
        if visible(page, "#h-picker") == "none":
            page.click("#h-tree-btn")
            page.wait_for_timeout(600)
        check(visible(page, "#h-picker") != "none", "起点选择器打得开")
        families = page.eval_on_selector_all("#h-picker [data-family]",
                                             "els => els.map(e => e.dataset.family)")
        check(len(families) > 1, f"族有 {len(families)} 个")
        page.click(f"#h-picker [data-family='{families[1]}']")
        page.wait_for_timeout(500)
        runs = page.eval_on_selector_all("#h-picker [data-run]", "els => els.map(e => e.dataset.run)")
        check(len(runs) > 1, f"族展开后有 run（{len(runs)} 个）")
        page.click(f"#h-picker [data-run='{runs[1]}']")
        page.wait_for_timeout(1600)
        rounds = page.eval_on_selector_all("#h-picker [data-round]", "els => els.map(e => e.dataset.round)")
        check(len(rounds) > 0, f"选 run 后出现轮次（{len(rounds)} 个）")
        page.click(f"#h-picker [data-round='{rounds[-1]}']")
        page.wait_for_timeout(1500)
        check("/" in page.evaluate("location.hash")[10:] and page.evaluate("location.hash").endswith(rounds[-1]),
              f"选轮次后 URL 指到那一轮（{page.evaluate('location.hash')}）")
        check(visible(page, "#h-picker") == "none", "选完轮次选择器收起")

        # ── 编辑：版本列表、默认选中、文件树可读 ────────────────────────────
        page.goto(base + f"/harness.html?candidate={RUN}-r20", wait_until="networkidle")
        page.wait_for_timeout(1600)
        check(page.eval_on_selector_all(".hg-srcbar__row", "e => e.length") >= 1, "版本列表有行")
        # The bar paints twice: the base harnesses arrive with the first paint, the
        # candidates come from a second fetch, and only then is the selected row in the DOM.
        # Waiting for the row instead of for a longer timeout is what makes this a check of
        # "a version ends up selected" rather than a check of how fast the machine is.
        try:
            page.wait_for_selector(".hg-srcbar__row[aria-selected='true']", timeout=8000)
        except Exception:                                            # noqa: BLE001
            pass
        selected = page.eval_on_selector_all(".hg-srcbar__row[aria-selected='true']",
                                             "els => els.map(e => e.dataset.source)")
        check(bool(selected), f"有一个版本被选中（{selected}）")
        check("读取失败" not in page.inner_text("#files"), "文件树读得出来")
        check(page.eval_on_selector_all("#thread", "e => e.length") == 1, "右侧是对话区")

        # ── 编辑这一版：点两次不该多出一份候选副本 ────────────────────────
        # `data/candidates/` 里曾经有 `…-r20` 到 `…-r20-6` 六份同样内容的副本，因为每打开
        # 一次就复制一份。这一条按 HTTP 层判定：第二次必须 `reused`，且目录集合不变。
        page.goto(base + "/", wait_until="networkidle")
        page.wait_for_timeout(800)

        def candidate_dirs() -> list[str]:
            return page.evaluate("""async () => {
                const r = await fetch('/api/harness/candidates');
                const d = await r.json();
                return (d.rows || []).map(x => x.dir).sort();
            }""")

        def from_round() -> dict:
            return page.evaluate("""async (run) => {
                const r = await fetch('/api/harness/from-round', {method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({run, round: 20})});
                return {status: r.status, ...(await r.json())};
            }""", RUN)

        before = candidate_dirs()
        first = from_round()
        second = from_round()
        after = candidate_dirs()
        check(first.get("source") in ("state", "snapshot", "replay"),
              f"「编辑这一版」报出了来源（{first.get('source')}）")
        check(first.get("candidate") and second.get("candidate") == first.get("candidate"),
              f"两次「编辑这一版」是同一份候选（{second.get('candidate')}）")
        check(second.get("reused") is True, f"第二次是复用而不是再复制（{second.get('reused')}）")
        check(after == before or (len(after) == len(before) + 1 and second.get("reused") is True),
              f"候选目录没有越点越多（{len(before)} → {len(after)}）")
        # 编辑页的版本说明要能说清这一版从哪来：快照签出的版本点名 snapshots/round-N.json。
        # 顺手把首屏的请求也数一遍：`loadSources()` 曾经连着两次 `await openSource(start)`，
        # 于是每次进编辑页都多取一次 `/api/harness/file`、多取一次这一版的历史，编辑器被
        # 写两遍。这是"多做了一件事"的 bug：DOM 前后一模一样，只有数请求才看得见。
        hits: list[str] = []
        page.on("request", lambda request: hits.append(request.url))
        hits.clear()
        page.goto(base + f"/harness.html?candidate={first['candidate']}", wait_until="networkidle")
        page.wait_for_timeout(1200)
        file_hits = [url for url in hits if "/api/harness/file" in url]
        check(len(file_hits) == 1, f"进编辑页只取一次文件（/api/harness/file ×{len(file_hits)}）")
        history_hits = [url for url in hits if "/api/runs/" in url and "/history" in url]
        check(len(history_hits) == 1, f"这一版的历史也只取一次（history ×{len(history_hits)}）")
        source_hint = page.inner_text("#source-hint").strip()
        check(bool(source_hint), f"编辑页的版本说明有一行（{source_hint}）")
        check(first.get("source") != "snapshot" or "快照" in source_hint,
              f"快照来源写进了版本说明（{source_hint}）")

        # ── 另存为：名字打开就已填好，而且当场存得进去（201，不是 409）──────────
        # `#name` 以前只在助手改完之后才被写：从版本条或 `?candidate=` 进来的版本是空的，
        # 点「另存为候选」先弹名字校验；照着正在编辑的这一版输入名字，又直接 409（同名候选
        # 已经在了）。所以这里既看预填，也用预填的名字真的存一次。
        prefilled = page.input_value("#name").strip()
        check(bool(prefilled), f"打开一版就有另存为的名字（{prefilled!r}）")
        check(re.fullmatch(r"[A-Za-z0-9._-]{1,48}", prefilled) is not None,
              f"预填的名字过页面自己的正则（{prefilled!r}）")
        check(prefilled and prefilled != first["candidate"],
              f"预填的名字不是正在编辑的这一版（{prefilled!r}）")
        opened_path = page.inner_text("#editing").strip()
        saved = page.evaluate("""async ({name, source, path, text}) => {
            const r = await fetch('/api/harness/save', {method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name, from: source, files: {[path]: text}})});
            return {status: r.status, ...(await r.json())};
        }""", {"name": prefilled, "source": first["candidate"], "path": opened_path,
               "text": page.eval_on_selector("#editor", "e => e.value")})
        check(saved.get("status") == 201,
              f"按预填的名字另存为是 201（{saved.get('status')}: {saved.get('error') or saved.get('candidate')}）")
        check(saved.get("written") == [opened_path],
              f"存下来的就是打开的那个文件（{saved.get('written')}）")

        # ── 版本条：run 的「最新 rN」必须是真正的轮次号 ──────────────────────
        # `/api/runs` 的 `rounds` 是**测点数**，页面却当轮次号渲染：只有 r0 的单点 run 被
        # 标成"最新 r1"，3 个测点的 run 被标成"最新 r3"。标签对不对，只能拿 `/api/runs/<id>`
        # 里的真实轮次反过来对。
        labels = dict(page.eval_on_selector_all(
            ".hg-srcbar__row[data-kind='run']",
            "els => els.map(e => [e.dataset.source, e.innerText])"))
        listed = page.evaluate("""async () => ((await (await fetch('/api/runs')).json()).rows || [])
            .slice(0, 200).map(x => ({id: x.id, points: x.rounds}))""")

        def real_latest_round(run_id: str) -> int | None:
            detail = page.evaluate("""async (id) =>
                (await (await fetch('/api/runs/' + encodeURIComponent(id))).json())""", run_id)
            numbers = [x.get("round") for x in (detail.get("rounds") or [])
                       if isinstance(x.get("round"), int)]
            return max(numbers) if numbers else None

        singles = [row for row in listed if row["points"] == 1]
        check(bool(singles), f"数据里有单点 run 可以校验（{len(singles)} 个）")
        if singles:
            run_id, real = singles[0]["id"], real_latest_round(singles[0]["id"])
            label = labels.get(f"run:{run_id}", "")
            check(real == 0 and re.search(r"最新 r0\b", label) is not None,
                  f"单点 run 标成 r0 而不是 r1（{label.splitlines()[-1] if label else '没有这一行'}）")
        multi = [row for row in listed if (row["points"] or 0) >= 3]
        check(bool(multi), f"数据里有多轮 run 可以校验（{len(multi)} 个）")
        if multi:
            run_id, real = multi[0]["id"], real_latest_round(multi[0]["id"])
            label = labels.get(f"run:{run_id}", "")
            check(re.search(rf"最新 r{real}\b", label) is not None,
                  f"多轮 run 的标签是最大轮次 r{real}（{label.splitlines()[-1] if label else '没有这一行'}）")

        # ── 删掉的那一页：地址是 404，导航里也不再提它 ─────────────────────
        gone = page.evaluate("""async () => (await fetch('/chat.html')).status""")
        check(gone == 404, f"/chat.html 已经不存在（{gone}）")
        linked = page.evaluate("""() => [...document.querySelectorAll('a[href]')]
            .map(a => a.getAttribute('href')).filter(h => h.includes('chat.html'))""")
        check(not linked, f"导航里没有指向已删页面的链接（{linked}）")

        # ── 启动：方法下拉存在且能进命令 ────────────────────────────────────
        page.goto(base + "/launch.html", wait_until="networkidle")
        page.wait_for_timeout(1500)
        methods = page.eval_on_selector_all("#f-method option", "e => e.length")
        check(methods > 2, f"启动页有方法可选（{methods} 个）")
        page.select_option("#f-method", "rrsi")
        page.wait_for_timeout(1500)
        check("--method-entrypoint" in page.inner_text("#cmd"), "选中的方法进了命令")

        # ── 提交页的结果渲染：只定义一次，渲染出来的字一个不少 ────────────────
        # `renderResult` 曾经在 `submit.js` 里定义了两遍（字节相同），第二份静默替换第一份：
        # 改其中一份等于没改，页面照旧。两份字节相同，所以浏览器里看不出任何差别 —— 这一条
        # 是本文件唯一读源码的断言。后面再把「出分」演示点一次，钉住渲染出来的内容。
        submit_js = (ROOT / "console" / "static" / "submit.js").read_text(encoding="utf-8")
        defined = re.findall(r"^  function renderResult\(", submit_js, re.M)
        check(len(defined) == 1, f"renderResult 只定义一次（{len(defined)} 次）")
        page.goto(base + "/submit.html", wait_until="networkidle")
        page.wait_for_timeout(900)
        page.click("[data-demo='scored']")
        page.wait_for_timeout(400)
        result_text = page.inner_text("#result-root")
        missing = [wanted for wanted in
                   ("结果有警告", "dev: 1 task invalid，未计入分母。", "分池结果",
                    "train", "dev", "exam", "0.500", "成本", "harness tokens", "身份")
                   if wanted not in result_text]
        check(not missing, f"出分结果渲染完整（缺 {missing}）")
        # 空值必须是「—」而不是 "null"/"undefined"：分池表里 exam 一列全是 null。
        check("null" not in result_text and "undefined" not in result_text,
              f"没有把 null/undefined 渲染成字（{result_text[:60]!r}…）")

        check(not errors, f"没有页面报错（{errors[:3]}）")
        browser.close()
    print(f"\nui smoke: {'PASS' if not failures else 'FAIL'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
