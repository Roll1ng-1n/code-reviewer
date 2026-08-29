"""上下文组装（#17）：结构地图 + import 邻域，纯函数集。

Spec #12 两档上下文策略：下游专家拿到的不再是裸 diff——

- 结构地图（常驻）：``os.walk`` 目录树（排除版本控制 / 虚拟环境 / 构建产物等
  噪声目录）+ ``AGENTS.md`` 模块职责（存在时并入），总量 ≤500 行，超限截断可观测；
- import 邻域（按需）：stdlib ``ast`` 解析 diff 中被改 ``.py`` 文件的直接依赖
  （Import/ImportFrom → 仓库内文件路径，存在才收录），独立 token 预算
  （启发式 len(text)//4 ≈ 2000 tokens），超限按文件顺序截断可观测。

降级路径一律不致命且可观测：repo_root 为空（纯回放无快照）或被改文件不在
快照中 → 邻域为空、统计计数 0；ast 解析失败只跳过该文件。预算统计 dict 经
state.context_stats 并入 metadata（additive，见 contract.py 注释，不升
schema_version）。文件读取显式 encoding="utf-8"。
"""

from __future__ import annotations

import ast
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# 结构地图排除的目录名（版本控制 / 虚拟环境 / 构建产物等与模块职责无关的噪声）
EXCLUDED_DIRS: frozenset[str] = frozenset(
    {".git", "__pycache__", ".venv", "venv", "node_modules", ".tox", "dist", "build"}
)

# 结构地图总行数上限（含截断尾注）
STRUCTURE_MAP_MAX_LINES = 500

# import 邻域独立 token 预算（规格 1-2k 档取上限；只约束邻域文本本身）
NEIGHBORHOOD_BUDGET_TOKENS = 2000

# token 近似估算：平均 ~4 字符 / token（英文文本经验值，中文更碎）。
# 只求量级正确的预算控制、支撑「按文件顺序截断」的启发式，不是精确计量。
_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    """token 近似估算：len(text) // 4（启发式，见模块 docstring 说明）。"""
    return len(text) // _CHARS_PER_TOKEN


def build_structure_map(repo_root: str | None) -> tuple[str, dict[str, Any]]:
    """结构地图：``os.walk`` 目录树 + ``AGENTS.md`` 模块职责（存在时并入），≤500 行。

    每行 ``path/`` 形态（目录带尾斜杠、排序保证确定性）；repo_root/AGENTS.md
    存在时其内容作为「模块职责」块并入。总行数超限时按序截断并尾注
    「[truncated N lines]」。repo_root 为空或非目录 → 空地图（正常降级，不致命）。

    返回 (地图文本, 统计)：structure_map_lines / structure_map_truncated。
    """
    stats: dict[str, Any] = {"structure_map_lines": 0, "structure_map_truncated": False}
    if not repo_root:
        return "", stats
    root = Path(repo_root)
    if not root.is_dir():
        return "", stats

    lines: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # 就地过滤 + 排序：排除噪声目录（含 *.egg-info 通配）并保证确定性输出
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in EXCLUDED_DIRS and not d.endswith(".egg-info")
        )
        rel = Path(dirpath).relative_to(root)
        prefix = "" if rel == Path(".") else rel.as_posix() + "/"
        if prefix:
            lines.append(prefix)
        for name in sorted(filenames):
            lines.append(prefix + name)

    agents_md = root / "AGENTS.md"
    if agents_md.is_file():
        try:
            agents_content = agents_md.read_text(encoding="utf-8")
        except OSError:
            agents_content = ""  # 读不了 ≠ 审不了：跳过该块，不致命
        if agents_content.strip():
            lines.append("")
            lines.append("## 模块职责（AGENTS.md）")
            lines.extend(agents_content.rstrip().splitlines())

    truncated = len(lines) > STRUCTURE_MAP_MAX_LINES
    if truncated:
        dropped = len(lines) - (STRUCTURE_MAP_MAX_LINES - 1)
        lines = lines[: STRUCTURE_MAP_MAX_LINES - 1]
        lines.append(f"[truncated {dropped} lines]")
    stats["structure_map_lines"] = len(lines)
    stats["structure_map_truncated"] = truncated
    return "\n".join(lines), stats


def _changed_py_files(diff: str) -> list[str]:
    """从 diff 的 ``+++ b/`` 行收集被改 ``.py`` 文件（仓库相对路径，保序去重）。

    只认新侧路径（``+++ b/...``）；``/dev/null``（删除文件的新侧）、非 .py、
    以及 git 时间戳后缀（``path<TAB>2024-01-01``）一律不收。
    """
    files: list[str] = []
    for line in diff.splitlines():
        if not line.startswith("+++ b/"):
            continue
        path = line[len("+++ b/") :].split("\t")[0].strip()
        if path.endswith(".py") and path not in files:
            files.append(path)
    return files


def _module_candidates(base: Path, module: str) -> list[Path]:
    """模块名 → 候选文件路径：mod.a.b → mod/a/b.py 与 mod/a/b/__init__.py。

    存在性由调用方过滤——候选不限于仓库内（标准库 / 第三方会自然落空）。
    """
    if not module:
        return []
    pkg = base.joinpath(*module.split("."))
    return [pkg.parent / (pkg.name + ".py"), pkg / "__init__.py"]


def _relative_anchor(file_path: Path, level: int) -> Path | None:
    """相对导入锚点目录（PEP 328）：level=1 = 文件所在包，每多一个点再上溯一层。

    上溯越过仓库根仍不够 → None（越界导入放弃，不致命）。
    """
    anchor = file_path.parent
    for _ in range(level - 1):
        parent = anchor.parent
        if parent == anchor:
            return None
        anchor = parent
    return anchor


