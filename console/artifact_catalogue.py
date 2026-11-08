"""Read-only inspection of method, baseline and dataset source artifacts.

All roots/limits are explicit; inspecting source never imports adapters, executes
a method, computes a measurement identity or creates a root.
"""
from __future__ import annotations
import ast
import hashlib
import json
import pathlib
import re
from dataclasses import dataclass
if __package__:
    from .record_reader import read_json
    from .path_policy import within
    from .serialization import human_bytes
else:
    from record_reader import read_json
    from path_policy import within
    from serialization import human_bytes

@dataclass(frozen=True)
class ArtifactRoots:
    platform: pathlib.Path
    base: pathlib.Path
    bases: pathlib.Path
    datasets: pathlib.Path

@dataclass(frozen=True)
class TextLimits:
    method_bytes: int
    method_lines: int
    panel_bytes: int
    panel_lines: int

MANIFEST_KEYS = ("name", "version", "entrypoint", "backend", "install")

_KIND_RE = re.compile(r"^KIND\s*=\s*[\"']([A-Za-z_]+)[\"']\s*$", re.M)

_SPLIT_RE = re.compile(r"^SPLIT\s*=", re.M)

def _deviation_items(spec: dict) -> list[dict] | None:
    """`method.json.deviations` -> 稳定的条目列表；没有 method.json 时是 null，不是 []。

    两种写法都见过：dict 条目（what/why/direction/id）和纯字符串。缺字段的条目
    不编造，保留已有键；`what` 是界面上“可以借什么”的正文。
    """
    if not spec:
        return None
    raw = spec.get("deviations")
    if raw is None:
        return []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if isinstance(item, dict):
            row = {}
            for key in ("id", "what", "why", "direction"):
                value = item.get(key)
                if value is None or isinstance(value, (str, int, float, bool)):
                    row[key] = value
                else:
                    row[key] = json.dumps(value, ensure_ascii=False)[:2000]
            if not any(row.get(k) for k in ("id", "what", "why")):
                row["what"] = json.dumps(item, ensure_ascii=False)[:2000]
            out.append(row)
        elif isinstance(item, str):
            out.append({"what": item})
        else:
            out.append({"what": str(item)})
    return out


