#!/usr/bin/env python3
"""Loop 0.3.0 with optional PNG/JPEG attachments on shell actions."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4


# Outer loop turns include parse and submission retries.
MAX_STEPS = int(os.environ.get("HG_AGENT_MAX_STEPS", "32"))
MAX_OUTPUT_TOKENS = int(os.environ.get("HG_AGENT_MAX_OUTPUT_TOKENS", "2048"))
if MAX_OUTPUT_TOKENS < 1:
    raise ValueError("HG_AGENT_MAX_OUTPUT_TOKENS must be positive")

SUBMIT_RETRIES = int(os.environ.get("HG_AGENT_SUBMIT_RETRIES", "2"))

PARSE_RETRIES = int(os.environ.get("HG_AGENT_PARSE_RETRIES", "2"))

FINISH_NUDGE_STEPS = int(os.environ.get("HG_AGENT_FINISH_NUDGE_STEPS", "4"))

REPEAT_NUDGE_LIMIT = int(os.environ.get("HG_AGENT_REPEAT_NUDGE_LIMIT", "2"))

OUTPUT_LIMIT = int(os.environ.get("HG_AGENT_OUTPUT_LIMIT", "4000"))

CONTEXT_CHAR_BUDGET = int(os.environ.get("HG_AGENT_CONTEXT_CHARS", "60000"))
MAX_IMAGES = 3
MAX_IMAGE_BYTES = 1024 * 1024

SYSTEM = """You are an agent solving a task in a working directory.

You have one tool: run a shell command with optional image attachments.

Reply with EXACTLY one of:
  {"tool": "bash", "command": "<shell command>"}
  {"tool": "bash", "command": "<shell command>", "images": ["relative/path.png"]}
  {"answer": "<your final answer>"}

The optional images field attaches PNG or JPEG files from the working directory
after the command succeeds. You can create and attach the files in the same action.

For a structured final answer, "answer" may be a JSON object containing the task's
prediction fields.

