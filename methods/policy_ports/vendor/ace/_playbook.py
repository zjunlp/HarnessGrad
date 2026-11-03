"""ACE's private ADD protocol, sectioned bullets and usage counters.

Adapted from FlashRSI 5efe9b438483710060183c6c3ecbc5062b410a58.
Upstream: https://github.com/ace-agent/ace
Commit: 82709de050e1db6e6ef2f07bcb0393560b94992a
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_BULLET = re.compile(r"^\[([^\]]+)\]\s*helpful=(\d+)\s*harmful=(\d+)\s*::\s*(.*)$")
_SLUGS = {
    "financial_strategies_and_insights": "fin",
    "formulas_and_calculations": "calc",
    "code_snippets_and_templates": "code",
    "common_mistakes_to_avoid": "err",
    "problem_solving_heuristics": "prob",
    "context_clues_and_indicators": "ctx",
    "others": "misc",
    "meta_strategies": "meta",
}


class PlaybookError(ValueError):
    """The reply is not a valid playbook update."""


@dataclass(frozen=True)
class Reflection:
    content: str
    tags: tuple[tuple[str, str], ...] = ()

    @classmethod
    def parse(cls, reply: str) -> Reflection:
        value = _json(reply)
        raw_tags = value.get("bullet_tags")
        tags = tuple(
            (item["id"], item["tag"])
            for item in (raw_tags if isinstance(raw_tags, list) else [])
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and item.get("tag") in ("helpful", "harmful", "neutral")
        )
        parts = [
            value[name].strip()
            for name in (
                "error_identification",
                "root_cause_analysis",
                "correct_approach",
                "key_insight",
            )
            if isinstance(value.get(name), str) and value[name].strip()
        ]
        # A prose diagnosis still helps a retry, unlike an unparseable edit.
        return cls("\n".join(parts) or reply.strip(), tags)


@dataclass(frozen=True)
class Patch:
    adds: tuple[tuple[str, str], ...]

    @classmethod
    def parse(cls, reply: str) -> Patch:
        value = _json(reply)
        operations = value.get("operations")
        if not isinstance(operations, list):
            raise PlaybookError("ACE Curator update requires an operations list")
        adds = []
        for operation in operations:
            if not isinstance(operation, dict):
                raise PlaybookError("ACE Curator operation must be an object")
            if str(operation.get("type", "")).upper() != "ADD":
                raise PlaybookError("ACE Curator may only ADD")
            content, section = (
                operation.get("content"),
                operation.get("section", "general"),
            )
            if not isinstance(content, str) or not content.strip():
                raise PlaybookError("ACE Curator ADD requires content")
            if not isinstance(section, str) or not section.strip():
                raise PlaybookError("ACE Curator ADD requires a section")
            # Each operation is exactly one system-numbered bullet. Multiline
            # prose cannot create extra headings or forge another bullet's ID.
            adds.append((_normalized(section), " ".join(content.split())))
        return cls(tuple(adds))

    def apply(self, playbook: str, budget: int) -> str:
        if len(self.adds) > budget:
            raise PlaybookError(
                f"update contains {len(self.adds)} additions; budget={budget}"
            )
        existing = [
            match
            for line in playbook.splitlines()
            if (match := _BULLET.match(line.strip()))
        ]
        seen = {" ".join(match[4].split()).casefold() for match in existing}
        # A bullet's ID is prefix-NNNNN, and the number is unique across
        # sections, so the whole document sets where the next one starts.
        next_id = 1 + max(
            (
                int(suffix[1])
                for match in existing
                if (suffix := re.search(r"-(\d+)$", match[1]))
            ),
            default=0,
        )
        result = playbook
        for section, content in self.adds:
            if content.casefold() in seen:
                continue
            words = section.split("_")
            prefix = _SLUGS.get(
                section, "".join(word[0] for word in words if word)[:5] or "misc"
            )
            bullet = f"[{prefix}-{next_id:05d}] helpful=0 harmful=0 :: {content}"
            result = _file_under(result, section, bullet)
            seen.add(content.casefold())
            next_id += 1
        return result


def tag(playbook: str, tags: tuple[tuple[str, str], ...]) -> str:
    verdicts = dict(tags)
    lines = playbook.splitlines(keepends=True)
    for index, line in enumerate(lines):
        match = _BULLET.match(line.strip())
        if not match or verdicts.get(match[1]) not in ("helpful", "harmful"):
            continue
        helpful = int(match[2]) + (verdicts[match[1]] == "helpful")
        harmful = int(match[3]) + (verdicts[match[1]] == "harmful")
        ending = "\n" if line.endswith("\n") else ""
        lines[index] = (
            f"[{match[1]}] helpful={helpful} harmful={harmful} :: {match[4]}{ending}"
        )
    return "".join(lines)


def _json(reply: str) -> dict:
    text = reply.strip()
    if text.startswith("```"):
        text = text.partition("\n")[2].rpartition("```")[0].strip() or text
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if not 0 <= start < end:
            return {}
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _normalized(section: str) -> str:
    return "_".join(section.lower().strip().replace("&", "and").split())


def _file_under(playbook: str, section: str, bullet: str) -> str:
    lines = playbook.splitlines()
    headings = [
        (index, _normalized(line.lstrip("#").strip()))
        for index, line in enumerate(lines)
        if line.startswith("## ")
    ]
    if section != "general" and section not in {name for _, name in headings}:
        section = "others"
    at = next((index for index, name in headings if name == section), None)
    if at is None:
        if section == "general" and not headings:
            lines.extend(("", bullet))
        else:
            lines.extend(("", "## " + section.replace("_", " ").upper(), bullet))
    else:
        following = next((index for index, _ in headings if index > at), len(lines))
        lines.insert(following, bullet)
    return "\n".join(lines).strip() + "\n"
