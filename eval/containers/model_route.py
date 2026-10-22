"""Validated infrastructure mounts for the native offline model route.

The alias/replacement/writable-storage rules are shared with the tool route
(`eval/containers/unix_socket_route.py`) rather than restated here: two copies of a
security check drift, and the one that has drifted is the one nobody re-reads. What
stays local is what is actually specific to the model: the fixed `ROOT`, the trusted
forwarder, and the bootstrap that hands the client its loopback URL.
"""
from pathlib import Path

from eval.containers import unix_socket_route
from eval.service.modelgate import GatewayUnavailable

ROOT = Path("/run/harnessgrad-model")
FORWARDER = Path(__file__).resolve().parents[1] / "service/native_model_forwarder.py"


def mounts(socket_path: Path, *, network, exposed_paths, writable_paths):
    """Revalidate at launch; never bind a relay directory or accept a task target."""
    source = Path(socket_path)
    unix_socket_route.verify(source, root=ROOT, label="model", network=network,
                             exposed_paths=exposed_paths,
                             writable_paths=writable_paths)
    if FORWARDER.is_symlink() or not FORWARDER.is_file():
        raise GatewayUnavailable("trusted native model forwarder is unavailable")
    return ["--ro-bind", str(source), str(ROOT / "model.sock"),
            "--ro-bind", str(FORWARDER), str(ROOT / "forwarder.py")]


def bootstrap(argv, path):
    return ["/usr/bin/python3", "-I", "-S", str(ROOT / "forwarder.py"),
            str(ROOT / "model.sock"), path, "--", *argv]
