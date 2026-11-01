"""Offline adapter contract tests; no paid API or benchmark requests."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / "base_harness/opencode/agent.py"


def event(kind, message="m1", **part):
    return {"type": kind, "sessionID": "s1",
            "part": {"messageID": message, "id": kind + message, **part}}


class AdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if ENTRY.exists():
            spec = importlib.util.spec_from_file_location("opencode_adapter", ENTRY)
            cls.adapter = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.adapter)

    def setUp(self):
        self.assertTrue(ENTRY.exists(), "OpenCode project adapter is not implemented")

    def collect(self, events):
        records = []
        stream = self.adapter.EventCollector(records.append)
        for value in events:
            stream.feed(json.dumps(value))
        return stream, records

    def test_final_text_excludes_earlier_progress(self):
        stream, _ = self.collect([
            event("step_start"),
            event("text", text="I will inspect the files."),
            event("step_finish", reason="tool-calls", tokens={"input": 10, "output": 3}),
            event("step_start", "m2"),
            event("text", "m2", text="alpha"),
            event("step_finish", "m2", reason="stop", tokens={"input": 20, "output": 4}),
        ])
        self.assertEqual(stream.final_text(), "alpha")
        self.assertEqual(stream.usage()["input_tokens"], 30)
        self.assertEqual(stream.usage()["output_tokens"], 7)

    def test_duplicate_finish_does_not_double_count_usage(self):
        finish = event("step_finish", reason="stop",
                       tokens={"input": 8, "output": 2, "reasoning": 1,
                               "cache": {"read": 5, "write": 0}})
        stream, _ = self.collect([event("text", text="ok"), finish, finish])
        self.assertEqual(stream.usage()["input_tokens"], 13)
        self.assertEqual(stream.usage()["output_tokens"], 3)
        self.assertEqual(stream.usage()["cached_input_tokens"], 5)

    def test_tool_failure_preserves_call_arguments_and_output(self):
        stream, records = self.collect([
            event("step_start"),
            event("tool_use", tool="bash", state={"status": "error",
                  "input": {"command": "exit 7"}, "error": "Command exited 7"}),
        ])
        command = next(r for r in records if "command" in r)
        self.assertEqual(command["command"], "exit 7")
        self.assertIn("Command exited 7", command["output"])
        self.assertEqual(command["tool_status"], "error")
        self.assertFalse(stream.provider_errors)

    def test_provider_failure_cannot_turn_partial_text_into_success(self):
        stream, _ = self.collect([
            event("text", text="working"),
            {"type": "error", "error": {"type": "provider.internal", "status": 503,
                                       "message": "unavailable"}},
        ])
        self.assertEqual(stream.final_text(), "")
        self.assertTrue(stream.provider_errors)
        self.assertEqual(stream.usage()["input_tokens"], None)

    def test_length_finish_is_not_a_completed_answer(self):
        stream, _ = self.collect([event("text", text="partial"),
                                  event("step_finish", reason="length")])
        self.assertEqual(stream.final_text(), "")

    def snapshot(self, finish="stop", outcome="succeeded"):
        return {"info": {"id": "s1", "outcome": outcome,
                         "tokens": {"input": 80, "output": 10, "reasoning": 0,
                                    "cache": {"read": 0, "write": 0}}},
                "messages": [{"id": "m1", "type": "assistant", "finish": finish,
                              "content": [{"type": "text", "text": "alpha"}],
                              "tokens": {"input": 40, "output": 5, "reasoning": 0,
                                         "cache": {"read": 0, "write": 0}}}]}

    def test_session_export_recovers_missing_finish_and_auxiliary_usage(self):
        stream, records = self.collect([event("text", text="alpha")])
        stream.reconcile(self.snapshot())
        self.assertEqual(stream.final_text(), "alpha")
        self.assertEqual(stream.usage()["input_tokens"], 80)
        self.assertEqual(stream.usage()["task_steps"]["input_tokens"], 40)
        self.assertEqual(len([r for r in records if "reply" in r]), 1)

    def test_export_does_not_duplicate_stream_usage_or_text(self):
        stream, _ = self.collect([event("text", text="alpha"),
                                  event("step_finish", reason="stop",
                                        tokens={"input": 40, "output": 5})])
        stream.reconcile(self.snapshot())
        self.assertEqual(stream.final_text(), "alpha")
        self.assertEqual(stream.usage()["task_steps"]["input_tokens"], 40)
        self.assertEqual(stream.usage()["completed_steps"], 1)

    def test_rejects_wrong_session_failed_or_truncated_export(self):
        for variation in ("session", "outcome", "finish"):
            with self.subTest(variation=variation):
                stream, _ = self.collect([event("text", text="alpha")])
                snapshot = self.snapshot()
                if variation == "session":
                    snapshot["info"]["id"] = "other-session"
                elif variation == "outcome":
                    snapshot["info"]["outcome"] = "failed"
                else:
                    snapshot["messages"][-1]["finish"] = "length"
                with self.assertRaises(ValueError):
                    stream.reconcile(snapshot)
                self.assertEqual(stream.final_text(), "")

    def test_tool_written_answer_and_prediction_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            before = self.adapter.answer_stamp(work)
            raw = '{"answer":"Paris","supporting_facts":[["France",0]]}\n'
            (work / "answer.txt").write_bytes(raw.encode("utf-8"))
            (work / "prediction.json").write_text('{"answer":"Paris"}', encoding="utf-8")
            answer, origin = self.adapter.save_answer(work, before, "Task complete.")
            self.assertEqual(answer, raw)
            self.assertEqual(origin, "artifact")
            self.assertEqual((work / "answer.txt").read_text(encoding="utf-8"), raw)
            self.assertEqual((work / "prediction.json").read_text(encoding="utf-8"),
                             '{"answer":"Paris"}')

    def test_plain_text_fallback_and_empty_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            answer, origin = self.adapter.save_answer(work, None, "42")
            self.assertEqual((answer, origin), ("42", "final_message"))
            self.assertEqual((work / "answer.txt").read_text(encoding="utf-8"), "42")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                self.adapter.save_answer(Path(tmp), None, "")

    def test_prompt_preserves_task_instructions_and_structured_inputs(self):
        prompt = self.adapter.task_prompt({
            "goal": 'Use dojo submit; preserve JSON {"calls":[],"output":"x"}.',
            "inputs": {"query": "\u591a\u6b65\u4efb\u52a1", "path": "report.xlsx"},
        })
        self.assertIn('{"calls":[],"output":"x"}', prompt)
        self.assertIn("\u591a\u6b65\u4efb\u52a1", prompt)
        self.assertIn("report.xlsx", prompt)
        self.assertNotIn("briefly describe", prompt)

    def test_source_hash_changes_with_typescript_not_dependency_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.ts").write_text("export const n = 1")
            first = self.adapter.source_hash(root)
            (root / "node_modules").mkdir()
            (root / "node_modules/ignored.js").write_text("different cache")
            self.assertEqual(first, self.adapter.source_hash(root))
            (root / "main.ts").write_text("export const n = 2")
            self.assertNotEqual(first, self.adapter.source_hash(root))

    def test_short_platform_deadline_is_respected(self):
        self.assertLessEqual(self.adapter.timeout_seconds({
            "HG_AGENT_DEADLINE_S": "3", "HG_AGENT_STOP_MARGIN_S": "90"}), 3)

    def test_environment_keeps_dataset_services_and_excludes_other_model_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self.adapter.child_environment({
                "PATH": "bin", "HG_AGENT_MODEL": "fixture-model",
                "HG_AGENT_BASE_URL": "http://127.0.0.1:9999/v1",
                "HG_AGENT_API_KEY": "task-secret", "HG_METHOD_API_KEY": "method-secret",
                "HG_ENV_API_KEY": "env-secret", "OPENAI_API_KEY": "ambient-secret",
                "APPWORLD_BASE_URL": "http://service:8000",
            }, Path(tmp))
            self.assertNotIn("HG_METHOD_API_KEY", env)
            self.assertNotIn("HG_ENV_API_KEY", env)
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertEqual(env["APPWORLD_BASE_URL"], "http://service:8000")
            config = env["OPENCODE_CONFIG_CONTENT"]
            self.assertNotIn("task-secret", config)
            self.assertIn("http://127.0.0.1:9999/v1", config)


class ContractTests(unittest.TestCase):
    """The adapter contract (`docs/adapter-spec.md` section 4), not only the CLI's stream.

    `opencode_event` records are the CLI's JSON verbatim and a method cannot read them: the
    platform's one extractor counts `reply` and `command`. These pin the translation.
    """

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("opencode_adapter_contract", ENTRY)
        cls.adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.adapter)
        import sys
        sys.path.insert(0, str(ROOT))
        from eval.observe.tracing import loop_facts
        cls.loop_facts = staticmethod(loop_facts)

    def collect(self, events):
        records = []
        stream = self.adapter.EventCollector(records.append)
        for value in events:
            stream.feed(json.dumps(value))
        return stream, records

    def test_a_completed_tool_call_becomes_a_command_and_an_observation(self):
        stream, records = self.collect([
            event("tool_use", tool="bash",
                  state={"status": "completed", "input": {"command": "ls -a"},
                         "output": "a b"}),
        ])
        tool = next(r for r in records if r.get("tool") == "bash")
        self.assertEqual(tool["command"], "ls -a")
        self.assertEqual(tool["observation"],
                         {"kind": "shell", "returncode": None,
                          "output_chars": 3, "truncated": False})
        facts = self.loop_facts("\n".join(json.dumps(r) for r in records))
        self.assertEqual((facts["commands"], facts["observations"]), (1, 1))
        # No exit code is available from the CLI, and `None` must not become `0`: every
        # failed command would then look successful to a method reading return codes.
        self.assertEqual(facts["return_codes"], {"None": 1})

    def test_the_observation_names_which_environment_spoke(self):
        for tool, kind in (("bash", "shell"), ("read", "filesystem"), ("edit", "filesystem"),
                           ("webfetch", "api"), ("task", "other")):
            with self.subTest(tool=tool):
                self.assertEqual(self.adapter.observation_kind(tool), kind)

    def test_usage_calls_agrees_with_the_number_of_turns(self):
        """`usage.calls` and the platform's `reply` count are one fact; this keeps them
        from drifting apart."""
        stream, records = self.collect([
            event("step_finish", "m1", reason="stop", tokens={"input": 5, "output": 1}),
            event("step_finish", "m2", reason="stop", tokens={"input": 5, "output": 1}),
        ])
        self.assertEqual(stream.usage()["calls"], 2)
        self.assertEqual(len([r for r in records if "reply" in r]), 2)

    def test_a_failed_tool_call_still_carries_the_observation(self):
        """A tool that errored is the feedback a method learns most from."""
        _, records = self.collect([
            event("tool_use", tool="bash",
                  state={"status": "error", "input": {"command": "exit 7"},
                         "error": "Command exited 7"}),
        ])
        tool = next(r for r in records if r.get("tool") == "bash")
        self.assertEqual(tool["observation"]["output_chars"], len("Command exited 7"))
        self.assertEqual(tool["observation"]["kind"], "shell")


if __name__ == "__main__":
    unittest.main()
