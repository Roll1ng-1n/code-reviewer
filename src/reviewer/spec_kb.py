"""Spec KB 加载（#19，ADR-0001 文档式落地）：三层来源合并、内容去重、解析与降级渲染。

来源优先级（高 → 低）：``--spec``（CLI，可重复 / 逗号分隔，文件或目录）>
``spec_kb.paths``（配置，目录递归收 *.md）> 约定目录自动发现
（``<repo_root>/specs/`` 与 ``<repo_root>/.reviewer/specs/``，存在即生效）。
相对路径解析基准：回放模式 = 快照仓库（--repo），git 模式 = 进程 cwd（CLI 层
折算进 :attr:`SpecSources.base_dir`）；约定目录恒以 repo_root 为基准。

合并语义（平级不覆盖）：

- 内容 sha256 相同 → 同一文档只计一次（同内容两路径 → documents 计 1）；
- 文档名相同、内容不同 → 高优先层覆盖低优先层。实现按「约定 → 配置 → CLI」
  的优先级升序逐层合并，后到者覆盖——与「CLI 层最高优先」等价；
- 其余情形一律平级共存。语义冲突（多文档互相矛盾）不做仲裁，属人工维护
  责任（ADR-0001），系统只按内容 hash 去重。

解析（每文档）：

- TL;DR 块（启发式）：首个 ``>`` 引用行命中 TL;DR 变体（``> **TL;DR**`` /
  ``> TL;DR：`` / ``> TLDR`` 等，大小写不敏感、容忍加粗与中英文冒号）即取该
  连续引用块整块；无则空串（降级时该文档只贡献命中的章节）；
- 章节：``## `` 级标题切分（``#`` / ``###`` 不切），记录「文档名 § 节名」；
  首个 ## 之前的序言（无节名、降级时无法按路径过滤也无法被引用）不作为章节。

降级：全部文档总行数 >500 行 → spec 专家上下文 = 各文档 TL;DR 全量 + 文档名
与 diff 改动路径「前缀相交」的章节（启发式：specs/orders.md 的路径段/词干
{specs, orders} 与改动路径 src/orders/create.py 的段 {src, orders, create}
相交即整文档收录——宽松启发式，宁可多注入少量无关章节，不可漏掉相关规范；
节名不编码路径，故过滤粒度为文档级）；≤500 行全量注入。降级尾注
「[spec kb truncated: ...]」进上下文可观测。

加载产物 :class:`SpecKB`：documents（去重合并后文档列表）/ rendered_text
（注入 spec 专家的上下文，空库为空串）/ stats（预算与降级统计）/ hash
（sha256(按路径排序内容拼接) 前 12 位，空库为 None）。metadata.spec_kb =
{loaded, documents, hash} 三键（additive，见 contract.py 注释）。

缺失来源降级不致命：CLI 显式 --spec 指向不存在路径 → stderr 告警后跳过
（显式意图，typo 代价高）；配置与约定目录缺失/为空 → 静默跳过（KB「可留空」
是规格语义，缺省配置 paths=["specs/"] 不该对无 specs/ 的仓库刷告警）。
单个文档读取失败同理告警跳过。文件读取显式 encoding="utf-8"；零新依赖
（hashlib 为标准库），纯本地零模型调用。
"""

from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 全部文档总行数上限：超过即降级（TL;DR 全量 + 命中章节）
KB_LINE_LIMIT = 500

# 约定目录（相对 repo_root），存在即生效；数组顺序 = 合并顺序，
# .reviewer/specs 靠后 → 同名不同内容时覆盖 specs/（与「后层覆盖前层」惯例一致）
CONVENTION_DIRS: tuple[str, ...] = ("specs", ".reviewer/specs")

# TL;DR 引用块首行变体：> **TL;DR** / > TL;DR： / > TLDR（大小写不敏感，
# 容忍加粗星号与中英文冒号——启发式只认前缀，块内其余行不设限）
_TLDR_FIRST_LINE = re.compile(r"^\s*>\s*\*{0,2}\s*TL;?\s*DR", re.IGNORECASE)

# ## 级章节标题（^## 后必须紧跟空白 → ### 与 ##无空格 均不命中）
_SECTION_HEADING = re.compile(r"^##\s+(.+?)\s*$")


@dataclass
class SpecSection:
    """单个 ## 级章节：节名 + 正文（不含标题行）。"""

    name: str
    body: str


@dataclass
class SpecDocument:
    """单个规范文档：文档名、「文档名 § 节名」引用与同名覆盖判定的键、解析产物。

    ``name`` = 相对发现根的 posix 路径（目录扫描）或文件名（显式单文件）；
    ``path`` = 规范化绝对路径（posix），作 hash 排序键。
    """

    name: str
    path: str
    content: str
    tldr: str = ""
    sections: list[SpecSection] = field(default_factory=list)

    @property
    def line_count(self) -> int:
        return len(self.content.splitlines())


