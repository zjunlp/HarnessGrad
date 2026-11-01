"""
Workspace loader. Reads the evolvable layout produced by `evolve.py init`
(or any iter_NNN/evolve/workspace/) and returns a bundle the agent.py
runtime consumes.

Workspace layout (mirrors harness/seed_workspace/):
  agent.yaml
  prompts/{system,extract}.md
  tool_descriptions/<name>.json
  tools/<name>.py             (optional, exports `async def call(args, ctx) -> str`)
  middleware/<name>.py        (optional, exports any lifecycle hook)
  skills/<name>.md            (optional, inlined into system prompt)
  skills/<name>/SKILL.md      (optional, catalog + read_skill tool)
  sub_agents/<name>.py        (optional, exports `async def run(input, ctx) -> str`)
"""

from __future__ import annotations

import importlib.util
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import yaml

if TYPE_CHECKING:
    from .agent import Context


# Type aliases for clarity (no enforcement — duck-typed at the call site).
ToolCall = Callable[[dict, "Context"], Awaitable[Any]]
SubAgent = Callable[[str, "Context"], Awaitable[str]]
BUILTIN_TOOL_NAMES = {"web_search", "read_url"}


@dataclass
class Middleware:
    name: str
    before_llm: Callable | None = None
    after_llm: Callable | None = None
    before_tool: Callable | None = None
    after_tool: Callable | None = None
    before_final: Callable | None = None


@dataclass
class WorkspaceBundle:
    root: Path
    system_prompt: str  # system.md + skills (already concatenated)
    extract_prompt: str
    tool_schemas: list[dict]  # in agent.yaml order, OpenAI format
    tool_names: list[str]  # parallel to tool_schemas
    extra_tools: dict[str, ToolCall] = field(default_factory=dict)
    middlewares: list[Middleware] = field(default_factory=list)
    sub_agents: dict[str, SubAgent] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_module(name_hint: str, path: Path):
    """Import a .py file by absolute path with a unique module name."""
    mod_name = f"_ws_{name_hint}_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load(workspace_dir: str | Path) -> WorkspaceBundle:
    root = Path(workspace_dir)
    if not root.exists():
        raise FileNotFoundError(f"workspace not found: {root}")
    cfg_path = root / "agent.yaml"
    if not cfg_path.exists():
        raise FileNotFoundError(f"workspace missing agent.yaml: {cfg_path}")
    cfg = yaml.safe_load(_read_text(cfg_path)) or {}

    # Prompts
    prompts_cfg = cfg.get("prompts", {}) or {}
    system_path = root / prompts_cfg.get("system", "prompts/system.md")
    extract_path = root / prompts_cfg.get("extract", "prompts/extract.md")
    system_prompt = _read_text(system_path)
    extract_prompt = _read_text(extract_path)

    # Configured flat skills retain their original inline behavior. Directory
    # skills are discovered from files so adding/removing one needs no config edit.
    skill_blocks: list[str] = []
    for name in cfg.get("skills") or []:
        path = root / "skills" / f"{name}.md"
        skill_blocks.append(f"\n\n## Skill: {name}\n\n{_read_text(path)}")
    directories = sorted((root / "skills").glob("*/SKILL.md"))
    for path in directories:
        name = path.parent.name
        if (path.parent.parent / f"{name}.md").exists():
            raise ValueError(f"directory skill {name} shadows a flat skill")
        text = _read_text(path)
        match = re.match(r"^---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.DOTALL)
        meta = yaml.safe_load(match[1]) if match else {}
        if not isinstance(meta, dict) or not meta.get("description"):
            raise ValueError(f"directory skill {name} requires a description")
        skill_blocks.append(
            f"\n\n## Available skill: {name}\n{meta['description']}\n"
            f"Use read_skill with skill={name!r} only if relevant to the task."
        )
    if skill_blocks:
        system_prompt = system_prompt.rstrip() + "".join(skill_blocks)

    # The directory catalog and its reader appear together, only when needed.
    tool_names: list[str] = list(cfg.get("tools") or [])
    if directories and "read_skill" not in tool_names:
        tool_names.append("read_skill")
    tool_schemas: list[dict] = []
    extra_tools: dict[str, ToolCall] = {}
    for name in tool_names:
        desc_path = root / "tool_descriptions" / f"{name}.json"
        desc = json.loads(_read_text(desc_path))
        tool_schemas.append({"type": "function", "function": desc})
        impl_path = root / "tools" / f"{name}.py"
        if directories and name == "read_skill" and not impl_path.is_file():
            raise FileNotFoundError(f"directory skills require {impl_path}")
        if impl_path.exists():
            if name in BUILTIN_TOOL_NAMES:
                raise AttributeError(
                    f"workspace tool {name!r} attempts to override a built-in; "
                    "change its prompt/schema or register a new tool name instead"
                )
            mod = _load_module(f"tool_{name}", impl_path)
            if not hasattr(mod, "call"):
                raise AttributeError(f"workspace tool {name} missing `call`")
            extra_tools[name] = mod.call

    # Middlewares
    middlewares: list[Middleware] = []
    for name in cfg.get("middlewares") or []:
        path = root / "middleware" / f"{name}.py"
        mod = _load_module(f"mw_{name}", path)
        middlewares.append(
            Middleware(
                name=name,
                before_llm=getattr(mod, "before_llm", None),
                after_llm=getattr(mod, "after_llm", None),
                before_tool=getattr(mod, "before_tool", None),
                after_tool=getattr(mod, "after_tool", None),
                before_final=getattr(mod, "before_final", None),
            )
        )

    # Sub-agents
    sub_agents: dict[str, SubAgent] = {}
    for name in cfg.get("sub_agents") or []:
        path = root / "sub_agents" / f"{name}.py"
        mod = _load_module(f"sa_{name}", path)
        if not hasattr(mod, "run"):
            raise AttributeError(f"workspace sub_agent {name} missing `run`")
        sub_agents[name] = mod.run

    return WorkspaceBundle(
        root=root,
        system_prompt=system_prompt,
        extract_prompt=extract_prompt,
        tool_schemas=tool_schemas,
        tool_names=tool_names,
        extra_tools=extra_tools,
        middlewares=middlewares,
        sub_agents=sub_agents,
    )


SEED_WORKSPACE: Path = Path(__file__).parent.parent
