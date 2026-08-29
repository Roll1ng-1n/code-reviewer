"""#18 专家团拓扑 CLI 缝测试：确定性 router + Send fan-out + 四专家 + reducer 汇合。

ScriptedProvider by_expert 按专家身份出脚本（key 命中专家 system 提示词片段），
零网络覆盖验收标准：四专家各调用一次且 charter 正确、enabled 过滤生效、
findings 经 operator.add reducer 全量汇合无静默丢失、style 全 nit 且 ≤3
（charter 内约束 + 代码侧保险双保险）、#19 KB 空剔除 spec（可留空落图——
spec 参与需要 KB 非空，约定目录自动发现）、category 代码钉死、
verdict 按汇合结果派生。
"""

from __future__ import annotations

import json

# 各专家 charter 的标志性片段（移植自 eval/golden-set/compare.py arm_panel 对应臂）
CHARTER_LANDMARKS = {
    "architecture": "模块边界、依赖方向、接口契约",
    "logic": "业务逻辑的正确性与遗漏",
    "spec": "对照规范知识库（Spec KB）逐条检查违规",  # #19：KB 引用格式 charter
    "style": "吸收 nit 级问题",
}


def test_four_experts_fan_out_each_called_once(
    run_cli, diff_file, panel, monkeypatch, tmp_path
) -> None:
    """四专家并行 fan-out：各被调用一次，system 含各自 charter 标志片段。

    #19：spec 参与需要 KB 非空——tmp_path/specs/orders.md 触发约定目录自动
    发现（KB 空 → router 剔除 spec，见 test_empty_kb_drops_spec_from_panel）。
    """
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "orders.md").write_text(
        "# 订单规范\n\n> **TL;DR** 订单生命周期。\n\n## 创建订单\nPOST /api/orders。\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    code, out, err, provider = run_cli(
        panel(), "check", "--diff-file", str(diff_file), "--json"
    )
    assert code == 0
    assert len(provider.calls) == 4
    systems = [system for system, _ in provider.calls]
    for expert, landmark in CHARTER_LANDMARKS.items():
        owning = [s for s in systems if expert in s]
        assert len(owning) == 1  # 每位专家的 system 只含自己的 category 名
        assert landmark in owning[0]
        assert f'category 一律填 "{expert}"' in owning[0]


def test_enabled_config_filters_fan_out(
    run_cli, diff_file, panel, monkeypatch, tmp_path
) -> None:
    """enabled=[architecture, logic, spec] + KB 空 → 只有 2 次调用：#19 在 enabled
    过滤之上叠加「KB 空剔除 spec」（tmp_path 无 specs/），分支数运行时决定。"""
    (tmp_path / ".reviewer.yaml").write_text(
        "experts:\n  enabled: [architecture, logic, spec]\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    code, out, err, provider = run_cli(
        panel(), "check", "--diff-file", str(diff_file), "--json"
    )
    assert code == 0
    assert len(provider.calls) == 2
    systems = [system for system, _ in provider.calls]
    # style 专家未启用：既无 style charter，也不该出现 style 维度提示
    assert all("style" not in system for system in systems)
    assert all(CHARTER_LANDMARKS["style"] not in system for system in systems)
    # #19：spec 在 enabled 里但 KB 空 → router 剔除（可留空落图）
    assert all("spec" not in system for system in systems)
    assert all(CHARTER_LANDMARKS["spec"] not in system for system in systems)
    # 配置层 → router 输入的接线在 CLI 缝可观测
    assert run_cli.configs[-1].experts.enabled == ["architecture", "logic", "spec"]


def test_unknown_expert_names_ignored(
    run_cli, diff_file, panel, monkeypatch, tmp_path
) -> None:
    """enabled 里未知身份被 router 过滤（∩ 已知专家），只发送已知者。"""
    (tmp_path / ".reviewer.yaml").write_text(
        "experts:\n  enabled: [logic, nosuch]\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    code, out, err, provider = run_cli(
        panel(), "check", "--diff-file", str(diff_file), "--json"
    )
    assert code == 0
    assert len(provider.calls) == 1
    assert "logic" in provider.calls[0][0]


def test_all_experts_disabled_fails_loudly(
    run_cli, diff_file, panel, monkeypatch, tmp_path
) -> None:
    """enabled 过滤后为空 → 显式失败（70），绝不产出假 pass 报告；router 纯规则零模型调用。"""
    (tmp_path / ".reviewer.yaml").write_text("experts:\n  enabled: []\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, out, err, provider = run_cli(
        panel(), "check", "--diff-file", str(diff_file), "--json"
    )
    assert code == 70
    assert "experts.enabled 过滤后为空" in err
    assert provider.calls == []


