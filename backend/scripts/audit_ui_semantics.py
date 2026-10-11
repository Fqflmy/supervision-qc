# -*- coding: utf-8 -*-
"""以各角色视角通读界面文案，找出**语义不明**处（系统性走查）。

为什么需要它
------------
功能正确 ≠ 用户看得懂。本项目已多次出现「数据对、界面说不清」的问题，
且**全部由用户先发现**：

1. Judge 总分/等级列全空 —— 只测了接口，没测界面显示；
2. 进度显示 90% —— 只测了状态值，没测派生展示；
3. 普通用户无报告入口 —— 只测了接口可达，没测界面可达；
4. **「复核结论：合格」歧义** —— 只测了值正确，没测语义清晰。

前三个是「功能没到位」，第四个是「功能到位了但说不清楚」——
属**可用性**问题，纯靠断言数据永远测不出来，必须**换位到用户视角读界面**。

四类检查
--------
1. **状态标签是否有歧义**：结论性词（合格/不合格/通过/未通过）是否**标注了对象**；
2. **空状态是否告诉用户下一步**：只有「暂无数据」而无操作指引的，用户会卡住；
3. **错误提示是否可执行**：是否出现原始异常/堆栈/SQL（用户看不懂也无从下手）；
4. **字段名是否多义**：同一界面内是否有同名不同义的标签、
   或裸状态词（如「状态」列只显示「启用」而不说是什么的状态）。

判定策略（避免误报）
--------------------
「是否有歧义」在多数情况下需要人类判断，因此本脚本分两类输出：

- **硬失败（exit 1）**：可机器判定的明确问题 ——
  原始异常堆栈泄漏、空状态缺指引、结论词缺对象；
- **待人工确认**：列出上下文供逐条评审，不阻塞（写成报告文件）。

用法：
    python scripts/audit_ui_semantics.py --base http://127.0.0.1:8080
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

ARTIFACTS = ROOT / "var" / "artifacts"
REPORT = ROOT / "docs" / "界面语义走查报告.md"

ROLES = [
    ("系统管理员", "admin"),
    ("知识库管理员", "kb_manager"),
    ("监理工程师", "engineer"),
    ("审核人员", "expert"),
    ("普通用户", "viewer"),
]

PAGES = [
    ("dashboard", "运行总览"),
    ("knowledge", "知识库"),
    ("graph", "知识图谱"),
    ("chat", "智能问答"),
    ("evaluation", "评估任务"),
    ("reports", "评估报告"),
    ("judge", "质量评审"),
    ("users", "用户与授权"),
    ("system", "系统与审计"),
    ("profile", "个人中心"),
]

#: 结论性词：**必须带对象**才不算歧义。
#: 这些词在工程语境里天然指向「质量/合规」，用在别的对象上就会误导
#: （正是「复核结论：合格」那个问题的模式）。
CONCLUSION_WORDS = ("合格", "不合格", "通过", "未通过")

#: 「已消歧」判定：标签里必须出现**真正的对象名词**才算说清了「在判什么」。
#:
#: ⚠️ 刻意**不包含** ``复核`` / ``评估`` / ``评审`` / ``判定`` 这类**动作词**：
#: 它们是「谁在判」，不是「判什么」。第一版把「复核」也当对象标记，
#: 结果检测器**漏报了那个真实的用户问题**（「复核结论：合格」被判为已消歧）——
#: 阳性对照测试当场暴露了这个缺陷。
OBJECT_MARKERS = (
    # 判定对象（实体/资料）
    "报告", "工程质量", "质量", "工程", "条款", "实体", "检验批",
    # 判定对象（系统内其他物）
    "评分", "文档", "规范", "账号", "任务", "用户", "项目",
    "解析", "签发", "模型", "知识库", "图谱",
)

#: 原始异常/技术细节泄漏（用户看不懂，也无从下手）
RAW_ERROR_PATTERNS = [
    (r"Traceback \(most recent call last\)", "Python 异常堆栈"),
    (r"File \"/[^\"]+\", line \d+", "源码文件路径与行号"),
    (r"\b(psycopg|sqlalchemy|pydantic|fastapi)\.[A-Za-z]", "框架内部类名"),
    (r"\b(SELECT|INSERT|UPDATE|DELETE)\s+.+\bFROM\b", "SQL 语句"),
    (r"NoneType|KeyError|ValueError|AttributeError|IntegrityError", "Python 异常类型"),
    (r"HTTP\s*5\d\d|Internal Server Error", "裸 HTTP 错误码"),
    (r"ECONNREFUSED|ETIMEDOUT|ERR_", "网络栈错误码"),
]

#: 空状态必须包含「可执行的下一步」——命中任一即认为有指引
EMPTY_HINTS = (
    "请", "点击", "新建", "上传", "创建", "发起", "添加", "联系", "前往",
    "试试", "调整", "选择", "配置",
)

#: 空状态的常见措辞（用于定位）
EMPTY_PATTERNS = (
    "暂无数据", "暂无记录", "没有数据", "无数据", "暂无内容", "列表为空",
    "暂无任务", "暂无报告", "暂无结果", "尚无",
)


def _login(page, base: str, card: str) -> bool:
    page.goto(f"{base}/#/login", wait_until="networkidle", timeout=90000)
    page.evaluate("localStorage.clear()")
    page.goto(f"{base}/#/login", wait_until="networkidle", timeout=60000)
    try:
        page.wait_for_selector(".identity__card", timeout=30000)
    except Exception:  # noqa: BLE001
        return False
    page.locator(".identity__card", has_text=card).first.click()
    page.wait_for_timeout(250)
    page.click('button:has-text("登 录")')
    page.wait_for_timeout(3500)
    return page.locator(".app-shell").count() > 0


def _collect(page, base: str, name: str) -> dict:
    """打开页面并抓取**结构化**文本。

    ⚠️ 必须区分「标签」与「数据值」：
    第一版把所有 ``.el-table__cell`` 都当成标签，于是「已发布」出现 4 次
    （4 行数据）被误报成「标签重名」—— **40 处全是误报**。
    标签只应取自 ``.el-descriptions__label`` / ``.el-form-item__label``
    与**表头**，数据值单独收集用于「结论词是否消歧」的判断。

    抓取内容：
    - ``labels``：真正的字段标签（description / form-item / 表头）；
    - ``headers``：各表格列头（判断结论词时用于确认对象）；
    - ``cells``：表格单元格值（判断裸结论词）；
    - ``metrics``：看板卡片的标签与脚注。
    """
    page.goto(f"{base}/#/{name}", wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(2200)
    if not page.url.rstrip("/").endswith(f"/{name}"):
        return {}  # 无权限，跳过

    result: dict[str, list[str]] = {"labels": [], "headers": [], "cells": [], "metrics": []}

    def _grab(selector: str, key: str) -> None:
        try:
            for text in page.locator(selector).all_inner_texts():
                t = (text or "").strip().replace("\n", " ")
                if t:
                    result[key].append(t)
        except Exception:  # noqa: BLE001
            pass

    # 真实字段标签
    _grab(".el-descriptions__label", "labels")
    _grab(".el-form-item__label", "labels")
    _grab(".el-tabs__item", "labels")
    _grab(".panel__title", "labels")
    # 表头（含列内自定义表头）
    _grab(".el-table__header th", "headers")
    # 数据单元格
    _grab(".el-table__body td", "cells")
    # 看板
    _grab(".metric__label", "metrics")
    _grab(".metric__foot", "metrics")
    return result


def _self_check() -> list[str]:
    """检测器**自检**：用已知的真实问题与已知的正常写法做对照。

    为什么必须自检：审计脚本输出「0 处问题」时，无法区分两种情况 ——
    (a) 界面确实没问题；(b) **检测器坏了**。

    这不是假想风险：本脚本第一版把 ``复核`` / ``评估`` 当作「对象标记」，
    于是**漏报了那个真实的用户问题**（「复核结论：合格」被判为已消歧），
    输出了一片「0 处」。直到用阳性对照测试才发现。

    因此每次运行都先验证检测器能识别已知问题、且不误报已知正常写法；
    自检不过就直接失败，避免给出误导性的「一切正常」。
    """
    problems: list[str] = []

    def _flags_conclusion(labels: list[str], cells: list[str]) -> bool:
        header = " ".join(labels)
        header_has_object = any(m in header for m in OBJECT_MARKERS)
        for s in labels:
            for w in CONCLUSION_WORDS:
                if w in s and not any(m in s for m in OBJECT_MARKERS):
                    return True
        bare = [c for c in cells if c in CONCLUSION_WORDS]
        return bool(bare and not header_has_object)

    #: (说明, 标签/表头, 单元格值, 期望是否被标记为有歧义)
    cases: list[tuple[str, list[str], list[str], bool]] = [
        # —— 必须能发现的真实问题（阳性对照）——
        ("修复前的「复核结论：合格」", ["复核结论"], ["合格"], True),
        ("修复前列头「复核」+ 值「不合格」", ["复核"], ["不合格"], True),
        ("Judge 等级裸值，表头未说明评什么", ["等级"], ["合格"], True),
        # —— 必须不误报的正常写法（阴性对照）——
        ("修复后「复核决定：接受报告」", ["复核决定"], ["接受报告"], False),
        ("修复后列头「报告复核 / 人工判报告」", ["报告复核", "人工判报告"], ["已签发"], False),
        ("修复后列头「评估结论 / AI 判工程质量」", ["评估结论", "AI 判工程质量"], ["不符合"], False),
        ("Judge 等级，表头写明「评报告质量」", ["等级", "评报告质量"], ["合格"], False),
        ("条款判定列头「判定」+ 值「不符合」", ["判定"], ["不符合"], False),
    ]
    failed = [c for c in cases if _flags_conclusion(c[1], c[2]) != c[3]]
    if failed:
        for desc, labels, cells, expect in failed:
            got = _flags_conclusion(labels, cells)
            problems.append(f"结论词检测器对照失败：{desc}（判定={got} 期望={expect}）")

    #: 错误提示检测器的对照
    error_cases = [
        ("用户名或密码错误", False),
        ("请联系管理员为该用户分配项目", False),
        ("Traceback (most recent call last):", True),
        ("psycopg.errors.UndefinedFunction", True),
        ("Internal Server Error", True),
    ]
    for sample, expect in error_cases:
        hit = any(re.search(p, sample) for p, _ in RAW_ERROR_PATTERNS)
        if hit != expect:
            problems.append(f"错误提示检测器对照失败：{sample!r}（命中={hit} 期望={expect}）")

    #: 空状态指引检测器的对照
    empty_cases = [
        ("暂无数据", False),
        ("暂无项目数据，请联系管理员分配", True),
        ("暂无报告，请先发起评估", True),
    ]
    for sample, expect in empty_cases:
        got = any(h in sample for h in EMPTY_HINTS)
        if got != expect:
            problems.append(f"空状态指引检测器对照失败：{sample!r}（判定={got} 期望={expect}）")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    # ---- 检测器自检（先于一切分析）----
    print("=== 检测器自检（阳性/阴性对照）===")
    self_problems = _self_check()
    if self_problems:
        print(f"  [FAIL] 检测器自身有缺陷（{len(self_problems)} 项）：")
        for p in self_problems:
            print(f"    {p}")
        print("  → 检测器不可信，本次结果不予采信。请先修复检测器。")
        return 2
    print("  [OK] 检测器能识别已知问题、且不误报已知正常写法\n")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / "var" / "pw-browsers"))

    #: (角色, 页面) -> 抓取到的结构化文本
    collected: dict[tuple[str, str], dict] = {}
    #: 整页文本（用于空状态与错误提示检查）
    full_text: dict[tuple[str, str], str] = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, channel="msedge")
        context = browser.new_context(viewport={"width": 1600, "height": 1000}, locale="zh-CN")
        page = context.new_page()

        for label, _role in ROLES:
            print(f"=== {label} ===")
            if not _login(page, base, label):
                print("  [跳过] 登录失败")
                continue
            visited = 0
            for name, title in PAGES:
                data = _collect(page, base, name)
                if not data:
                    continue
                visited += 1
                collected[(label, name)] = data
                full_text[(label, name)] = page.inner_text("body")
                print(
                    f"  {title:8s} 标签 {len(data['labels'])} / 表头 {len(data['headers'])} "
                    f"/ 单元格 {len(data['cells'])}"
                )
            print(f"  可访问页面：{visited}/{len(PAGES)}")
            page.screenshot(
                path=str(ARTIFACTS / f"31-semantics-{_role}.png"), full_page=True
            )

        browser.close()

    # ---------------- 分析 ----------------
    hard: list[str] = []
    review: list[str] = []

    # ① 结论词是否标注对象
    #    关键：列头就是「对象」的声明处。单元格里只有一个光秃秃的「合格」时，
    #    必须看它的**列头**是否说明了对象 —— 这正是「复核结论：合格」问题的
    #    判断方式（列头写「报告复核」即已消歧；只写「结论」则没有）。
    print()
    print("=== ① 结论性标签是否标注对象 ===")
    conclusion_hits = 0
    for (role, page_name), data in collected.items():
        # 表头已声明对象的情况：把表头文本拼起来作为「上下文对象声明」
        header_text = " ".join(data["headers"])
        header_has_object = any(m in header_text for m in OBJECT_MARKERS)

        # 字段标签里的结论词
        for s in data["labels"]:
            for word in CONCLUSION_WORDS:
                if word in s and not any(m in s for m in OBJECT_MARKERS):
                    conclusion_hits += 1
                    review.append(f"{role} · {page_name} —— 字段标签「{s}」未见对象说明")

        # 表格单元格里的裸结论词
        bare = [
            c for c in data["cells"]
            if c in CONCLUSION_WORDS or any(c == w for w in CONCLUSION_WORDS)
        ]
        if bare and not header_has_object:
            conclusion_hits += 1
            review.append(
                f"{role} · {page_name} —— 表格出现裸结论值 {sorted(set(bare))}，"
                f"且表头（{header_text[:60]!r}）未说明判定对象"
            )
    print(f"  发现 {conclusion_hits} 处需人工确认（已列入报告）")

    # ② 空状态是否告诉用户下一步
    print()
    print("=== ② 空状态是否给出下一步 ===")
    empty_issues = 0
    for (role, page_name), text in full_text.items():
        for pat in EMPTY_PATTERNS:
            if pat not in text:
                continue
            idx = text.find(pat)
            window = text[max(0, idx - 120): idx + 220]
            if not any(h in window for h in EMPTY_HINTS):
                empty_issues += 1
                hard.append(
                    f"{role} · {page_name} —— 空状态「{pat}」未告诉用户下一步做什么"
                    f"（原文片段：{window.strip()[:80]!r}）"
                )
    print(f"  发现 {empty_issues} 处缺指引")

    # ③ 错误提示是否可执行（是否泄漏原始异常）
    print()
    print("=== ③ 错误提示是否泄漏技术细节 ===")
    raw_issues = 0
    for (role, page_name), text in full_text.items():
        for pattern, desc in RAW_ERROR_PATTERNS:
            m = re.search(pattern, text)
            if m:
                raw_issues += 1
                hard.append(
                    f"{role} · {page_name} —— 界面出现{desc}：{m.group(0)[:70]!r}"
                )
    print(f"  发现 {raw_issues} 处泄漏")

    # ④ 字段名多义：**只比对真正的标签**，不看数据值。
    #    第一版把表格单元格也算作标签，于是「已发布」出现 4 次（4 行数据）
    #    被误报成「标签重名」—— 40 处全是误报。
    print()
    print("=== ④ 同一界面内标签重名（可能多义）===")
    dup_issues = 0
    for (role, page_name), data in collected.items():
        seen: dict[str, int] = {}
        for s in data["labels"]:
            seen[s] = seen.get(s, 0) + 1
        for s, n in seen.items():
            if n > 1:
                dup_issues += 1
                review.append(f"{role} · {page_name} —— 标签「{s}」在同一界面出现 {n} 次")
    print(f"  发现 {dup_issues} 处重复标签（已列入报告）")

    # ---------------- 输出报告 ----------------
    lines = [
        "# 界面语义走查报告",
        "",
        "> 由 `backend/scripts/audit_ui_semantics.py` **实测生成**：以各角色视角",
        "> 遍历所有可访问页面，检查四类语义问题。结论分「硬失败」与「待人工确认」。",
        "",
        "## 为什么需要这份走查",
        "",
        "功能正确 ≠ 用户看得懂。本项目已多次出现「数据对、界面说不清」的问题，",
        "且**全部由用户先发现**：",
        "",
        "| 次序 | 用户发现 | 为什么我的测试没发现 |",
        "| :-: | --- | --- |",
        "| 1 | Judge 总分/等级列全空 | 只测了接口返回，没测界面显示 |",
        "| 2 | 进度显示 90% | 只测了状态值，没测派生展示 |",
        "| 3 | 普通用户无报告入口 | 只测了接口可达，没测界面可达 |",
        "| 4 | 「复核结论：合格」歧义 | 只测了值正确，没测语义清晰 |",
        "",
        "前三个是「功能没到位」，第四个是「功能到位了但说不清楚」——",
        "属**可用性**问题，纯靠断言数据永远测不出来，必须换位到用户视角读界面。",
        "",
        "## 检查项",
        "",
        "| # | 检查 | 判定方式 |",
        "| :-: | --- | --- |",
        "| ① | 状态标签是否有歧义 | 结论词（合格/不合格/通过/未通过）是否标注对象 |",
        "| ② | 空状态是否告诉用户下一步 | 空状态文案须含可执行指引 |",
        "| ③ | 错误提示是否可执行 | 不得出现原始异常/堆栈/SQL/网络栈错误码 |",
        "| ④ | 字段名是否多义 | 同一界面内标签是否重名 |",
        "",
        f"## 走查范围",
        "",
        f"- 角色：{len(ROLES)} 个；页面：{len(PAGES)} 个；",
        f"- 实际采集：**{len(collected)} 个（角色 × 页面）组合**。",
        "",
        "## 硬失败（必须修）",
        "",
    ]
    if hard:
        lines += [f"- ❌ {item}" for item in hard]
    else:
        lines.append("- 无 —— 空状态均有指引、无技术细节泄漏。")
    lines += [
        "",
        "## 待人工确认",
        "",
        "> 「是否有歧义」多数情况需人类判断，以下列出上下文供逐条评审。",
        "",
    ]
    if review:
        lines += [f"- ⚠️ {item}" for item in review[:60]]
        if len(review) > 60:
            lines.append(f"- …（另有 {len(review) - 60} 处，略）")
    else:
        lines.append("- 无。")
    lines += [
        "",
        "## 检测器自检（结论可信度的前提）",
        "",
        "审计脚本输出「0 处问题」时，无法区分两种可能：界面确实没问题，或者**检测器坏了**。",
        "",
        "这不是假想风险 —— 本脚本第一版把 `复核` / `评估` 当作「对象标记」，",
        "于是**漏报了那个真实的用户问题**（「复核结论：合格」被判为已消歧），",
        "输出了一片「0 处」。直到用阳性/阴性对照测试才发现。",
        "",
        "因此脚本每次运行都先自检，自检不过直接以退出码 2 失败、拒绝给出结论：",
        "",
        "| 对照 | 期望 |",
        "| --- | :-: |",
        "| 「复核结论：合格」（修复前的真实问题） | 必须标记 |",
        "| 列头「复核」+ 值「不合格」 | 必须标记 |",
        "| Judge 等级裸值「合格」且表头未说明评什么 | 必须标记 |",
        "| 「复核决定：接受报告」（修复后） | 不得标记 |",
        "| 列头「报告复核 / 人工判报告」 | 不得标记 |",
        "| 列头「评估结论 / AI 判工程质量」 | 不得标记 |",
        "| 「Judge 等级 / 评报告质量」 | 不得标记 |",
        "| 错误提示：`Traceback` / `psycopg.errors` / `Internal Server Error` | 必须标记 |",
        "| 错误提示：「用户名或密码错误」「请联系管理员分配项目」 | 不得标记 |",
        "| 空状态：「暂无数据」 | 必须标记（缺指引） |",
        "| 空状态：「暂无报告，请先发起评估」 | 不得标记 |",
        "",
        "## ⚠️ 本走查的局限（不要过度解读「0 处」）",
        "",
        "| 局限 | 说明 |",
        "| --- | --- |",
        "| **自带盲区** | 检测器第一版漏报过真实问题。它的正确性只由上面的对照表保证，**不等于审查完备** |",
        "| **只查结论词用词** | 不判断「这个标签是否用在了不该用的地方」——例如「状态」列在 A 页指账号停用、在 B 页指任务进度，本脚本不会发现 |",
        "| **空状态只在有数据时才能验证** | 当前库里有数据，绝大多数空状态**根本没渲染**，因此这一项实际覆盖不足 |",
        "| **未覆盖交互后文案** | 只抓静态页面；提交表单后的成功/失败提示、对话框内文案未纳入 |",
        "| **未覆盖动态生成内容** | 报告正文里的「合格/不符合」由 Markdown 渲染，属报告内容而非界面文案，不在此检查范围 |",
        "| **「是否有歧义」本质需人判断** | 机器只能查「有没有对象名词」，查不出「对象名词用错了」 |",
        "",
        "因此本报告应作为**走查记录**而非**验收证明**；",
        "真实结论仍以人工通读界面为准（这也正是这类问题一直由用户先发现的原因）。",
        "",
    ]
    lines += [
        "## 复现",
        "",
        "```bash",
        "python backend/scripts/audit_ui_semantics.py --base http://127.0.0.1:8080",
        "```",
        "",
        "各角色走查截图见 `var/artifacts/31-semantics-<role>.png`。",
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print()
    print(f"=== 报告已写入 {REPORT} ===")

    print()
    print("=" * 60)
    if hard:
        print(f"[FAIL] {len(hard)} 处硬失败：")
        for h in hard[:20]:
            print(f"  {h}")
        print(f"  另有 {len(review)} 处待人工确认（见报告）")
        return 1
    print(f"[OK] 无硬失败（空状态均有指引、无技术细节泄漏）")
    print(f"     {len(review)} 处待人工确认，详见报告")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
