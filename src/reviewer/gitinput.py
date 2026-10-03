"""真实 git 输入（实现 4/11 #16）：subprocess 封装 git，产出 diff 与提交信息上下文。

纯函数风格：输入 = 仓库路径（+ 引用），输出 = :class:`GitInput`；无全局状态、无副作用。

失败语义（CLI 层统一映射退出码 64，属输入/用法侧问题）：

- 非 git 目录 → :class:`NotAGitRepoError`（git 输出 "not a git repository" 类信号）；
- 其余 git 失败（引用不存在、git 不可用等）→ :class:`GitInputError`。

采集约定：

- 所有 git 调用统一走 subprocess：显式 ``encoding="utf-8"``（Windows 默认 locale
  编码会把 UTF-8 提交信息读花）+ ``errors="replace"``（容错异体编码）+ 超时兜底；
- precheck = ``git diff HEAD``：staged + 未提交改动一起进 diff（未跟踪文件
  git diff 天然不含，超出本票范围）；全新仓库（HEAD 不存在）退化为
  ``git diff --cached``（staged vs 空树），让「刚 init 就 precheck」也能工作；
- check [ref]：merge-base = ``git merge-base HEAD <ref>``（分叉历史取共同祖先
  而非 ref 尖端，只审本分支真正引入的改动），diff = ``git diff <merge-base>..HEAD``；
- 提交信息上下文 = ``git log <merge-base>..HEAD --format=%s%n%b``，供描述自动拼接
  （best-effort，超长截断）；
- precheck 的 ``commits_text`` 恒为空：工作区改动尚未形成提交，无
  「merge-base..HEAD」区间可言，HEAD 既有历史与本次改动无关的概率高，拼入
  反而是意图噪声（logic 专家拿它对照会误判）——best-effort 决策，宁缺勿滥。
"""

from __future__ import annotations

import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

#: 单条 git 命令超时（秒），防大仓库 / 存储异常拖垮 CLI
GIT_TIMEOUT_S = 30.0

#: 自动拼接提交信息的长度上限（超出截断，防超大历史撑爆 prompt）
MAX_COMMITS_TEXT_CHARS = 20_000


class GitInputError(Exception):
    """git 输入采集失败（引用不存在 / git 不可用等）；CLI 层映射退出码 64。"""


class NotAGitRepoError(GitInputError):
    """目标目录不是 git 仓库（git 报 "not a git repository" 类信号）；→ 64。"""


@dataclass
class GitInput:
    """一次 git 采集的产出：diff 文本 + 基线元数据 + 提交信息上下文。

    ``base_ref`` 是 diff 的实际基线（check = merge-base 完整 SHA，可复现；
    precheck = "HEAD"，改动在工作区）。``commits_text`` 供描述自动拼接，
    可能为空串（无提交区间 / precheck 决策为空，见模块 docstring）。
    """

    diff: str
    base_ref: str
    head_ref: str
    commits_text: str
    source_commit: str | None = None