def test_four_experts_findings_merged_without_loss(
    run_cli, diff_file, panel, make_finding, monkeypatch, tmp_path
) -> None:
    """四专家各出 2 条 → 报告 8 条：operator.add reducer 并行汇合零静默丢失。

    每位专家的脚本故意标错 category（旋转成别人的维度）——报告里的 category
    必须按产出者钉死，证明 category 由代码填本专家常量、不信任模型自我标注。
    style 脚本全 nit（否则会被 style 保险过滤，汇合数就不是 8）。
    #19：spec 参与需要 KB 非空（约定目录 tmp_path/specs/ 自动发现），
    KB 空时 spec 分支不存在、只会汇合 6 条。
    """
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "orders.md").write_text(
        "# 订单规范\n\n> **TL;DR** 订单生命周期。\n\n## 创建订单\nPOST /api/orders。\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    # 脚本 category 一律写错（rotate 一位），输出侧按产出专家断言
    scripted_category = {
        "architecture": "logic",
        "logic": "spec",
        "spec": "style",
        "style": "architecture",
    }
    scripts: dict[str, list[dict]] = {}
    for expert in ("architecture", "logic", "spec", "style"):
        # style 双保险要求全 nit；其余专家第一条 concern（verdict 派生可观测）
        severity_a = "nit" if expert == "style" else "concern"
        scripts[expert] = [
            make_finding(
                category=scripted_category[expert],
                file=f"{expert}-a.py",
                line=1,
                severity=severity_a,
                message=f"{expert}-a",
            ),
            make_finding(
                category=scripted_category[expert],
                file=f"{expert}-b.py",
                line=2,
                severity="nit",
                message=f"{expert}-b",
            ),
        ]
    code, out, err, provider = run_cli(
        panel(**scripts), "check", "--diff-file", str(diff_file), "--json"
    )
    assert code == 2  # 3 concern → concerns
    report = json.loads(out)
    findings = report["findings"]

    # 无静默丢失：8 条全量汇合，编号 F001..F008
    assert len(findings) == 8
    assert [f["id"] for f in findings] == [f"F{i:03d}" for i in range(1, 9)]

    # 跨专家确定性排序（文件, 严重度, 行号）+ category 按产出者钉死
    expected = [
        ("architecture-a.py", "concern", "architecture"),
        ("architecture-b.py", "nit", "architecture"),
        ("logic-a.py", "concern", "logic"),
        ("logic-b.py", "nit", "logic"),
        ("spec-a.py", "concern", "spec"),
        ("spec-b.py", "nit", "spec"),
        ("style-a.py", "nit", "style"),
        ("style-b.py", "nit", "style"),
    ]
    assert [(f["file"], f["severity"], f["category"]) for f in findings] == expected

    # verdict 按汇合结果派生
    assert report["summary"]["verdict"] == "concerns"
    assert report["summary"]["counts"] == {"blocker": 0, "concern": 3, "nit": 5}


def test_style_output_all_nit_capped_at_three(
    run_cli, diff_file, panel, make_finding
) -> None:
    """style 混入非 nit → 过滤丢弃；超上限 → 截断：产出全 nit 且 ≤3（双保险，stderr 可观测）。"""
    style_script = [
        make_finding(category="style", file="s1.py", line=1, severity="concern", message="越界一"),
        make_finding(category="style", file="s2.py", line=2, severity="nit", message="nit-1"),
        make_finding(category="style", file="s3.py", line=3, severity="blocker", message="越界二"),
        make_finding(category="style", file="s4.py", line=4, severity="nit", message="nit-2"),
        make_finding(category="style", file="s5.py", line=5, severity="nit", message="nit-3"),
        make_finding(category="style", file="s6.py", line=6, severity="nit", message="nit-4"),
    ]
    code, out, err, _ = run_cli(
        panel(style=style_script), "check", "--diff-file", str(diff_file), "--json"
    )
    assert code == 0  # style 只剩 nit，其余专家全空 → pass
    report = json.loads(out)
    style_rows = [f for f in report["findings"] if f["category"] == "style"]
    assert len(style_rows) == 3  # ≤3 截断
    assert all(row["severity"] == "nit" for row in style_rows)
    # 过滤发生在截断之前：2 条非 nit 先丢弃，剩 4 条 nit 截前 3
    assert {row["file"] for row in style_rows} == {"s2.py", "s4.py", "s5.py"}
    # 双保险可观测：丢弃计数 + 截断告警
    assert "丢弃 2 条非 nit 发现" in err
    assert "超出上限 3 条" in err


def test_empty_kb_drops_spec_from_panel(
    run_cli, diff_file, panel, make_finding, monkeypatch, tmp_path
) -> None:
    """KB 空（#19）→ router 剔除 spec：calls 无 spec 身份，报告无 spec 维度发现。

    行为契约升级：本票前 spec 恒调用、靠 charter「无规范库→输出空数组」兜底；
    本票后「可留空」落图——KB 空时 spec 分支在图结构上不存在（user story 8：
    没有规范文档时跳过规范检查而不是编造规则）。
    """
    logic_finding = make_finding(severity="concern", file="l.py", message="逻辑偏差")
    monkeypatch.chdir(tmp_path)  # 空快照：无 specs/ → KB 确定空
    code, out, err, provider = run_cli(
        panel(logic=[logic_finding]), "check", "--diff-file", str(diff_file), "--json"
    )
    assert code == 2
    report = json.loads(out)
    # 只调用 architecture / logic / style（3 次），spec 从未被调用
    assert len(provider.calls) == 3
    assert all("spec" not in system for system, _ in provider.calls)
    # 报告无 spec 维度发现，其余专家不受影响
    assert [f["category"] for f in report["findings"]] == ["logic"]
    assert report["summary"]["verdict"] == "concerns"
