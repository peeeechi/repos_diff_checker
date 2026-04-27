#!/usr/bin/env python3
"""
Compare *.repos between two git refs in the parent repository, then list commits
for each package whose definition changed.

Git entries are matched by **remote URL** (not YAML key): if the path/key changes
between refs but the URL is the same, the tool treats it as one package.

A URL pair is reported when **type** or any pin field (**version**, **branch**,
**tag**) differs between refs (field-wise equality, not only the resolved
``effective_ref``). Commit logs still use ``effective_ref`` on each side.

Discovery: walks ``--search-root`` (default: cwd) for ``*.repos``. Each path is
read with ``git show <ref>:<path>``; files not in the git object database at a
given ref are skipped (typically only git-tracked .repos are compared).

Branch names that exist only as ``origin/<branch>`` (no local branch) are
resolved automatically for reading ``.repos`` from the parent repo.

Requires: PyYAML (``pip install pyyaml``), ``git`` on PATH, and network access
when cloning remotes (unless ``--local-only``).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import yaml
except ImportError as e:
    print("This script requires PyYAML: pip install pyyaml", file=sys.stderr)
    raise SystemExit(1) from e


def _run_git(
    args: List[str],
    cwd: Optional[Path] = None,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        check=check,
        text=True,
        capture_output=capture,
    )


def git_repo_root(start: Path) -> Path:
    p = _run_git(["rev-parse", "--show-toplevel"], cwd=start)
    return Path(p.stdout.strip())


_HEX_REF_RE = re.compile(r"^[0-9a-f]{7,40}$", re.I)


def resolve_ref_for_read(repo_root: Path, user_ref: str) -> str:
    """
    Return a ref string that ``git show <ref>:path`` can use.

    If ``user_ref`` is not found locally (e.g. branch exists only as
    ``origin/foo``), tries ``origin/<user_ref>``.
    """
    r = _run_git(["rev-parse", "--verify", user_ref], cwd=repo_root, check=False)
    if r.returncode == 0:
        return user_ref
    if _HEX_REF_RE.match(user_ref.strip()):
        raise RuntimeError(
            f"Cannot resolve ref {user_ref!r} (not an object in this repository)."
        )
    u = user_ref.strip()
    if u.startswith(("refs/", "origin/")) or u in ("HEAD", "FETCH_HEAD", "MERGE_HEAD"):
        raise RuntimeError(
            f"Cannot resolve ref {user_ref!r}. "
            "Check the name or run: git fetch origin"
        )
    alt = f"origin/{u}"
    r2 = _run_git(["rev-parse", "--verify", alt], cwd=repo_root, check=False)
    if r2.returncode == 0:
        return alt
    raise RuntimeError(
        f"Cannot resolve ref {user_ref!r} (also tried {alt!r}). "
        "Fetch the branch (e.g. git fetch origin) or pass origin/<branch> explicitly."
    )


def git_show_file(repo_root: Path, ref: str, relpath: str) -> Optional[str]:
    """Return file contents at ref, or None if missing."""
    r = _run_git(["show", f"{ref}:{relpath}"], cwd=repo_root, check=False)
    if r.returncode != 0:
        return None
    return r.stdout


def iter_repos_under(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fn in filenames:
            if fn.endswith(".repos"):
                yield Path(dirpath) / fn


def parse_repos_yaml(text: str) -> Dict[str, Any]:
    data = yaml.safe_load(text)
    if not data or "repositories" not in data:
        return {}
    repos = data["repositories"]
    if not isinstance(repos, dict):
        return {}
    return repos


def effective_ref(entry: Dict[str, Any]) -> str:
    v = entry.get("version")
    if v is not None and str(v).strip():
        return str(v).strip()
    b = entry.get("branch")
    if b is not None and str(b).strip():
        return str(b).strip()
    t = entry.get("tag")
    if t is not None and str(t).strip():
        return str(t).strip()
    return ""


def _yaml_str_field(entry: Dict[str, Any], key: str) -> str:
    if not isinstance(entry, dict):
        return ""
    v = entry.get(key)
    if v is None:
        return ""
    return str(v).strip()


def normalized_entry_type(entry: Dict[str, Any]) -> str:
    """Lowercased type string; default git when missing or blank."""
    if not isinstance(entry, dict):
        return "git"
    t = entry.get("type", "git")
    s = str(t).strip().lower() if t is not None else ""
    return s if s else "git"


def git_entry_pin_signature(entry: Dict[str, Any]) -> Tuple[str, str, str, str]:
    """
    (type, version, branch, tag) for diff detection.

    Compared field-wise so a change between branch-only vs version-only
    pinning is not collapsed incorrectly.
    """
    return (
        normalized_entry_type(entry),
        _yaml_str_field(entry, "version"),
        _yaml_str_field(entry, "branch"),
        _yaml_str_field(entry, "tag"),
    )


def git_entry_pin_changed(old_e: Dict[str, Any], new_e: Dict[str, Any]) -> bool:
    """True if type or any of version/branch/tag fields differ."""
    return git_entry_pin_signature(old_e) != git_entry_pin_signature(new_e)


def normalize_git_entry(name: str, entry: Any) -> Optional[Tuple[str, str, str, str]]:
    """Returns (name, type, url, effective_ref) or None if skipped."""
    if not isinstance(entry, dict):
        return None
    t = str(entry.get("type", "git")).strip().lower()
    if t != "git":
        return None
    url = entry.get("url")
    if not url or not str(url).strip():
        return None
    ref = effective_ref(entry)
    return (name, t, str(url).strip(), ref)


@dataclass(frozen=True)
class GitEntryInfo:
    """One git repository entry from a .repos file."""

    yaml_key: str
    entry: Dict[str, Any]
    type: str
    url: str
    ref: str


def iter_git_entry_infos(repos: Dict[str, Any]) -> List[GitEntryInfo]:
    out: List[GitEntryInfo] = []
    for name, entry in repos.items():
        if not isinstance(entry, dict):
            continue
        n = normalize_git_entry(str(name), entry)
        if not n:
            continue
        kn, tp, url, ref = n
        out.append(GitEntryInfo(yaml_key=kn, entry=entry, type=tp, url=url, ref=ref))
    return out


def index_git_entries_by_url(entries: Iterable[GitEntryInfo]) -> Dict[str, GitEntryInfo]:
    """
    Map URL -> one entry. If the same URL appears under multiple YAML keys,
    the lexicographically smallest key wins (deterministic).
    """
    out: Dict[str, GitEntryInfo] = {}
    for e in sorted(entries, key=lambda x: x.yaml_key):
        if e.url not in out:
            out[e.url] = e
    return out


@dataclass
class CommitLine:
    hash: str
    subject: str


def _local_repo_candidates(repo_root: Path, package_key: str) -> List[Path]:
    parts = Path(package_key)
    return [
        repo_root / package_key,
        repo_root / "src" / package_key,
        repo_root / "src" / parts.name,
    ]


def find_local_git_repo(repo_root: Path, package_key: str) -> Optional[Path]:
    for c in _local_repo_candidates(repo_root, package_key):
        if (c / ".git").exists() and c.is_dir():
            return c
    return None


def find_local_git_repo_for_keys(
    repo_root: Path, *yaml_keys: Optional[str]
) -> Optional[Path]:
    for k in yaml_keys:
        if not k:
            continue
        p = find_local_git_repo(repo_root, k)
        if p is not None:
            return p
    return None


_mirror_cache: Dict[str, Path] = {}


def _mirror_path_for_url(url: str) -> Path:
    if url not in _mirror_cache:
        d = Path(tempfile.mkdtemp(prefix="repos-diff-mirror-"))
        r = subprocess.run(
            ["git", "clone", "--mirror", url, str(d)],
            text=True,
            capture_output=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"git clone --mirror failed for {url}:\n{r.stderr}")
        _mirror_cache[url] = d
    return _mirror_cache[url]


def _fetch_ref_in_mirror(mirror: Path, ref: str) -> None:
    # Fetch objects needed to resolve ref (commit SHA, branch, tag).
    r = subprocess.run(
        ["git", "-C", str(mirror), "fetch", "origin", ref],
        text=True,
        capture_output=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"git fetch failed in mirror {mirror} for ref {ref!r}:\n{r.stderr}")


def _rev_parse_commit(git_dir: Path, ref: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(git_dir), "rev-parse", "--verify", f"{ref}^{{commit}}"],
        text=True,
        capture_output=True,
    )
    if r.returncode != 0:
        r2 = subprocess.run(
            ["git", "-C", str(git_dir), "rev-parse", "--verify", ref],
            text=True,
            capture_output=True,
        )
        if r2.returncode != 0:
            raise RuntimeError(f"rev-parse failed for {ref!r}: {r.stderr or r2.stderr}")
        return r2.stdout.strip()
    return r.stdout.strip()


def commits_between(
    url: str,
    old_ref: str,
    new_ref: str,
    *,
    local_git: Optional[Path] = None,
    local_only: bool = False,
) -> Tuple[List[CommitLine], Optional[str]]:
    """
    Returns (commits, error_message). Commits are oldest-first (git log order).
    """
    if not old_ref or not new_ref:
        return [], "missing version/branch ref in .repos entry"

    git_dir: Optional[Path] = local_git
    if git_dir is not None:
        try:
            o = _rev_parse_commit(git_dir, old_ref)
            n = _rev_parse_commit(git_dir, new_ref)
        except RuntimeError:
            git_dir = None

    if git_dir is None and not local_only:
        try:
            mirror = _mirror_path_for_url(url)
            _fetch_ref_in_mirror(mirror, old_ref)
            _fetch_ref_in_mirror(mirror, new_ref)
            o = _rev_parse_commit(mirror, old_ref)
            n = _rev_parse_commit(mirror, new_ref)
            git_dir = mirror
        except RuntimeError as e:
            return [], str(e)

    if git_dir is None:
        return [], "no usable local clone and --local-only set, or fetch failed"

    if o == n:
        return [], None

    r = subprocess.run(
        [
            "git",
            "-C",
            str(git_dir),
            "log",
            "--reverse",
            "--pretty=format:%H%x09%s",
            f"{o}..{n}",
        ],
        text=True,
        capture_output=True,
    )
    if r.returncode != 0:
        return [], f"git log failed: {r.stderr.strip()}"

    commits: List[CommitLine] = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        h, sep, subj = line.partition("\t")
        if sep:
            commits.append(CommitLine(hash=h, subject=subj))

    if not commits and o != n:
        # Different SHAs but no reachable path (e.g. unrelated histories): still report.
        r2 = subprocess.run(
            ["git", "-C", str(git_dir), "merge-base", o, n],
            text=True,
            capture_output=True,
        )
        if r2.returncode != 0:
            return (
                [],
                f"refs {o[:7]}..{n[:7]} are not on a linear ancestry; "
                "try comparing in a full clone or check fork/URL changes",
            )

    return commits, None


def render_markdown(
    repo_root: Path,
    ref_old: str,
    ref_new: str,
    sections: List[
        Tuple[
            str,
            Optional[str],
            List[Tuple[str, Dict[str, Any], Dict[str, Any], List[CommitLine], Optional[str]]],
        ]
    ],
    *,
    git_ref_old: Optional[str] = None,
    git_ref_new: Optional[str] = None,
) -> str:
    lines: List[str] = []
    lines.append("# .repos diff report")
    lines.append("")
    lines.append(f"- **Repository**: `{repo_root}`")
    lines.append(f"- **Old ref**: `{ref_old}`")
    if git_ref_old and git_ref_old != ref_old:
        lines.append(f"  - *Git resolves this as*: `{git_ref_old}`")
    lines.append(f"- **New ref**: `{ref_new}`")
    if git_ref_new and git_ref_new != ref_new:
        lines.append(f"  - *Git resolves this as*: `{git_ref_new}`")
    lines.append("")
    if not sections:
        lines.append("No `.repos` files found at both refs under the search path, or no changes.")
        lines.append("")
        return "\n".join(lines)

    for rel_file, file_note, blocks in sections:
        if not blocks:
            lines.append(f"## `{rel_file}`")
            lines.append("")
            if file_note:
                lines.append(f"> {file_note}")
                lines.append("")
            else:
                lines.append("*No package entry changes (git) in this file.*")
                lines.append("")
            continue

        lines.append(f"## `{rel_file}`")
        lines.append("")
        if file_note:
            lines.append(f"> {file_note}")
            lines.append("")

        for block_url, old_e, new_e, commits, err in blocks:
            lines.append(f"### `{block_url}`")
            lines.append("")
            lines.append("| | Old | New |")
            lines.append("|---|-----|-----|")
            ou = old_e.get("url", "") if isinstance(old_e, dict) else ""
            nu = new_e.get("url", "") if isinstance(new_e, dict) else ""
            ot = normalized_entry_type(old_e) if isinstance(old_e, dict) else ""
            nt = normalized_entry_type(new_e) if isinstance(new_e, dict) else ""
            o_ver = _yaml_str_field(old_e, "version") if isinstance(old_e, dict) else ""
            n_ver = _yaml_str_field(new_e, "version") if isinstance(new_e, dict) else ""
            o_br = _yaml_str_field(old_e, "branch") if isinstance(old_e, dict) else ""
            n_br = _yaml_str_field(new_e, "branch") if isinstance(new_e, dict) else ""
            o_tg = _yaml_str_field(old_e, "tag") if isinstance(old_e, dict) else ""
            n_tg = _yaml_str_field(new_e, "tag") if isinstance(new_e, dict) else ""
            ov = effective_ref(old_e) if isinstance(old_e, dict) else ""
            nv = effective_ref(new_e) if isinstance(new_e, dict) else ""
            lines.append(f"| **type** | `{ot}` | `{nt}` |")
            lines.append(f"| **version** | `{o_ver}` | `{n_ver}` |")
            lines.append(f"| **branch** | `{o_br}` | `{n_br}` |")
            lines.append(f"| **tag** | `{o_tg}` | `{n_tg}` |")
            lines.append(f"| **url** | `{ou}` | `{nu}` |")
            lines.append(
                f"| **effective ref** (used for commit range) | `{ov}` | `{nv}` |"
            )
            lines.append("")
            if commits:
                lines.append("Commits (oldest first):")
                lines.append("")
                for c in commits:
                    lines.append(f"- `{c.hash}` — {c.subject}")
                lines.append("")
            elif not err:
                lines.append("*No commits in range (same resolved commit, or empty range).*")
                lines.append("")
            if err:
                lines.append(f"**Note**: {err}")
                lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Diff .repos packages between two git refs and list intermediate commits.",
    )
    ap.add_argument("ref_old", help="Older git ref (branch, tag, or commit)")
    ap.add_argument("ref_new", help="Newer git ref (branch, tag, or commit)")
    ap.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Git repository root (default: inferred from cwd)",
    )
    ap.add_argument(
        "--search-root",
        type=Path,
        default=None,
        help="Directory under which to discover *.repos (default: cwd)",
    )
    ap.add_argument(
        "--local-only",
        action="store_true",
        help="Do not clone remotes; only use existing repos under the workspace",
    )
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Write markdown to this file (default: stdout)",
    )
    args = ap.parse_args()

    cwd = Path.cwd()
    repo_root = args.repo_root.resolve() if args.repo_root else git_repo_root(cwd)
    search_root = (args.search_root or cwd).resolve()
    try:
        search_root.relative_to(repo_root)
    except ValueError:
        print("--search-root must be inside the git repository", file=sys.stderr)
        raise SystemExit(2)

    try:
        ref_old_git = resolve_ref_for_read(repo_root, args.ref_old)
        ref_new_git = resolve_ref_for_read(repo_root, args.ref_new)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(2)

    all_sections: List[
        Tuple[str, Optional[str], List[Tuple[str, Dict[str, Any], Dict[str, Any], List[CommitLine], Optional[str]]]]
    ] = []

    for path in sorted(iter_repos_under(search_root)):
        try:
            rel = str(path.resolve().relative_to(repo_root.resolve()))
        except ValueError:
            continue
        old_txt = git_show_file(repo_root, ref_old_git, rel)
        new_txt = git_show_file(repo_root, ref_new_git, rel)
        if old_txt is None and new_txt is None:
            continue
        if old_txt is None and new_txt is not None:
            all_sections.append(
                (
                    rel,
                    f"This file exists only at new ref `{args.ref_new}` (not at `{args.ref_old}`).",
                    [],
                )
            )
            continue
        if old_txt is not None and new_txt is None:
            all_sections.append(
                (
                    rel,
                    f"This file exists only at old ref `{args.ref_old}` (not at `{args.ref_new}`).",
                    [],
                )
            )
            continue

        old_repos = parse_repos_yaml(old_txt)
        new_repos = parse_repos_yaml(new_txt)
        old_by_url = index_git_entries_by_url(iter_git_entry_infos(old_repos))
        new_by_url = index_git_entries_by_url(iter_git_entry_infos(new_repos))
        blocks: List[
            Tuple[str, Dict[str, Any], Dict[str, Any], List[CommitLine], Optional[str]]
        ] = []
        for url in sorted(set(old_by_url) | set(new_by_url)):
            oi = old_by_url.get(url)
            ni = new_by_url.get(url)

            if oi and ni:
                od, nd = oi.entry, ni.entry
                if not git_entry_pin_changed(od, nd):
                    continue
                o_norm = normalize_git_entry(oi.yaml_key, od)
                n_norm = normalize_git_entry(ni.yaml_key, nd)
                if not o_norm or not n_norm:
                    continue
                _, _, url_o, ref_o = o_norm
                _, _, url_n, ref_n = n_norm
                clone_url = url_n or url_o
                old_ref = ref_o
                new_ref = ref_n
                local = find_local_git_repo_for_keys(
                    repo_root, oi.yaml_key, ni.yaml_key
                )
                commits, err = commits_between(
                    clone_url,
                    old_ref,
                    new_ref,
                    local_git=local,
                    local_only=args.local_only,
                )
                blocks.append((url, od, nd, commits, err))
                continue

            if not oi and ni:
                nd = ni.entry
                blocks.append(
                    (
                        url,
                        {},
                        nd,
                        [],
                        "No git entry at old ref (newly added URL).",
                    )
                )
                continue

            if oi and not ni:
                od = oi.entry
                blocks.append(
                    (
                        url,
                        od,
                        {},
                        [],
                        "No git entry at new ref (removed URL).",
                    )
                )
                continue

        all_sections.append((rel, None, blocks))

    md = render_markdown(
        repo_root,
        args.ref_old,
        args.ref_new,
        all_sections,
        git_ref_old=ref_old_git,
        git_ref_new=ref_new_git,
    )
    if args.output:
        args.output.write_text(md, encoding="utf-8")
    else:
        sys.stdout.write(md)


if __name__ == "__main__":
    main()