@dataclass
class SpecSources:
    """三层来源的 CLI 侧打包（约定目录由 repo_root 推导，不经此处）。

    ``cli`` = --spec 展开后的条目（最高优先层）；``config`` = spec_kb.paths；
    ``base_dir`` = cli/config 相对路径的解析基准（回放 = 快照仓库，git = cwd）。
    """

    cli: list[str] = field(default_factory=list)
    config: list[str] = field(default_factory=list)
    base_dir: str | None = None


@dataclass
class SpecKB:
    """一次加载的完整产物；``as_state()`` 即图 state 的 spec_kb 键形态。"""

    documents: list[SpecDocument]
    rendered_text: str
    stats: dict[str, Any]
    hash: str | None

    def as_state(self) -> dict[str, Any]:
        """进 state 的 JSON 兼容形态：router 只读 documents，spec 专家只读 rendered_text。"""
        return {
            "documents": [d.name for d in self.documents],
            "rendered_text": self.rendered_text,
            "stats": self.stats,
            "hash": self.hash,
        }


def _extract_tldr(content: str) -> str:
    """提取顶部 TL;DR 块（启发式见模块 docstring）：首个命中行起的连续引用块。"""
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if not _TLDR_FIRST_LINE.match(line):
            continue
        block = [line]
        for follow in lines[i + 1 :]:
            if follow.lstrip().startswith(">"):
                block.append(follow)
            else:
                break  # 连续引用块结束（空行/正文行均终止）
        return "\n".join(block).rstrip()
    return ""  # 无 TL;DR：降级时该文档只贡献命中的章节


def _parse_sections(content: str) -> list[SpecSection]:
    """按 ## 级标题切章节（# / ### 不切）；首个 ## 之前的序言不作为章节。"""
    sections: list[SpecSection] = []
    current: SpecSection | None = None
    body: list[str] = []
    for line in content.splitlines():
        match = _SECTION_HEADING.match(line)
        if match:
            if current is not None:
                current.body = "\n".join(body).strip()
                sections.append(current)
            current = SpecSection(name=match.group(1).strip(), body="")
            body = []
        elif current is not None:
            body.append(line)
    if current is not None:
        current.body = "\n".join(body).strip()
        sections.append(current)
    return sections


def _make_doc(name: str, file: Path, content: str) -> SpecDocument:
    return SpecDocument(
        name=name,
        path=file.as_posix(),
        content=content,
        tldr=_extract_tldr(content),
        sections=_parse_sections(content),
    )


def _doc_name(file: Path, source_root: Path | None) -> str:
    """文档名：目录扫描 → 相对发现根的 posix 路径；显式单文件 → 文件名。"""
    if source_root is not None:
        try:
            return file.relative_to(source_root).as_posix()
        except ValueError:  # 理论不可达（扫描产物必在根下）；兜底用文件名
            pass
    return file.name


def _collect_entry(
    entry: str, base_dir: Path | None
) -> tuple[list[tuple[Path, Path | None]], bool]:
    """单个来源条目 → ([(文件, 发现根)], 条目是否存在)。

    目录 → 递归收 *.md（排序保证确定性），发现根 = 该目录；文件 → 原样收
    （显式给出时不限扩展名），发现根 = None（文档名取文件名）。
    """
    path = Path(entry)
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    if path.is_dir():
        root = path.resolve()
        try:
            found = [f.resolve() for f in sorted(root.rglob("*.md")) if f.is_file()]
        except OSError as exc:  # 目录不可读：告警后按缺失降级，不致命
            print(f"reviewer: warning: 规范目录无法扫描 {root}：{exc}", file=sys.stderr)
            return [], True
        return [(f, root) for f in found], True
    if path.is_file():
        return [(path.resolve(), None)], True
    return [], False


def _merge_docs(layers: list[list[tuple[Path, Path | None]]]) -> list[SpecDocument]:
    """逐层合并（层序 = 优先级升序，后到者覆盖同名 → CLI 层最高优先）。

    - 文档名已存在 → 覆盖（同内容重复引用为 no-op，不同内容即高优先层胜出）；
    - 内容 sha256 已见 → 去重跳过（同内容两路径只计一次，先到者保留名字）；
    - 其余平级共存（不同名不同内容互不覆盖）。
    """
    by_name: dict[str, SpecDocument] = {}
    seen_hashes: set[str] = set()
    for layer in layers:
        for file, source_root in layer:
            try:
                content = file.read_text(encoding="utf-8")
            except OSError as exc:
                print(
                    f"reviewer: warning: 无法读取规范文档 {file}：{exc}", file=sys.stderr
                )
                continue
            name = _doc_name(file, source_root)
            if name in by_name:
                by_name[name] = _make_doc(name, file, content)  # 同名：覆盖（含 no-op）
                continue
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if digest in seen_hashes:
                continue  # 内容去重
            by_name[name] = _make_doc(name, file, content)
            seen_hashes.add(digest)
    return list(by_name.values())