def _run_git(repo: Path, *args: str) -> str:
    """跑一条 ``git -C <repo> ...``，按失败信号归类抛 :class:`GitInputError` 家族。

    ``core.quotepath=false``：非 ASCII 路径不转义成八进制，diff 更可读。
    """
    cmd = ["git", "-c", "core.quotepath=false", "-C", str(repo), *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_S,
        )
    except FileNotFoundError as exc:
        raise GitInputError("git 命令不可用：请确认已安装 git 并加入 PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitInputError(f"git {' '.join(args)} 超时（>{GIT_TIMEOUT_S:.0f}s）") from exc
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if "not a git repository" in stderr.lower():
            raise NotAGitRepoError(f"{repo} 不是 git 仓库（或不在任何 git 仓库内）")
        raise GitInputError(f"git {' '.join(args)} 失败：{stderr or '（无错误输出）'}")
    return proc.stdout or ""


def _has_head(repo: Path) -> bool:
    """HEAD 是否指向已存在的提交（全新仓库 ``rev-parse HEAD`` 会失败）。

    非 git 目录的信号必须继续向上抛，不能误判成「无 HEAD」；其余失败
    （unknown revision 等）按 best-effort 视为无提交。
    """
    try:
        return bool(_run_git(repo, "rev-parse", "--verify", "--quiet", "HEAD").strip())
    except NotAGitRepoError:
        raise
    except GitInputError:
        return False


def collect_precheck_diff(repo: Path) -> GitInput:
    """precheck：``git diff HEAD``——staged + 未提交改动一起进 diff。

    无提交的全新仓库退化为 ``git diff --cached``（staged vs 空树；此时
    未暂存改动不含在内——罕见场景，best-effort）。``commits_text`` 恒空，
    决策理由见模块 docstring。
    """
    if _has_head(repo):
        diff = _run_git(repo, "diff", "HEAD")
    else:
        # 全新仓库：HEAD 不存在，diff 退化为 staged vs 空树
        diff = _run_git(repo, "diff", "--cached")
    return GitInput(diff=diff, base_ref="HEAD", head_ref="HEAD", commits_text="")


def collect_check_diff(repo: Path, ref: str) -> GitInput:
    """check [ref]：以 merge-base 为基线，审 HEAD 相对 ``ref`` 的改动。

    merge-base 而非 ref 尖端：分支分叉后基线分支又前进时，只审本分支
    真正引入的改动（Spec：基线 = ``git merge-base HEAD <默认分支>``）。
    提交信息上下文 = ``git log <merge-base>..HEAD``（subject + body），
    供描述自动拼接，超长截断。
    """
    head = _run_git(repo, "rev-parse", "--verify", "HEAD^{commit}").strip()
    merge_base = _run_git(repo, "merge-base", head, ref).strip()
    if not merge_base:
        raise GitInputError(f"git merge-base HEAD {ref} 无输出：两者可能无共同祖先")
    diff = _run_git(repo, "diff", f"{merge_base}..{head}")
    commits_text = _run_git(repo, "log", f"{merge_base}..{head}", "--format=%s%n%b").strip()
    if len(commits_text) > MAX_COMMITS_TEXT_CHARS:
        commits_text = (
            commits_text[:MAX_COMMITS_TEXT_CHARS].rstrip() + "\n…（提交信息超长，已截断）"
        )
    return GitInput(diff=diff, base_ref=merge_base, head_ref="HEAD", commits_text=commits_text,
                    source_commit=head)


@contextmanager
def committed_snapshot(repo: Path, commit: str):
    """Materialize regular Git blobs only, without checkout or archive attributes.

    Symlinks and submodules cannot lead reads outside the isolated source view.
    All object ids come from the pinned tree, including when HEAD moves later.
    """
    listing = _run_git(repo, "ls-tree", "-rz", "--full-tree", commit)
    entries: list[tuple[str, str]] = []
    for record in listing.split("\0"):
        if not record:
            continue
        details, name = record.split("\t", 1)
        mode, kind, oid = details.split()
        if mode in ("100644", "100755") and kind == "blob":
            entries.append((name, oid))
    with tempfile.TemporaryDirectory(prefix="reviewer-source-") as directory:
        root = Path(directory).resolve()
        # Batch the immutable blob reads; binary lengths prevent newline corruption.
        try:
            proc = subprocess.run(
                ["git", "-C", str(repo), "cat-file", "--batch"],
                input="".join(oid + "\n" for _, oid in entries).encode("ascii"),
                capture_output=True, timeout=GIT_TIMEOUT_S,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitInputError(f"无法读取被审提交源码：{exc}") from exc
        if proc.returncode:
            raise GitInputError("无法读取被审提交的 Git 对象")
        offset = 0
        for name, oid in entries:
            end = proc.stdout.find(b"\n", offset)
            header = proc.stdout[offset:end].decode("ascii").split()
            if len(header) != 3 or header[:2] != [oid, "blob"]:
                raise GitInputError("被审提交的 Git 对象响应无效")
            size = int(header[2])
            offset = end + 1
            content = proc.stdout[offset:offset + size]
            offset += size + 1
            target = (root / name).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise GitInputError("被审提交包含越界源码路径") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        yield root
