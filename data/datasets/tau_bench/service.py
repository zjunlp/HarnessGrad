"""Task-bound tau service; only this process writes the grading recording.

Launched with ``python3 -c <this source>`` in the existing tau image. No ground
truth, host paths or client-provided code enter this process. Health is unrecorded.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

TERMINATE_TOOLS = ("transfer_to_human_agents",)


class Episode:
    def __init__(self, task_id, domain, database, tools, policy, recording):
        self.task_id, self.domain = task_id, domain
        self.database, self.tools, self.policy = database, tools, policy
        self.events, self.done = [], False
        self.recording = Path(recording)
        self.persist()
        # This service never calls a model. Publish observed zero model usage using
        # the platform's reserved metadata filename (INTERFACE.md section 2.5.7).
        self.recording.with_name("harnessgrad.json").write_text(json.dumps(
            {"usage": {"input_tokens": 0, "output_tokens": 0, "calls": 0}}))

    def persist(self):
        """Publish database + event log as one atomic, service-owned snapshot."""
        self.recording.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.recording.parent, prefix=".episode-")
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump({"version": 1, "task_id": self.task_id,
                           "domain": self.domain, "done": self.done,
                           "database": self.database, "events": self.events}, handle)
            os.replace(temporary, self.recording)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def act(self, request):
        if self.done:
            raise ValueError("episode is already done")
        command = request.get("command")
        if command == "call":
            name, arguments = request.get("name"), request.get("arguments")
            if not isinstance(name, str) or name not in self.tools:
                raise ValueError("unknown tool")
            if not isinstance(arguments, dict):
                raise ValueError("tool arguments must be an object")
            try:
                observation = self.tools[name].invoke(self.database, **arguments)
            except Exception as exc:  # mirrors the previous CLI / upstream Env.step
                observation = f"Error: {exc}"
            event = {"kind": "call", "name": name, "arguments": arguments}
            self.done = name in TERMINATE_TOOLS
        elif command == "say":
            content = request.get("content")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("say requires nonempty content")
            event = {"kind": "say", "name": "respond",
                     "arguments": {"content": content}}
            observation = "Noted. There is no user to reply."
        elif command == "done":
            event = {"kind": "done", "name": "done", "arguments": {}}
            observation, self.done = "Noted. The task is marked done.", True
        else:
            raise ValueError("unknown command")
        self.events.append(event)
        self.persist()  # errors fail the request; no success before evidence is saved
        return observation


def handler_for(episode):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, payload):
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/health":
                self.reply(200, {"status": "ok"})
            elif self.path == "/list":
                self.reply(200, {"output": [tool.get_info()
                                          for tool in episode.tools.values()]})
            elif self.path == "/policy":
                self.reply(200, {"output": episode.policy})
            elif self.path == "/show":
                self.reply(200, {"output": episode.database})
            else:
                self.reply(404, {"error": "unknown endpoint"})

        def do_POST(self):
            if self.path != "/act":
                self.reply(404, {"error": "unknown endpoint"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024 * 1024:
                    raise ValueError("request must be 1..1048576 bytes")
                request = json.loads(self.rfile.read(length))
                if not isinstance(request, dict):
                    raise ValueError("request must be an object")
                output = episode.act(request)
            except (ValueError, TypeError) as exc:
                self.reply(400, {"error": str(exc)})
                return
            self.reply(200, {"output": output})

    return Handler


def main():
    task_id = os.environ["HG_ENV_TASK_ID"]
    # `<domain>_<official split>_<index>`. The three retail task files each index from 0,
    # so the split is part of the id; the domain is the only part this process needs.
    match = re.fullmatch(r"(retail|airline)_(?:train|dev|test)_[0-9]{3}", task_id)
    if match is None:
        raise ValueError("invalid HG_ENV_TASK_ID")
    domain = match[1]
    sys.path.insert(0, "/opt/tau/lib")
    module = importlib.import_module(f"tau_bench.envs.{domain}.tools")
    tools = {tool.get_info()["function"]["name"]: tool for tool in module.ALL_TOOLS}
    episode = Episode(task_id, domain,
                      json.loads(Path(f"/opt/tau/pristine/{domain}.json").read_text()),
                      tools, Path(f"/opt/tau/policy/{domain}.txt").read_text(),
                      "/recordings/tau/episode.json")
    # Single-threaded: concurrent HTTP requests cannot reorder database/log commits.
    HTTPServer(("0.0.0.0", 8080), handler_for(episode)).serve_forever()


if __name__ == "__main__":
    main()