def _changed_paths(diff: str) -> list[str]:
    """diff 新侧路径（``+++ b/`` 行；与 context._changed_py_files 同构但不限 .py）。"""
    paths: list[str] = []
    for line in diff.splitlines():
        if not line.startswith("+++ b/"):
            continue
        path = line[len("+++ b/") :].split("\t")[0].strip()
        if path and path != "/dev/null" and path not in paths:
            paths.append(path)
    return paths


def _doc_matches_changes(name: str, changed: list[str]) -> bool:
    """启发式：文档名与任一改动路径的「路径段/词干」相交即整文档收录（降级用）。

    specs/orders.md → 候选段 {specs, orders}；改动 src/orders/create.py 的段
    {src, orders, create.py} ∪ {create}（文件名去扩展名也参与）→ "orders" 命中。
    宽松匹配（宁多勿漏）的理由见模块 docstring。
    """
    doc_segs = {
        seg.rsplit(".", 1)[0] if "." in seg else seg
        for seg in name.replace("\\", "/").split("/")
        if seg
    }
    for path in changed:
        segs = path.replace("\\", "/").split("/")
        path_segs = set(segs)
        last = segs[-1]
        if "." in last:
            path_segs.add(last.rsplit(".", 1)[0])
        if doc_segs & path_segs:
            return True
    return False


def _render(
    documents: list[SpecDocument], changed: list[str], sections_total: int
) -> tuple[str, dict[str, Any]]:
    """渲染 spec 专家上下文：≤500 行全量；>500 行 TL;DR 全量 + 命中章节。"""
    total_lines = sum(d.line_count for d in documents)
    if total_lines <= KB_LINE_LIMIT:
        parts = [f"### 文档：{d.name}\n{d.content.strip()}" for d in documents]
        return "\n\n".join(parts), {
            "total_lines": total_lines,
            "truncated": False,
            "sections_total": sections_total,
            "sections_matched": sections_total,
        }

    matched = 0
    parts: list[str] = []
    for doc in documents:
        blocks: list[str] = [f"### 文档：{doc.name}（降级：仅 TL;DR 与改动相关章节）"]
        if doc.tldr:
            blocks.append(doc.tldr)  # TL;DR 恒全量注入（专家提示词最有效的规范形态）
        if _doc_matches_changes(doc.name, changed):
            for section in doc.sections:
                matched += 1
                blocks.append(f"#### {doc.name} § {section.name}\n{section.body}".rstrip())
        if len(blocks) > 1:  # 无 TL;DR 且未命中 → 该文档降级后无内容可注，整体省略
            parts.append("\n\n".join(blocks))
    text = "\n\n".join(parts) + (
        f"\n\n[spec kb truncated: {total_lines} lines > {KB_LINE_LIMIT}, "
        f"TL;DR kept + {matched}/{sections_total} sections matched]"
    )
    return text, {
        "total_lines": total_lines,
        "truncated": True,
        "sections_total": sections_total,
        "sections_matched": matched,
    }


def load_spec_kb(*, sources: SpecSources, repo_root: str | None, diff: str) -> SpecKB:
    """加载入口：三层来源 → 合并去重 → 解析 → 渲染（>500 行降级）。纯本地零模型。

    ``repo_root`` 为空（纯回放无快照）时约定目录不生效，仅 CLI/config 来源按
    ``sources.base_dir`` 解析——与 context.py 的「缺快照降级不致命」同型。
    """
    base_dir = Path(sources.base_dir) if sources.base_dir else None

    convention: list[tuple[Path, Path | None]] = []
    if repo_root:
        root = Path(repo_root)
        for rel in CONVENTION_DIRS:
            found, _ = _collect_entry(rel, root)  # 约定目录缺失 = 正常态，不告警
            convention.extend(found)
    config_layer: list[tuple[Path, Path | None]] = []
    for entry in sources.config:
        found, _ = _collect_entry(entry, base_dir)  # 配置缺失/为空静默跳过（缺省 paths 恒在）
        config_layer.extend(found)
    cli_layer: list[tuple[Path, Path | None]] = []
    for entry in sources.cli:
        found, exists = _collect_entry(entry, base_dir)
        if not exists:  # 显式 --spec 指向不存在路径：typo 代价高，stderr 告警
            print(f"reviewer: warning: --spec 来源不存在，已跳过：{entry}", file=sys.stderr)
        cli_layer.extend(found)

    documents = _merge_docs([convention, config_layer, cli_layer])
    sections_total = sum(len(d.sections) for d in documents)
    rendered, stats = _render(documents, _changed_paths(diff), sections_total)
    stats = {"documents": len(documents), **stats}

    kb_hash: str | None = None
    if documents:
        joined = "".join(d.content for d in sorted(documents, key=lambda d: d.path))
        kb_hash = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]
    return SpecKB(
        documents=documents, rendered_text=rendered, stats=stats, hash=kb_hash
    )
