"""Author-bundle listing/import/draft orchestration with explicit builder/process ports.

Listing retains the existing builder validation/gates behavior. Extraction and
draft mechanics are not represented as side-effect-free artifact inspection.
"""
from __future__ import annotations
import importlib.util
import json
import os
import pathlib
import re
import shutil
import sys
import time
import tarfile
import zipfile

def list_bundles(authored_root, builder_path) -> dict:
    """每个 authored bundle 的结构校验 + 三道门，都从磁盘现算，没有缓存。"""
    rows = []
    if not authored_root.is_dir():
        return {"root": str(authored_root), "rows": rows}
    spec = importlib.util.spec_from_file_location("_scenario_builder", builder_path)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    for path in sorted(authored_root.iterdir()):
        if not path.is_dir() or not (path / "bundle.json").is_file():
            continue
        row = {"slug": path.name, "path": str(path)}
        try:
            data = json.loads((path / "bundle.json").read_text(encoding="utf-8"))
            row.update(title=data.get("title"), env_kind=(data.get("env") or {}).get("kind"),
                       tasks=len(data.get("tasks") or []),
                       eval_visible_to_authors=data.get("eval_visible_to_authors"))
            problems = builder.check_bundle(path)
            row["valid"] = not problems
            row["problems"] = problems
            if row["valid"]:
                gates = builder.gates(path)
                row["gates_ok"] = bool(gates.get("ok"))
                row["gates"] = [{"task_id": t["task_id"],
                                 "oracle": t["oracle_passes"],
                                 "empty": t["empty_fails"],
                                 "deterministic": t["deterministic"],
                                 "detail": t.get("detail", {})} for t in gates.get("tasks") or []]
            else:
                row["gates_ok"] = False
        except (OSError, ValueError) as exc:
            row["valid"] = False
            row["problems"] = [str(exc)]
            row["gates_ok"] = False
        rows.append(row)
    return {"root": str(authored_root), "rows": rows}


def safe_extract(archive: pathlib.Path, target: pathlib.Path) -> None:
    """tar/zip -> target, but only regular files and directories, and only inside target."""
    import tarfile
    import zipfile
    target.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                name = member.filename
                if member.is_dir():
                    continue
                if name.startswith("/") or ".." in pathlib.PurePosixPath(name).parts:
                    raise ValueError(f"归档里有越界路径：{name}")
                if (member.external_attr >> 16) & 0o170000 not in (0, 0o100000):
                    raise ValueError(f"归档里有不允许的条目类型：{name}")
                out = target / name
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(zf.read(member))
        return
    with tarfile.open(archive) as tf:
        for member in tf.getmembers():
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError(f"归档里有不允许的条目类型：{member.name}")
            if member.name.startswith("/") or ".." in pathlib.PurePosixPath(member.name).parts:
                raise ValueError(f"归档里有越界路径：{member.name}")
            out = target / member.name
            out.parent.mkdir(parents=True, exist_ok=True)
            with tf.extractfile(member) as src, out.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def import_bundle(archive_path: pathlib.Path, wanted_slug: str = "", *, authored_root, max_bytes, extract, list_bundles) -> tuple[int, dict]:
    """Import one bundle archive into `data/authored/<slug>/`, then validate and gate it."""
    import tempfile
    if not archive_path.is_file():
        return 400, {"error": "no-archive"}
    if archive_path.stat().st_size > max_bytes:
        return 413, {"error": "archive-too-large", "limit": max_bytes}
    with tempfile.TemporaryDirectory(prefix="hg-import-") as tmp:
        staging = pathlib.Path(tmp) / "x"
        try:
            extract(archive_path, staging)
        except (tarfile.TarError, zipfile.BadZipFile, OSError, ValueError) as exc:
            return 400, {"error": "bad-archive", "detail": str(exc)}
        roots = [p for p in staging.iterdir()]
        root = staging
        if len(roots) == 1 and roots[0].is_dir():
            root = roots[0]          # 允许"多套一层目录"的打包习惯
        bundle = root / "bundle.json"
        if not bundle.is_file():
            return 400, {"error": "manifest-missing",
                         "detail": "归档根（或唯一顶层目录）里没有 bundle.json"}
        try:
            data = json.loads(bundle.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return 400, {"error": "manifest-invalid", "detail": str(exc)}
        slug = wanted_slug or str(data.get("slug") or "").strip() or root.name
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", slug):
            return 400, {"error": "bad-slug", "detail": f"不接受的 slug: {slug!r}"}
        data["slug"] = slug
        bundle.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        target = authored_root / slug
        if target.exists():
            return 409, {"error": "duplicate-bundle", "detail": f"{target} 已存在；先删掉或换个 slug"}
        authored_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(root, target)
    listed = list_bundles()
    row = next((r for r in listed["rows"] if r["slug"] == slug), None)
    if row is None or not row.get("valid"):
        return 400, {"error": "bundle-invalid", "detail": row.get("problems") if row else "未找到",
                     "row": row}
    return 201, {"row": row, "imported": str(target)}


def draft(note: str, materials: str = "", *, authored_root, builder_path, platform, list_bundles, run_process, environment) -> tuple[int, dict]:
    """起草：把这段自然语言交给 scenario_builder，它自己会校验、跑门、重试。"""
    if not isinstance(note, str) or not note.strip():
        return 400, {"error": "bad-note"}
    import subprocess
    import tempfile
    slug = "draft-" + time.strftime("%Y%m%d-%H%M%S")
    out = authored_root / slug
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(builder_path), "--draft", "-", "--out", str(out), "--json"]
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
        fh.write(note)
        note_path = fh.name
    cmd[cmd.index("-")] = note_path
    if materials:
        cmd += ["--materials", materials]
    try:
        done = run_process(cmd, cwd=str(platform), capture_output=True, text=True,
                              timeout=900, env=dict(environment))
    finally:
        os.unlink(note_path)
    if done.returncode != 0:
        return 422, {"error": "draft-refused", "detail": (done.stderr or done.stdout)[-1500:]}
    listed = list_bundles()
    row = next((r for r in listed["rows"] if r["slug"] == slug), None)
    return 200, {"row": row, "output": (done.stdout or "")[-1500:]}