Rules:
- One action per reply. No prose outside the JSON.
- Keep commands concise. Use a loop or a script instead of enumerating many calls.
- To inspect files use shell commands (ls, cat, grep, ...).
- Give the final answer only when you are confident.
"""


# Model backends

def _backend() -> str:
    """Select the model backend, defaulting to mock for offline checks."""
    return os.environ.get("HG_AGENT_BACKEND", "mock")


def _call_mock(messages: list[dict]) -> str:
    """Return a deterministic response based on the task text."""
    task_text = messages[1]["content"] if len(messages) > 1 else ""
    if "answer.txt exists" in task_text or "already" in task_text:
        return json.dumps({"answer": "READ_FROM_FILE"})
    return json.dumps({"tool": "bash", "command": "ls -a"})


# Track context-trimming retries separately from other retries.
USAGE = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "retries": 0,
         "context_trims": 0}


def _record_usage(usage) -> None:
    """Add reported token counts. _complete counts logical calls."""
    if usage is None:
        return
    for attr, key in (("prompt_tokens", "input_tokens"),
                      ("completion_tokens", "output_tokens")):
        v = getattr(usage, attr, None)
        if isinstance(v, int):
            USAGE[key] += v


def _call_openai(messages: list[dict], *, visual_request=None) -> str:
    """Request a completion with a per-request timeout and bounded retries."""
    from openai import OpenAI

    # Use a fresh connection to avoid stale sockets after long shell actions.
    client = OpenAI(
        base_url=os.environ["HG_AGENT_BASE_URL"],
        api_key=os.environ["HG_AGENT_API_KEY"],
        timeout=float(os.environ.get("HG_AGENT_TIMEOUT_S", "180")),
        max_retries=0,          # retried here, so the count is ours to record
        default_headers={"Connection": "close"},
    )
    model = os.environ["HG_AGENT_MODEL"]
    attempts = int(os.environ.get("HG_AGENT_RETRIES", "3"))
    last: Exception | None = None

    for attempt in range(attempts):
        try:
            request_id = visual_request(messages) if visual_request is not None else None
            resp = client.chat.completions.create(
                model=model, messages=messages, temperature=0.0,
                max_tokens=MAX_OUTPUT_TOKENS,
                **({"extra_headers": {"X-HarnessGrad-Visual-Request": request_id}} if request_id else {}),
            )
            _record_usage(getattr(resp, "usage", None))
            return resp.choices[0].message.content or ""
        except Exception as exc:                      # noqa: BLE001
            last = exc
            # Use the provider's context-limit response to retry with less history.
            if any(marker in str(exc) for marker in _CONTEXT_ERROR_MARKERS) \
                    and len(messages) > 4 and attempt + 1 < attempts:
                dropped = _trim_history(messages, budget=(CONTEXT_CHAR_BUDGET // 2))
                if not dropped:
                    dropped = _trim_history(messages, budget=1)
                USAGE["context_trims"] += 1
                USAGE["retries"] += 1
                continue
            if attempt + 1 < attempts:
                USAGE["retries"] += 1
                time.sleep(min(2 ** attempt, 30))
    raise RuntimeError(f"{attempts} attempts failed, last: {last}") from last


def _complete(messages: list[dict], *, visual_request=None) -> str:
    """Dispatch one logical model call and count it once."""
    USAGE["calls"] += 1
    return _call_mock(messages) if _backend() == "mock" else _call_openai(messages, visual_request=visual_request)


class _Action(dict):
    """A parsed action with the duplicate keys recorded during decoding."""
    duplicates: tuple[str, ...] = ()


def _first_key_wins(pairs):
    """Keep the earliest value for each key and record repetitions."""
    action = _Action()
    repeated: list[str] = []
    for key, value in pairs:
        if key in action:
            repeated.append(key)
            continue
        action[key] = value
    if repeated:
        action.duplicates = tuple(repeated)
    return action


def _json_block_end(text: str, start: int) -> int:
    """Skip a malformed JSON block without treating its strings as actions."""
    depth = 0
    quoted = escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if quoted:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = False
        elif ch == '"':
            quoted = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


def _parse(reply: str) -> dict:
    """Read the first complete action, allowing prose or fences around it.

    Skip entire JSON blocks so nested examples and truncated commands
    cannot supply executable actions.
    """
    decoder = json.JSONDecoder(object_pairs_hook=_first_key_wins)
    i = 0
    while i < len(reply):
        if reply[i] not in "{[":
            i += 1
            continue
        try:
            obj, end = decoder.raw_decode(reply, i)
        except json.JSONDecodeError:
            i = _json_block_end(reply, i)
            continue
        i = end
        if not isinstance(obj, dict) or obj.get("tool", "bash") != "bash":
            continue
        cmd = obj.get("command", "")
        if isinstance(cmd, str) and cmd.strip():
            return obj
        if cmd == "" and "answer" in obj:
            _answer_text(obj["answer"])
            return obj
    raise ValueError("no complete JSON action found in reply")


def _answer_stamp(workdir: Path):
    """Read answer.txt for byte-for-byte comparison before and after the run."""
    path = workdir / "answer.txt"
    try:
        return path.read_bytes()
    except OSError:
        return None


def _answer_text(value: object) -> str:
    """Serialize a final answer while preserving structured JSON.

    A string beginning with a JSON object must parse as written.
    """
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    if not isinstance(value, str):
        raise ValueError("final answer must be a string or a JSON object")
    if value.lstrip().startswith("{"):
        try:
            json.loads(value)
        except ValueError as exc:
            raise ValueError(f"final answer contains malformed JSON: {exc}") from exc
    return value


_PATH_RE = __import__("re").compile(r"/[A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,6}\b")


def _missing_deliverables(task: dict, workdir: Path) -> list[str]:
    """Report missing files named by absolute path in the task goal."""
    goal = str(task.get("goal") or "")
    wanted = {m for m in _PATH_RE.findall(goal)}
    missing = []
    for raw in sorted(wanted):
        # Check the absolute path and map /app paths into the native task directory.
        candidates = [Path(raw)]
        try:
            candidates.append(workdir / Path(raw).relative_to("/app"))
        except ValueError:
            pass
        if not any(c.exists() for c in candidates):
            missing.append(raw)
    return missing


def _clip_text(out: str, limit: int) -> str:
    if len(out) <= limit:
        return out
    marker = f"\n... [text clipped; original {len(out)} characters] ...\n"
    room = max(0, limit - len(marker))
    head = (room + 1) // 2
    tail = room // 2
    return (out[:head] + marker + (out[-tail:] if tail else ""))[:limit]


def _json_preview(value: list | dict, limit: int) -> str | None:
    """A bounded, valid JSON preview containing whole first/last entries."""
    mapping = isinstance(value, dict)
    items = list(value.items()) if mapping else value
    head, tail = [], []

    def render():
        return json.dumps({"_harness_preview": {
            "total_items": len(items), "omitted_items": len(items) - len(head) - len(tail)},
            "head": dict(head) if mapping else head,
            "tail": dict(tail) if mapping else tail}, ensure_ascii=False, separators=(",", ":"))

    shown = render()
    if len(shown) > limit:
        return None
    front, back = 0, len(items) - 1
    while front <= back:
        added = False
        head.append(items[front])
        candidate = render()
        if len(candidate) <= limit:
            shown = candidate
            front += 1
            added = True
        else:
            head.pop()
        if front <= back:
            tail.insert(0, items[back])
            candidate = render()
            if len(candidate) <= limit:
                shown = candidate
                back -= 1
                added = True
            else:
                tail.pop(0)
        if not added:
            break
    return shown


def _clip_output(out: str) -> str:
    """Fit shell output into the text budget with a JSON preview when possible.

    The caller saves the full output whenever the displayed text changes.
    """
    if len(out) <= OUTPUT_LIMIT:
        return out
    stripped = out.lstrip()
    try:
        obj, end = json.JSONDecoder().raw_decode(stripped)
    except (ValueError, RecursionError):
        return _clip_text(out, OUTPUT_LIMIT)
    if not isinstance(obj, (dict, list)):
        return _clip_text(out, OUTPUT_LIMIT)
    suffix = stripped[end:]
    compact = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + suffix
    if len(compact) <= OUTPUT_LIMIT:
        return compact
    preview = _json_preview(obj, OUTPUT_LIMIT - len(suffix))
    if preview is not None:
        return preview + suffix
    return _clip_text(compact, OUTPUT_LIMIT)


def _shell_runtime() -> tuple[str, str]:
    """Choose a shell up front to avoid switching shells after a command fails."""
    bash = shutil.which("bash")
    return (bash, "Bash") if bash else ("/bin/sh", "POSIX sh")


def _image_paths(names) -> list[Path]:
    if not isinstance(names, list) or len(names) > MAX_IMAGES:
        raise ValueError(f"images must be an array of at most {MAX_IMAGES} relative paths")
    paths = []
    for name in names:
        if not isinstance(name, str) or not name or "\x00" in name:
            raise ValueError("each image must have a nonempty relative file path")
        path = Path(name)
        if path.is_absolute() or not path.parts or ".." in path.parts:
            raise ValueError("image paths must be relative to the working directory")
        paths.append(path)
    if len(set(paths)) != len(paths):
        raise ValueError("image paths must be distinct")
    return paths


def _result_observation(stdout: str) -> tuple[dict, int | None]:
    try:
        result = json.loads(stdout)
        if isinstance(result, dict) and isinstance(result.get("images"), dict):
            images = result["images"]
            paths = _image_paths(list(images.values()))
            step = result.get("step")
            return dict(zip(paths, images)), step if type(step) is int and step >= 0 else None
    except (ValueError, RecursionError):
        pass
    return {}, None


def _read_image(workdir: Path, path: Path) -> bytes:
    # Keep lookups inside the working directory while background commands run.
    # Nonblocking open lets us reject FIFOs safely.
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = os.open(workdir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[:-1]:
            child = os.open(part, directory_flags, dir_fd=parent)
            os.close(parent)
            parent = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("expected a regular file")
            if info.st_size > MAX_IMAGE_BYTES:
                raise ValueError("image exceeds 1 MiB")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(MAX_IMAGE_BYTES + 1)
        finally:
            os.close(fd)
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError("image exceeds 1 MiB")
        return data
    finally:
        os.close(parent)


def _image_attachments(workdir: Path, paths: list[Path], exit_code: int):
    parts = []
    records = []
    for path in paths:
        row = {"path": str(path)}
        records.append(row)
        if exit_code != 0:
            row["error"] = "shell command failed"
            continue
        try:
            data = _read_image(workdir, path)
            if data.startswith(b"\x89PNG\r\n\x1a\n") and data.endswith(b"IEND\xae\x42\x60\x82"):
                media_type = "image/png"
            elif data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
                media_type = "image/jpeg"
            else:
                raise ValueError("expected a PNG or JPEG image")
        except OSError as exc:
            row["error"] = exc.strerror or "image cannot be read"
        except ValueError as exc:
            row["error"] = str(exc)
        else:
            encoded = base64.b64encode(data).decode("ascii")
            parts.append({"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{encoded}"}})
            row.update(media_type=media_type, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    return parts, records


def _text_chars(message: dict) -> int:
    content = message["content"]
    if isinstance(content, str):
        return len(content)
    return sum(len(part["text"]) for part in content if part["type"] == "text")


def _trim_history(messages: list[dict], budget: int | None = None) -> int:
    """Drop the oldest action and result pairs to fit the text budget.

    Keep the system prompt, task, and at least one pair. Image payloads
    use separate attachment limits. Return the number of messages removed.
    """
    limit = CONTEXT_CHAR_BUDGET if budget is None else budget
    dropped = 0
    while len(messages) > 4 and \
            sum(_text_chars(message) for message in messages) > limit:
        del messages[2:4]
        dropped += 2
    return dropped


_CONTEXT_ERROR_MARKERS = ("maximum context length", "context length is", "context_length_exceeded",
                          "reduce the length of the input prompt")


def run(task: dict, workdir: Path) -> str:
    """Execute one task, write its trace, and return the final answer."""
    trace_path = workdir / "trace.jsonl"
    before = _answer_stamp(workdir)
    shell, dialect = _shell_runtime()
    messages = [
        {"role": "system", "content": SYSTEM + (
            f"\nShell runtime: {shell} -c ({dialect}). Each command starts a new shell.\n"
            + ("Bash arrays and PIPESTATUS are supported.\n" if dialect == "Bash" else
               "Use POSIX syntax; Bash arrays, PIPESTATUS and [[ ]] are unavailable.\n"))},
        {"role": "user", "content": json.dumps(task)},
    ]

    answer = ""
    submits = 0
    parse_retries = 0
    repeats = 0
    last_command_result = None
    output_dir = None
    latest_image_message = None
    latest_image_source = None
    capture_visual = os.environ.get("HG_ACTOR_VISUAL_EVIDENCE") == "1"
    # Flush each trace line so partial runs retain their latest events.
    with trace_path.open("w", buffering=1) as trace:
        # Record effective settings so environment overrides are visible in the trace.
        identity = {"harness": "loop-vision", "config": {
            "max_steps": MAX_STEPS, "submit_retries": SUBMIT_RETRIES,
            "parse_retries": PARSE_RETRIES, "finish_nudge_steps": FINISH_NUDGE_STEPS,
            "repeat_nudge_limit": REPEAT_NUDGE_LIMIT, "output_limit": OUTPUT_LIMIT,
            "context_chars": CONTEXT_CHAR_BUDGET,
            "shell": shell, "shell_dialect": dialect,
            "max_output_tokens": MAX_OUTPUT_TOKENS, "max_images": MAX_IMAGES,
            "max_image_bytes": MAX_IMAGE_BYTES}}
        for key, value in (("agent_model", os.environ.get("HG_AGENT_MODEL")),
                           ("agent_backend", _backend())):
            if value:
                identity[key] = value
        trace.write(json.dumps({"harness_identity": identity}) + "\n")
        started = time.time()
        model_s = command_s = 0.0
        trims_before = USAGE["context_trims"]
        for step in range(MAX_STEPS):
            dropped = _trim_history(messages)
            if dropped:
                trace.write(json.dumps({"step": step, "history_dropped": dropped}) + "\n")
            t0 = time.time()
            def record_visual_request(current_messages):
                if latest_image_source is None or not any(
                        message is latest_image_message for message in current_messages):
                    return None
                request_id = uuid4().hex
                trace.write(json.dumps({"visual_request": {"id": request_id, "model_step": step,
                                                           **latest_image_source}}) + "\n")
                return request_id

            reply = (_complete(messages, visual_request=record_visual_request)
                     if capture_visual else _complete(messages))
            took = time.time() - t0
            model_s += took
            trace.write(json.dumps({"step": step, "reply": reply,
                                    "model_ms": int(took * 1000)}) + "\n")
            if USAGE["context_trims"] > trims_before:
                trace.write(json.dumps({
                    "step": step, "context_trims": USAGE["context_trims"],
                    "history_kept": len(messages)}) + "\n")
                trims_before = USAGE["context_trims"]
            messages.append({"role": "assistant", "content": reply})

            try:
                action = _parse(reply)
                image_paths = _image_paths(action.get("images", []))
                if image_paths and not action.get("command"):
                    raise ValueError("images can only be attached to a shell command")
            except Exception as exc:
                trace.write(json.dumps({"step": step, "parse_error": str(exc),
                                        "parse_retry": parse_retries}) + "\n")
                if parse_retries >= PARSE_RETRIES:
                    break
                parse_retries += 1
                # Keep malformed replies in the trace and ask the model for a fresh action.
                messages[-1]["content"] = "[Unparseable reply omitted; see trace.jsonl.]"
                messages.append({"role": "user", "content": (
                    f"Your reply could not be read as an action: {exc}\n"
                    "Nothing was executed. It may have been cut off by the output token limit.\n"
                    "Send one short command; use a loop/script instead of enumerating calls.\n"
                    f"You have {MAX_STEPS - step - 1} step(s) left.\n"
                    "Reply with EXACTLY ONE JSON object and nothing else, one of:\n"
                    '  {"tool": "bash", "command": "<shell command>"}\n'
                    '  {"tool": "bash", "command": "<shell command>", "images": ["view.png"]}\n'
                    '  {"answer": "<your final answer>"}\n'
                    'For structured predictions, put the complete JSON object in "answer"; '
                    'preserve the task\'s prediction schema.')})
                continue

            parse_retries = 0
            # Keep the parsed action in history and the original reply in the trace.
            messages[-1]["content"] = json.dumps(action)

            repaired = getattr(action, "duplicates", ())
            if repaired:
                trace.write(json.dumps({"step": step,
                                        "duplicate_keys": list(repaired)}) + "\n")

            # Execute a command first when a reply also contains a final answer.
            cmd = action.get("command", "")
            if not cmd and "answer" in action:
                missing = _missing_deliverables(task, workdir)
                if missing and submits < SUBMIT_RETRIES:
                    submits += 1
                    note = ("You answered but these paths the task asks for do not exist: "
                            + ", ".join(missing)
                            + ". Create them first, then answer again.")
                    trace.write(json.dumps({"step": step, "submit_rejected": missing}) + "\n")
                    messages.append({"role": "user", "content": note})
                    continue
                trace.write(json.dumps({"step": step, "submitted": True,
                                        "missing": missing}) + "\n")
                answer = _answer_text(action["answer"])
                break
            t0 = time.time()
            proc = subprocess.run(
                # Decode arbitrary shell output without aborting the task.
                [shell, "-c", cmd], cwd=workdir, capture_output=True, text=True,
                errors="replace"
            )
            took = time.time() - t0
            command_s += took
            out = proc.stdout + proc.stderr
            labels, environment_step = {}, None
            if proc.returncode == 0 and ("images" not in action or capture_visual):
                labels, environment_step = _result_observation(proc.stdout)
            if proc.returncode == 0 and "images" not in action:
                image_paths = list(labels)
            image_parts, image_records = _image_attachments(workdir, image_paths, proc.returncode)
            command_trace = {"step": step, "command": cmd, "exit": proc.returncode,
                             "shell": shell, "command_ms": int(took * 1000), "output": out}
            if image_records:
                command_trace["image_attachments"] = image_records
            trace.write(json.dumps(command_trace) + "\n")
            shown = _clip_output(out)
            output_note = ""
            if shown != out:
                if output_dir is None:
                    import tempfile
                    output_dir = Path(tempfile.mkdtemp(prefix=".hg-output-", dir=workdir))
                saved = output_dir / f"step-{step}.txt"
                saved.write_text(out)
                relative = saved.relative_to(workdir)
                output_note = (f"\nFull output: {relative} (use shell tools to inspect/filter it).\n"
                               "The view below may omit records; omitted does not mean absent.\n")
                trace.write(json.dumps({"step": step, "output_clipped": len(out),
                                        "shown": len(shown), "output_file": str(relative),
                                        "output_view": shown}) + "\n")
            text = f"exit={proc.returncode}{output_note}\n{shown}"
            attached = 0
            for row in image_records:
                if "error" in row:
                    text += f"\nCould not attach {row['path']}: {row['error']}"
                else:
                    attached += 1
                    text += f"\nImage {attached}: {row['path']}"
            observation = {"role": "user", "content": text}
            messages.append(observation)

            command_result = (cmd, proc.returncode, out, image_records)
            if cmd and command_result == last_command_result and repeats < REPEAT_NUDGE_LIMIT:
                repeats += 1
                trace.write(json.dumps({"step": step, "repeat_nudge": cmd[:120]}) + "\n")
                messages[-1]["content"] += "\n" + (
                    "That is the same command as the previous step and its output has not "
                    "changed. Do something different, or finish now with "
                    '{"answer": "<your final answer>"} if the work is already done.')
            last_command_result = command_result

            left = MAX_STEPS - (step + 1)
            # Prompt for completion at the threshold and before the last turn.
            if left and (left == FINISH_NUDGE_STEPS or left == 1):
                trace.write(json.dumps({"step": step, "finish_nudge": left}) + "\n")
                messages[-1]["content"] += "\n" + (
                    f"You have {left} step(s) left. Stop exploring. Create every file the "
                    "task asks for now, then reply with exactly one JSON object: "
                    '{"answer": "<your final answer>"}')

            if image_parts:
                if latest_image_message is not None:
                    latest_image_message["content"] = latest_image_message["content"][0]["text"]
                observation["content"] = [
                    {"type": "text", "text": observation["content"]}, *image_parts]
                latest_image_message = observation
                if capture_visual:
                    latest_image_source = {
                        "tool_step": step,
                        "images": [{"sha256": row["sha256"], "label": labels.get(Path(row["path"]))}
                                   for row in image_records if "sha256" in row],
                    }
                    if environment_step is not None and all(path in labels for path in image_paths):
                        latest_image_source["environment_step"] = environment_step

        trace.write(json.dumps({"wall_clock_s": {
            "model": round(model_s, 1), "commands": round(command_s, 1),
            "total": round(time.time() - started, 1)}}) + "\n")

        # Preserve a nonempty answer.txt that changed during the task.
        current = _answer_stamp(workdir)
        if current is not None and current != before and current.strip():
            answer = current.decode("utf-8", "replace")
            answer_source = "artifact"
        else:
            answer_source = "answer_action" if answer else "empty"
        trace.write(json.dumps({"answer_source": answer_source}) + "\n")

        trace.write(json.dumps({"usage": dict(USAGE)}) + "\n")

    if answer_source != "artifact":
        (workdir / "answer.txt").write_text(answer)
    return answer


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--workdir", required=True)
    args = ap.parse_args()

    task = json.loads(Path(args.task).read_text())
    run(task, Path(args.workdir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