def _iter_dep_candidates(tree: ast.AST, file_path: Path, root: Path) -> Iterator[Path]:
    """遍历 AST 产出依赖文件候选路径（存在性 / 去重由调用方过滤）。

    - ``import mod.a.b`` → mod/a/b.py 或 mod/a/b/__init__.py（仓库根解析）
    - ``from mod.a import b`` → mod/a 本体与 mod/a/b（b 可能是子模块）双候选
    - 相对导入（level>0）锚点 = 文件所在包按 PEP 328 上溯 level-1 层
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield from _module_candidates(root, alias.name)
        elif isinstance(node, ast.ImportFrom):
            anchor = root
            if node.level:
                anchor = _relative_anchor(file_path, node.level)
                if anchor is None:
                    continue
            if node.module:
                yield from _module_candidates(anchor, node.module)
            for alias in node.names:  # from-import 的名字可能是子模块
                name = f"{node.module}.{alias.name}" if node.module else alias.name
                yield from _module_candidates(anchor, name)


def _dep_block(display_path: str, content: str) -> str:
    """单个依赖文件的邻域块：仓库相对路径标题 + python 代码围栏。"""
    return f"### {display_path}\n```python\n{content.rstrip()}\n```"


def build_import_neighborhood(
    diff: str, repo_root: str | None
) -> tuple[str, dict[str, Any]]:
    """import 邻域：被改 ``.py`` 文件的直接依赖源码，独立 token 预算（#17）。

    流程：``+++ b/`` 行收集被改 ``.py`` 文件 → 在 repo_root 下读源码 →
    ``ast.parse``（失败跳过该文件，不致命）→ 收集 Import/ImportFrom 候选 →
    解析为仓库内文件路径（存在才收录，排除被改文件自身与重复）→ 按依赖
    首现顺序拼装源码块。

    token 预算启发式：len(text)//4 ≈ token 数（近似估算，见模块 docstring），
    预算 2000 tokens；整文件放不下时截断该文件塞满剩余预算并尾注
    「[budget truncated]」，其后文件不再收录（按文件顺序）。

    降级不致命：repo_root 为空（纯回放无快照）或被改文件不在快照中 →
    邻域为空，统计计数 0 即可观测。

    返回 (邻域文本, 统计)：changed_py_files / unresolved_files /
    neighborhood_files / neighborhood_tokens / neighborhood_truncated。
    """
    stats: dict[str, Any] = {
        "changed_py_files": 0,
        "unresolved_files": 0,
        "neighborhood_files": 0,
        "neighborhood_tokens": 0,
        "neighborhood_truncated": False,
    }
    changed = _changed_py_files(diff)
    stats["changed_py_files"] = len(changed)
    if not repo_root or not changed:
        return "", stats
    root = Path(repo_root)

    # 依赖解析：保序去重，排除被改文件自身（自引用 import 不算依赖）
    changed_resolved = {(root / rel).resolve() for rel in changed}
    dep_paths: list[Path] = []
    seen: set[Path] = set()
    for rel in changed:
        file_path = root / rel
        if not file_path.is_file():
            stats["unresolved_files"] += 1  # 不在快照中（纯回放常见）：跳过
            continue
        try:
            tree = ast.parse(file_path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            stats["unresolved_files"] += 1  # 解析失败只跳过该文件，不致命
            continue
        for cand in _iter_dep_candidates(tree, file_path, root):
            if not cand.is_file():
                continue
            resolved = cand.resolve()
            if resolved in changed_resolved or resolved in seen:
                continue
            seen.add(resolved)
            dep_paths.append(cand)

    # 预算内拼装：超预算按文件顺序截断（首文件即超 → 截其内容塞满预算）
    blocks: list[str] = []
    used_tokens = 0
    for dep in dep_paths:
        try:
            content = dep.read_text(encoding="utf-8")
        except OSError:
            stats["unresolved_files"] += 1  # 依赖读不到：跳过，不致命
            continue
        try:
            display = dep.relative_to(root).as_posix()
        except ValueError:  # 理论不可达（候选均由 root 派生）；兜底显示原路径
            display = dep.as_posix()
        remaining = NEIGHBORHOOD_BUDGET_TOKENS - used_tokens
        cost = _estimate_tokens(content)
        if cost <= remaining:
            blocks.append(_dep_block(display, content))
            used_tokens += cost
            stats["neighborhood_files"] += 1
        else:
            if remaining > 0:
                blocks.append(
                    _dep_block(display, content[: remaining * _CHARS_PER_TOKEN])
                )
                stats["neighborhood_files"] += 1
            stats["neighborhood_truncated"] = True
            break

    text = "\n\n".join(blocks)
    if stats["neighborhood_truncated"]:
        text += "\n[budget truncated]"
    stats["neighborhood_tokens"] = _estimate_tokens(text)
    return text, stats


def assemble_context(
    *, diff: str, repo_root: str | None, description: str
) -> dict[str, Any]:
    """上下文组装节点入口（#17）：返回值即 state 增量（structure_map /
    neighborhood / context_stats 三键）。

    输入 diff 文本、repo_root（可空 = 纯回放无快照）与描述文本；description
    本身由入口层原样入 state，此处暂不消费——保留参数作为未来「按描述裁剪
    上下文」的扩展点。
    """
    structure_map, map_stats = build_structure_map(repo_root)
    neighborhood, hood_stats = build_import_neighborhood(diff, repo_root)
    return {
        "structure_map": structure_map,
        "neighborhood": neighborhood,
        "context_stats": {**map_stats, **hood_stats},
    }
