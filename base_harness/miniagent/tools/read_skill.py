"""Load a discovered directory skill from this native workspace."""

import re


async def call(args, ctx) -> str:
    name = args.get("skill")
    root = ctx.workspace.root
    skills_dir = (root / "skills").resolve()
    if not isinstance(name, str):
        raise ValueError("skill must name a discovered workspace skill")
    path = (skills_dir / name / "SKILL.md").resolve()
    if (
        path.parent.parent != skills_dir
        or path.parent.name != name
        or not path.is_file()
    ):
        raise ValueError("skill must name a discovered workspace skill")
    text = path.read_text(encoding="utf-8")
    # Provenance metadata is for the curator; execution needs the procedure.
    match = re.match(r"^---\r?\n.*?\r?\n---(?:\r?\n|$)", text, re.DOTALL)
    return text[match.end() :].strip() if match else text