def _size_of(path: pathlib.Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


class ArtifactCatalogue:
    def __init__(self, roots, limits, *, skip_dirs):
        self.roots, self.limits, self.skip_dirs = roots, limits, skip_dirs

    def _method_dir(self, name: str) -> pathlib.Path | None:
        """`methods/<name>/`，名字只允许安全字符，且解析后必须仍在 methods/ 下面。"""
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name or "") or name in (".", ".."):
            return None
        root = self.roots.platform / "methods"
        d = root / name
        try:
            if not d.is_dir() or not within(root, d):
                return None
        except OSError:
            return None
        return d


    def _method_files(self, d: pathlib.Path) -> list[dict]:
        """方法目录里可读的文件。run.py / method.json 先列，其余按路径排。"""
        out: list[dict] = []
        run_path = d / "run.py"
        spec_path = d / "method.json"
        if run_path.is_file():
            out.append({"path": "run.py", "size": _size_of(run_path), "role": "runner"})
        if spec_path.is_file():
            out.append({"path": "method.json", "size": _size_of(spec_path), "role": "spec"})
        try:
            entries = sorted(d.rglob("*"))
        except OSError:
            return out
        for f in entries:
            try:
                rel = f.relative_to(d)
            except ValueError:
                continue
            if any(part in self.skip_dirs or part.startswith(".") for part in rel.parts):
                continue
            if not f.is_file() or str(rel) in ("run.py", "method.json"):
                continue
            out.append({"path": str(rel), "size": _size_of(f), "role": "extra"})
        return out


    def _method_row(self, d: pathlib.Path) -> dict | None:
        spec_path = d / "method.json"
        run_path = d / "run.py"
        if not spec_path.is_file() and not run_path.is_file():
            return None
        spec = read_json(spec_path)
        has_spec = bool(spec)
        has_run_py = run_path.is_file()
        deviations = _deviation_items(spec)
        note = (spec.get("note") or spec.get("what") or spec.get("summary") or "")
        if not isinstance(note, str):
            note = str(note)
        rule_test = self.roots.platform / "tests" / "quality" / f"test_{d.name}_rule.py"
        has_test = rule_test.is_file()
        title = spec.get("title") or ""
        kind = spec.get("kind") or None
        fields = {key: bool(spec.get(key)) for key in ("kind", "title", "note")}
        blockers: list[str] = []
        if not has_run_py:
            blockers.append("没有 run.py：方法没有入口")
        if not has_test:
            blockers.append(f"没有规则测试：tests/quality/test_{d.name}_rule.py 不存在")
        if not has_spec:
            blockers.append("没有可解析的 method.json：kind / title / note 都读不到")
        else:
            missing = [key for key, ok in fields.items() if not ok]
            if missing:
                blockers.append(f"method.json 缺字段：{'、'.join(missing)}")
        return {
            "name": d.name,
            "dir": f"methods/{d.name}",
            "entrypoint": f"python3 {run_path}" if has_run_py else None,
            "title": title,
            "kind": kind,
            "has_spec": has_spec,
            "has_run_py": has_run_py,
            "deviations": deviations,
            "deviation_count": None if deviations is None else len(deviations),
            "extra_files": [f for f in self._method_files(d)
                            if f["role"] == "extra"],
            "has_test": has_test,
            "test_path": f"tests/quality/test_{d.name}_rule.py" if has_test else None,
            # Metadata completeness only: inspection does not run rule tests or admit execution.
            "can_start": has_run_py and has_test and has_spec and all(fields.values()),
            "start_blockers": blockers,
            "validation": {
                "has_run_py": has_run_py,
                "has_test": has_test,
                "has_spec": has_spec,
                "fields": fields,
                "ok": has_run_py and has_test and has_spec and all(fields.values()),
            },
            "summary": note[:300],
            "note": note,
        }


    def api_methods(self) -> list[dict]:
        out: list[dict] = []
        root = self.roots.platform / "methods"
        if not root.is_dir():
            return out
        try:
            dirs = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            return out
        for d in dirs:
            if d.name.startswith(".") or d.name in self.skip_dirs:
                continue
            row = self._method_row(d)
            if row:
                out.append(row)
        return out


    def api_method(self, name: str) -> dict | None:
        """一个方法的元数据 + 文件清单（不含文件正文）。"""
        d = self._method_dir(name)
        if not d:
            return None
        row = self._method_row(d)
        if not row:
            return None
        row["path"] = f"methods/{d.name}"
        row["files"] = self._method_files(d)
        return row


    def _read_text_capped(self, target: pathlib.Path, rel: str,
                          max_bytes: int | None = None,
                          max_lines: int | None = None) -> dict | None:
        """读一个只读文件，带上限和一句人话解释。超过上限时不读正文。"""
        max_bytes = self.limits.method_bytes if max_bytes is None else max_bytes
        max_lines = self.limits.method_lines if max_lines is None else max_lines
        try:
            size = target.stat().st_size
        except OSError:
            return None
        if size > max_bytes:
            return {
                "path": rel, "size": size, "text": "", "truncated": True,
                "lines": None, "shown_lines": 0,
                "note": f"文件 {human_bytes(size)}，超过 {human_bytes(max_bytes)} 读取上限，未在浏览器展开。",
            }
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        all_lines = text.splitlines()
        total = len(all_lines)
        if total > max_lines:
            return {
                "path": rel, "size": size,
                "text": "\n".join(all_lines[:max_lines]), "truncated": True,
                "lines": total, "shown_lines": max_lines,
                "note": f"只显示前 {max_lines} 行（共 {total} 行）。",
            }
        return {
            "path": rel, "size": size, "text": text, "truncated": False,
            "lines": total, "shown_lines": total, "note": "",
        }


    def api_method_file(self, name: str, rel: str) -> dict | None:
        d = self._method_dir(name)
        if not d or not rel:
            return None
        target = (d / rel).resolve()
        if not within(d, target) or not target.is_file():
            return None
        return self._read_text_capped(target, rel)


    def api_protocol(self) -> dict:
        """`methods/protocol.py` 的模块 docstring —— 服务端读，前端只引用，不转述。"""
        path = self.roots.platform / "methods" / "protocol.py"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return {"path": "methods/protocol.py", "doc": None, "sha": None,
                    "lines": None, "error": "读不到 methods/protocol.py"}
        doc = None
        error = ""
        try:
            doc = ast.get_docstring(ast.parse(text), clean=False)
        except (SyntaxError, ValueError) as exc:
            error = f"protocol.py 解析失败：{exc}"
        if not doc:
            error = error or "protocol.py 没有模块 docstring"
        return {
            "path": "methods/protocol.py",
            "doc": doc,
            "sha": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
            "lines": len(text.splitlines()),
            "error": error or None,
        }


    def api_base(self) -> dict:
        """Inspect the configured baseline without computing a measured harness identity.

        The recorded harness SHA belongs to staged state, which may add runtime metadata.
        """
        base = self.roots.base
        if not base.is_dir():
            return {"path": "base_harness/loop", "exists": False, "manifest": {},
                    "files": [], "harness_sha": None,
                    "sha_note": "base_harness/loop 不存在"}
        files: list[dict] = []
        try:
            entries = sorted(base.rglob("*"))
        except OSError:
            entries = []
        for f in entries:
            try:
                rel = f.relative_to(base)
            except ValueError:
                continue
            if any(part in self.skip_dirs or part.startswith(".") for part in rel.parts):
                continue
            if not f.is_file():
                continue
            files.append({"path": str(rel), "size": _size_of(f)})
        manifest = read_json(base / "harness.json")
        return {
            "path": "base_harness/loop",
            "exists": True,
            "manifest": {key: manifest.get(key) for key in
                         ("name", "version", "entrypoint", "backend", "install")},
            "files": files,
            "harness_sha": None,
            "sha_note": "基线没有 run 记录；run 里的 harness_sha 是 staging 后的 git tree hash，本台不重算。",
        }


    def api_base_file(self, rel: str) -> dict | None:
        base = self.roots.base
        if not base.is_dir() or not rel:
            return None
        target = (base / rel).resolve()
        if not within(base, target) or not target.is_file():
            return None
        return self._read_text_capped(target, rel)


    def _base_harnesses_root(self) -> pathlib.Path:
        return self.roots.bases


    def _base_harness_dirs(self) -> list[pathlib.Path]:
        root = self._base_harnesses_root()
        if not root.is_dir():
            return []
        try:
            entries = sorted(root.iterdir())
        except OSError:
            return []
        return [p for p in entries
                if p.is_dir() and p.name not in self.skip_dirs and not p.name.startswith(".")]


    def _safe_rel_files(self, root: pathlib.Path) -> list[dict]:
        """root 下的可读文件（跳过 __pycache__/.state/隐藏目录），按路径排序。"""
        out: list[dict] = []
        try:
            entries = sorted(root.rglob("*"))
        except OSError:
            return out
        for f in entries:
            try:
                rel = f.relative_to(root)
            except ValueError:
                continue
            if any(part in self.skip_dirs or part.startswith(".") for part in rel.parts):
                continue
            if not f.is_file():
                continue
            out.append({"path": str(rel), "size": _size_of(f)})
        return out


    def api_base_harnesses(self) -> dict:
        """Inspect existing baseline manifests/files without importing or executing them."""
        rows: list[dict] = []
        for d in self._base_harness_dirs():
            manifest = read_json(d / "harness.json")
            files = self._safe_rel_files(d)
            rows.append({
                "name": d.name,
                "path": f"base_harness/{d.name}",
                "manifest": {key: manifest.get(key) for key in MANIFEST_KEYS},
                "files": files,
                "file_count": len(files),
                "bytes": sum(f["size"] for f in files if isinstance(f["size"], int)),
                "harness_sha": None,
            })
        return {
            "root": "base_harness",
            "rows": rows,
            "count": len(rows),
            "sha_note": ("基线没有 run 记录；run 里的 harness_sha 是 staging（补 .gitignore/.state）"
                         "之后的 git tree hash，本台不重算，也不拿别的 sha 冒充它。"),
            "note": ("`base_harness/*/` 是已存在的起点；这一屏只读它们，不复制、不执行、不修改。"
                     "没有列出来的目录（__pycache__ 等）不是起点。"),
        }


    def _base_harness_dir(self, name: str) -> pathlib.Path | None:
        if (not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name or "")
                or name in (".", "..") or name in self.skip_dirs or name.startswith(".")):
            return None
        root = self._base_harnesses_root()
        d = root / name
        try:
            if not d.is_dir() or not within(root, d):
                return None
        except OSError:
            return None
        return d


    def api_base_harness_file(self, name: str, rel: str) -> dict | None:
        """`base_harness/<name>/<rel>` 的正文（只读，超限截断并说明）。"""
        d = self._base_harness_dir(name)
        if not d or not rel:
            return None
        try:
            target = (d / rel).resolve()
        except OSError:
            return None
        if not within(d, target) or not target.is_file():
            return None
        return self._read_text_capped(target, rel, max_bytes=self.limits.panel_bytes,
                                 max_lines=self.limits.panel_lines)


    def api_adapters(self) -> dict:
        """Read dataset KIND/SPLIT declarations without importing adapters or downloading data.

        Declaration presence is not runtime/dependency readiness.
        """
        rows: list[dict] = []
        if self.roots.datasets.is_dir():
            try:
                entries = sorted(self.roots.datasets.glob("*.py"))
            except OSError:
                entries = []
            for path in entries:
                if path.stem in ("__init__", "registry"):
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                m = _KIND_RE.search(text)
                kind = m.group(1) if m else None
                rows.append({
                    "name": path.stem,
                    "kind": kind,
                    "published": kind == "dataset",
                    "module": f"data.datasets.{path.stem}",
                    "path": f"data/datasets/{path.name}",
                    "has_split": bool(_SPLIT_RE.search(text)),
                })
        return {
            "root": "data/datasets",
            "rows": rows,
            "count": len(rows),
            "published": [r["name"] for r in rows if r["published"]],
            "note": ("KIND == \"dataset\" 的才是可选数据集；其余是平台自己的 fixture。"
                     "本台不 import 适配器，也不下载数据。"),
        }
