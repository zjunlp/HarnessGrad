"""The tool route: launch-time refusals, the mount plan, and the end-to-end proof.

The proof is the whole point of the capability, so it is a real bwrap run and not a
model of one: a sandboxed process imports the published transport, calls the tool
through the published socket, and receives host-side state it could not have read --
while the same process is shown **unable** to read the file that state came from
(absent, because bwrap starts from an empty root and the relay's directory is not
mounted), unable to rewrite or replace the two published files, and running in a task
that declared no network policy at all.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.containers import model_route, sandbox, tool_route
from eval.service import tool_transport
from eval.service.modelgate import GatewayUnavailable


@pytest.fixture
def relay(tmp_path_factory):
    """A live relay whose handler answers from a host-side file."""
    directory = tmp_path_factory.mktemp("tool-route")
    state = directory / "environment.json"  # beside the socket, never mounted
    state.write_text(json.dumps({"position": "kitchen", "marker": "HOST-ONLY-STATE"}))

    def handler(op, args):
        if op != "observe":
            raise KeyError(f"unknown tool {op!r}")
        return {"position": json.loads(state.read_text())["position"], "args": args}

    with tool_transport.Relay(directory / "tool.sock", handler) as live:
        live.state = state
        yield live
    assert not (directory / "tool.sock").exists()


def layout(tmp_path):
    work = tmp_path / "work"
    task = tmp_path / "task"
    work.mkdir()
    task.mkdir()
    return dict(platform=ROOT, work_root=work, repo=work, task_dir=task)


def test_the_mount_plan_publishes_only_the_socket_and_the_transport_module(tmp_path, relay):
    assert tool_route.mounts(relay.path, exposed_paths=(), writable_paths=()) == [
        "--tmpfs", "/run/harnessgrad-tool",
        "--ro-bind", str(relay.path), "/run/harnessgrad-tool/tool.sock",
        "--ro-bind", str(tool_route.TRANSPORT_SOURCE),
        "/run/harnessgrad-tool/tool_transport.py",
        "--remount-ro", "/run/harnessgrad-tool"]
    # One fixed destination per route, and the tool's is not the model's.
    assert tool_route.ROOT == Path("/run/harnessgrad-tool")
    assert tool_route.ROOT != model_route.ROOT
    # Applied after every task bind and hide, so no dataset policy can shadow it.
    argv = sandbox.command(["true"], **layout(tmp_path), env={}, tool_socket=relay.path)
    bind = argv.index("--bind")
    assert argv.index(str(tool_route.SOCKET)) > bind
    assert argv.index(str(tool_route.SOCKET)) > argv.index("--tmpfs", bind)


@pytest.mark.parametrize("network", [None, "none"])
def test_the_tool_route_does_not_require_a_network_policy(relay, network):
    """`{"kind": "files"}` datasets may leave `network` unset; a socket needs no route.

    The model route still refuses it -- that policy is load-bearing there, because its
    transport is a loopback HTTP forwarder. Here it would refuse the datasets the
    capability exists for, so the rule is switched off and the reason is written down
    instead of implied.
    """
    assert tool_route.mounts(relay.path, network=network, exposed_paths=(),
                             writable_paths=())
    if network is None:
        with pytest.raises(GatewayUnavailable, match="network none"):
            model_route.mounts(relay.path, network=network, exposed_paths=(),
                               writable_paths=())
        # A policy the sandbox itself would not accept is still refused there, for the
        # task's own reasons -- and not silently read as "none".
        with pytest.raises(ValueError, match="unsupported native sandbox network policy"):
            sandbox.command(["true"], platform=ROOT, work_root=relay.path.parent,
                            repo=relay.path.parent, task_dir=relay.path.parent,
                            env={}, network="bridge", tool_socket=relay.path)


def test_relative_symlink_and_non_socket_sources_are_refused(tmp_path, relay):
    ordinary = tmp_path / "ordinary"
    ordinary.write_text("not a socket")
    alias = tmp_path / "alias"
    alias.symlink_to(relay.path)
    parent_alias = tmp_path / "parent-alias"
    parent_alias.symlink_to(relay.path.parent)
    # The relative refusal speaks in the tool route's own words, and does not mention a
    # network policy it never required.
    with pytest.raises(GatewayUnavailable,
                       match="tool socket requires an absolute non-symlink source"):
        tool_route.mounts(Path("relative.sock"), exposed_paths=(), writable_paths=())
    for source in (ordinary, alias, parent_alias / "tool.sock"):
        with pytest.raises(GatewayUnavailable, match="UNIX socket|non-symlink"):
            tool_route.mounts(source, exposed_paths=(), writable_paths=())


def test_a_missing_socket_is_refused_at_launch(tmp_path):
    # Not a silent skip: a source that is not there would mount as nothing at all.
    with pytest.raises(OSError):
        tool_route.mounts(tmp_path / "absent.sock", exposed_paths=(), writable_paths=())


@pytest.mark.parametrize("target", ["/run", "root", "child"])
def test_a_target_overlapping_the_tool_root_is_refused(target, relay):
    path = {"root": tool_route.ROOT, "child": tool_route.ROOT / "tool.sock"}.get(
        target, Path(target))
    with pytest.raises(GatewayUnavailable, match="overlaps"):
        tool_route.mounts(relay.path, exposed_paths=(path,), writable_paths=())


@pytest.mark.parametrize("writable", ["directory", "ancestor"])
def test_a_socket_reachable_through_writable_storage_is_refused(writable, relay):
    path = relay.path.parent if writable == "directory" else Path("/")
    with pytest.raises(GatewayUnavailable, match="writable"):
        tool_route.mounts(relay.path, exposed_paths=(), writable_paths=(path,))


def test_launch_revalidates_a_socket_replaced_after_construction(tmp_path, relay):
    relay.path.unlink()
    relay.path.write_text("replaced")
    with pytest.raises(GatewayUnavailable, match="UNIX socket"):
        sandbox.command(["true"], **layout(tmp_path), env={}, tool_socket=relay.path)


def test_the_published_transport_module_must_be_a_real_file(tmp_path, relay, monkeypatch):
    fake = tmp_path / "tool_transport.py"
    fake.symlink_to(ROOT / "eval/service/tool_transport.py")
    monkeypatch.setattr(tool_route, "TRANSPORT_SOURCE", fake)
    with pytest.raises(GatewayUnavailable, match="trusted tool transport"):
        tool_route.mounts(relay.path, exposed_paths=(), writable_paths=())
    monkeypatch.setattr(tool_route, "TRANSPORT_SOURCE", tmp_path / "absent.py")
    with pytest.raises(GatewayUnavailable, match="trusted tool transport"):
        tool_route.mounts(relay.path, exposed_paths=(), writable_paths=())


def test_the_published_transport_is_standalone(relay):
    """It has to run inside the sandbox, where the platform is not on the disk."""
    source = Path(tool_route.TRANSPORT_SOURCE).read_text()
    assert compile(source, "tool_transport.py", "exec")
    for forbidden in ("from eval", "import eval", "from harnessgrad", "import harnessgrad"):
        assert forbidden not in source
    assert tool_route.SOCKET == Path("/run/harnessgrad-tool/tool.sock")
    assert relay.receipt["calls"] == 0  # nothing above touched the relay


def test_model_route_plan_and_refusals_are_unchanged_by_the_shared_rules(relay):
    """The shared validator must leave `model_route` behaviour bit-identical."""
    forwarder = ROOT / "eval/service/native_model_forwarder.py"
    assert model_route.mounts(relay.path, network="none", exposed_paths=(),
                              writable_paths=()) == [
        "--ro-bind", str(relay.path), "/run/harnessgrad-model/model.sock",
        "--ro-bind", str(forwarder), "/run/harnessgrad-model/forwarder.py"]
    refused = {}
    cases = {
        "network": (relay.path, dict(network=None, exposed_paths=(), writable_paths=())),
        "relative": (Path("relative.sock"),
                     dict(network="none", exposed_paths=(), writable_paths=())),
        "exposed": (relay.path,
                    dict(network="none", exposed_paths=(model_route.ROOT / "child",),
                         writable_paths=())),
        "writable": (relay.path,
                     dict(network="none", exposed_paths=(),
                          writable_paths=(relay.path.parent,))),
    }
    for name, (target, kwargs) in cases.items():
        with pytest.raises(GatewayUnavailable) as raised:
            model_route.mounts(target, **kwargs)
        refused[name] = str(raised.value)
    assert refused == {
        "network": "model socket requires network none and an absolute non-symlink source",
        "relative": "model socket requires network none and an absolute non-symlink source",
        "exposed": "model route target overlaps another mount",
        "writable": "model socket source is reachable through writable storage",
    }


def probe(tmp_path, relay, state):
    """Run in a real bwrap namespace: call the tool, then try to read its state."""
    code = '''import json, pathlib, sys
sys.path.insert(0, %r)
import tool_transport
result = tool_transport.call(%r, "observe", {"target": "drawer"})
state = pathlib.Path(%r)
try:
    text = state.read_text()
except OSError as exc:
    read = type(exc).__name__
else:
    read = "LEAKED:" + text
infra = pathlib.Path(%r)
attempts = {}
for label, action in (("tamper", lambda: (infra / "tool_transport.py").write_text("x")),
                      ("unlink", lambda: (infra / "tool.sock").unlink()),
                      ("add", lambda: (infra / "extra.sock").write_text("x"))):
    try:
        action()
    except OSError as exc:
        attempts[label] = type(exc).__name__
    else:
        attempts[label] = "allowed"
print(json.dumps({
    "result": result,
    "read": read,
    "exists": state.exists(),
    "parent_exists": state.parent.exists(),
    "attempts": attempts,
    "exposed": sorted(entry.name for entry in infra.iterdir()),
}))
''' % (str(tool_route.ROOT), str(tool_route.SOCKET), str(state), str(tool_route.ROOT))
    # `network` deliberately unset: the datasets this serves declare {"kind": "files"}.
    plan = sandbox.command(["/usr/bin/python3", "-c", code], **layout(tmp_path),
                           env={}, tool_socket=relay.path)
    assert "--unshare-net" not in plan
    return subprocess.run(plan, capture_output=True, text=True, timeout=60, env={})


def test_sandboxed_process_calls_the_tool_and_cannot_read_the_served_state(tmp_path, relay):
    state = relay.state
    # Control: the file exists and is readable on the host, so a failure inside the
    # namespace is the namespace's doing and not a badly spelled path.
    assert json.loads(state.read_text())["marker"] == "HOST-ONLY-STATE"

    proc = probe(tmp_path, relay, state)
    assert proc.returncode == 0, proc.stderr
    seen = json.loads(proc.stdout.strip().splitlines()[-1])

    # The call really happened, over the published socket, against host-side state.
    assert seen["result"] == {"position": "kitchen", "args": {"target": "drawer"}}
    assert relay.receipt["calls"] == 1

    # And the file the answer came from is absent, for the right reason: bwrap starts
    # from an empty root, so it is not there at all -- not "permission denied", and
    # certainly not an empty tool result.
    assert seen["read"] == "FileNotFoundError"
    assert seen["exists"] is False and seen["parent_exists"] is False

    # Exactly the two infrastructure paths were published, and the directory is frozen:
    # the subject cannot rewrite the transport, unlink the socket, or add a third entry.
    assert seen["exposed"] == ["tool.sock", "tool_transport.py"]
    assert seen["attempts"] == {"tamper": "OSError", "unlink": "OSError", "add": "OSError"}

    # The host file is untouched, and the state never landed in the task directory.
    assert json.loads(state.read_text())["marker"] == "HOST-ONLY-STATE"
    assert list((tmp_path / "task").iterdir()) == []


def test_sandboxed_client_reports_an_absent_socket_as_a_platform_failure(tmp_path, relay):
    """The transport is silent about nothing: not its own absence, not the socket's."""
    paths = layout(tmp_path)
    code = ('import sys\nsys.path.insert(0, %r)\nimport tool_transport\n'
            'try:\n    print("returned", repr(tool_transport.call(%r, "observe")))\n'
            'except tool_transport.ToolTransportError as exc:\n'
            '    print("refused", exc)\n')
    module = str(tool_route.ROOT)

    # (a) Nothing published: the module and its socket are published together, so the
    # agent cannot even import it -- a mount failure Python reports, not empty data.
    proc = subprocess.run(
        sandbox.command(["/usr/bin/python3", "-c", code % (module, str(tool_route.SOCKET))],
                        **paths, env={}, tool_socket=None),
        capture_output=True, text=True, timeout=60, env={})
    assert proc.returncode != 0
    assert "ModuleNotFoundError: No module named 'tool_transport'" in proc.stderr

    # (b) The module is published but the socket asked for does not exist: the call
    # raises, so a CLI cannot read it as "the environment had nothing to say".
    proc = subprocess.run(
        sandbox.command(["/usr/bin/python3", "-c",
                         code % (module, "/run/harnessgrad-tool/absent.sock")],
                        **paths, env={}, tool_socket=relay.path),
        capture_output=True, text=True, timeout=60, env={})
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("refused tool relay socket is absent")
    assert "returned" not in proc.stdout
    assert relay.receipt["calls"] == 0


def test_a_tool_socket_is_purely_additive_to_the_mount_plan(tmp_path, relay):
    """A task without a tool socket gets the plan it gets today, and one with it differs
    by exactly the tool mounts -- no wrapper in argv, no hidden tree, no env change.

    Checked against the pre-change plan itself, byte for byte: `command()` with
    `tool_socket=None` never enters the new block, which a comparison against `main`'s
    `sandbox.py` confirmed for a plain task and for a model-socket task (93 plan tokens
    each, identical).
    """
    paths = layout(tmp_path)
    plain = sandbox.command(["true"], **paths, env={})
    withtool = sandbox.command(["true"], **paths, env={}, tool_socket=relay.path)
    cut = plain.index("--")  # bwrap's argv separator: everything after it is the task
    extra = tool_route.mounts(relay.path, exposed_paths=(), writable_paths=())
    assert withtool == plain[:cut] + extra + plain[cut:]
    assert withtool[cut + len(extra):] == ["--", "true"]
