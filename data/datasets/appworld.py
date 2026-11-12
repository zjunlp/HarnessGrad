"""AppWorld 0.1.3 as a service-backed tool dialogue dataset.

The encrypted 0.1.0 release declares four files: ``train`` (90), ``dev`` (57),
``test_normal`` (168), and ``test_challenge`` (417), 732 tasks in total.  The adapter
uses that declaration (``SPLIT_SOURCE = "declared"``) and can express the three-way
protocol every dataset is measured on:

  * **study** on the official ``train`` split (90 tasks) -- always, and only.  ``HG_APPWORLD_STUDY_IDS`` names a file of
    task ids, and the study side becomes exactly those ids, in file order.
  * **score every checkpoint** on the official ``dev`` split.  ``HG_APPWORLD_POOL=dev``
    makes dev the eval side, so ``--side eval --from-run`` measures a stored harness state
    on dev without touching the exam.
  * **one final exam** on the official test splits.  ``HG_APPWORLD_POOL=test`` (the
    default) is ``test_normal`` plus ``test_challenge``: 585 tasks that the study side
    never contains.

``HG_APPWORLD_POOL`` also accepts one test file alone.  It refuses ``train``, because a
score computed on the training split measures the material the method was shown.  With
neither knob set: **study = the official ``train`` split (90), exam = both test files (585)**.
``HG_APPWORLD_POOL=dev`` scores the official ``dev`` split (57) instead -- that is the
checkpoint-selection measurement, and dev is never study material, because a split cannot
be both the material a method learns from and the one its checkpoints are chosen on.

The paper-era build (Trivedi et al., ACL 2024, arXiv 2407.18901v2) reported
105/60/168/417 tasks, totaling 750. The pinned release has 18 fewer train and dev tasks
(105 against 90, 60 against 57); both test split counts match the paper.

AppWorld's environment server is a sidecar service.  The task container only gets the
small ``aw`` client; the service owns the world and writes its API-call log through the
platform recording mount.  A fresh verifier reads that log and replays every request in
a new AppWorld world before calling the released ``ground_truth/evaluation.py``.  A
process started by the agent would not survive the platform's fresh verification
container, which is why the server is declared in ``services``.

The release bundle contains generated solution artifacts for train and dev tasks. Their
released API-call logs are used as the oracle only for those covered tasks; uncovered
test tasks raise from ``oracle_files`` rather than receiving an empty reference.

That sidecar also makes this dataset the one whose size decides how many Docker networks a
pilot holds at once: every in-flight AppWorld task owns an ``--internal`` network from
start to stop.  Before the platform allocated those subnets itself, two 16-job runs
exhausted Docker's default address pool and recorded ``all predefined address pools have
been fully subnetted`` as 77 invalid tasks.  The platform now carves ``/24``s from
``HG_NET_SUBNET_BASE`` (default ``10.42.0.0/16``), so a 90-task pilot at ``--jobs 16`` needs
16 of 256; ``python3 tools/network_preflight.py --networks 40 --concurrency 16`` is the
pre-pilot check and ``docs/network-subnets.md`` has the measurement.
"""
from __future__ import annotations

import hashlib
import ctypes
import errno
import stat
import tempfile
import io
import json
import os
import shlex
import secrets
import subprocess
import shutil
import zipfile
from pathlib import Path

from eval.policy.appworld_reporting import make_manifest

from harnessgrad.paths import dataset_root

KIND = "dataset"
VERIFIER_KIND = 'command'

DATASET_ROOT = dataset_root('appworld')
SRC_DIR = DATASET_ROOT / "_src"
BUNDLE = SRC_DIR / "data-0.1.0.bundle"
UNPACKED = SRC_DIR / "unpacked"
POOL = DATASET_ROOT / "pool.jsonl"
TASKS_IN_RELEASE = 732
SPLIT_SIZES = {"train": 90, "dev": 57, "test_normal": 168, "test_challenge": 417}
TEST_SPLITS = ("test_normal", "test_challenge")
PAPER_SPLITS = {"train": 105, "dev": 60, "test_normal": 168, "test_challenge": 417}
SPLIT_SOURCE = "declared"

#: **Which official split the eval side measures.** `test` is the union of the two test
#: files and is the final exam; `dev` scores every checkpoint; `test_normal` and
#: `test_challenge` score one test file each. `train` is absent on purpose: it is study
#: material, and a score on it measures what the method was shown.
EVAL_POOLS = ("dev", "test_normal", "test_challenge", "test")
_EVAL_POOL = (os.environ.get("HG_APPWORLD_POOL") or "test").strip()

#: **A study set, named by a file of task ids.** When it is set the study side is exactly
#: those ids in file order, rather than the official train split. The ids are read
#: from a file rather than sampled here because a draw made at import is a draw nobody can
#: reproduce; the file can be one id per line or a comma-separated line, as the release's
#: own split files are newline-separated and ALFWorld's study file is a CSV.
_STUDY_FILE = (os.environ.get("HG_APPWORLD_STUDY_IDS") or "").strip()

ORACLE_SOURCE = "released"
ORACLE_VALIDATED_BY = (
    "AppWorld's released ground_truth/evaluation.py, executed by appworld.evaluator.TestTracker"

)
IMAGE = os.environ.get(
    "HG_APPWORLD_IMAGE",
    "harnessgrad/appworld-agent@sha256:d3a04ec8e295710347e978d8e58c663161d829e7dfa544da4d82d0d67abbd8eb",
)
SERVICE_IMAGE = os.environ.get(
    "HG_APPWORLD_SERVICE_IMAGE",
    "harnessgrad/appworld-service@sha256:db09e34ec90347e7d5271d95445f360222ba1aef3ca0eaa5614ce768bf23aca4",
)
VERIFIER_IMAGE = os.environ.get(
    "HG_APPWORLD_VERIFIER_IMAGE",
    "harnessgrad/appworld-service@sha256:f3cb916b9bdc297d4c73dd9646e9550befb7c6e23827d937b90a0640373e97ca",
)
BUNDLE_SHA256 = "fd9f9608c2ec71ed0ac25c3633a738b9129a318a129e31230425b9188e508250"
POOL_SHA256 = "970c39331bc350ce9d3ed91a75fd32b554c257fc48fc31633dfccdde74164e65"
WORKDIR = "/app"
PASSWORD = "WEquKLy##9M@qu"
SALT = b"Nvx#rYcYQ2%btf"


def _decrypt(data: bytes, runtime: dict) -> bytes:
    # Re-admit the current publication before each use; never install implicitly.
    from harnessgrad.dataset_runtime import resolve_execution
    admitted = resolve_execution("appworld")
    if (admitted["runtime_id"], admitted["manifest_path"]) != (runtime["runtime_id"], runtime["manifest_path"]):
        raise ValueError("appworld preparation runtime changed during selection")
    worker = Path(__file__).resolve().parent / "appworld" / "decrypt_bundle.py"
    completed = subprocess.run([admitted["python"], "-I", "-B", str(worker)], input=data,
                               capture_output=True, timeout=120, check=False,
                               env={"PATH": os.defpath, "PYTHONNOUSERSITE": "1"})
    if completed.returncode:
        raise ValueError("appworld decryption worker failed in the prepared runtime")
    return completed.stdout


def bundle_zip(bundle_path: Path | str | None = None, *, _runtime: dict | None = None) -> zipfile.ZipFile:
    """Decrypt in the explicitly prepared external runtime, never in platform Python."""
    from harnessgrad.dataset_runtime import resolve_execution
    runtime = _runtime if _runtime is not None else resolve_execution("appworld")
    path = Path(bundle_path) if bundle_path else BUNDLE
    return zipfile.ZipFile(io.BytesIO(_decrypt(path.read_bytes(), runtime)))


def task_ids(bundle_path: Path | str | None = None) -> list[str]:
    with bundle_zip(bundle_path) as archive:
        prefix, suffix = "data/tasks/", "/ground_truth/evaluation.py"
        return sorted(
            {
                name[len(prefix) : -len(suffix)]
                for name in archive.namelist()
                if name.startswith(prefix) and name.endswith(suffix)
            }
        )


def split_ids(bundle_path: Path | str | None = None) -> dict[str, list[str]]:
    with bundle_zip(bundle_path) as archive:
        return {
            split: [line.strip() for line in archive.read(f"data/datasets/{split}.txt").decode().splitlines() if line.strip()]
            for split in SPLIT_SIZES
        }


def _db_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((root / "dbs").glob("*.jsonl")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _archive_files(archive):
    """Validate every entry before extraction or comparison."""
    files = {}
    for member in archive.infolist():
        relative = Path(member.filename)
        if (relative.is_absolute() or not relative.parts or ".." in relative.parts
                or "\\" in member.filename or member.filename in files):
            raise ValueError("appworld archive contains an unsafe or duplicate path")
        mode = member.external_attr >> 16
        if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISDIR(mode) or stat.S_ISREG(mode))):
            raise ValueError("appworld archive contains an unsupported entry")
        files[member.filename] = member
    return files


def _verify_unpacked(archive, members, root):
    if root.is_symlink() or not root.is_dir():
        raise ValueError("appworld: unpacked tree is not a real directory")
    for name, member in members.items():
        target = root
        for part in Path(name).parts:
            target = target / part
            if target.is_symlink():
                raise ValueError("appworld: unpacked tree contains a symlink in an official path")
        if member.is_dir():
            if not target.is_dir():
                raise ValueError("appworld: unpacked tree missing official directory")
        elif not target.is_file() or target.read_bytes() != archive.read(member):
            raise ValueError("appworld: unpacked tree missing or changed official file")


def _publish_unpacked(stage, target):
    # Linux renameat2 prevents replacing even an empty directory created concurrently.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise ValueError("appworld: atomic no-replace publication is unavailable")
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    if rename(-100, os.fsencode(stage), -100, os.fsencode(target), 1) != 0:
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            raise ValueError("appworld: unpacked tree already exists; preserve it")
        raise OSError(code, os.strerror(code))


def prepare() -> Path:
    """Materialize pinned metadata using an admitted, isolated decrypt runtime.

    Existing official files must match the pinned bundle. Extra user files are retained.
    """
    from harnessgrad.dataset_runtime import resolve_execution
    runtime = resolve_execution("appworld")
    worker = Path(__file__).resolve().parent / "appworld" / "decrypt_bundle.py"
    worker_sha = hashlib.sha256(worker.read_bytes()).hexdigest()
    raw = BUNDLE.read_bytes()
    bundle_sha = hashlib.sha256(raw).hexdigest()
    if bundle_sha != BUNDLE_SHA256:
        raise ValueError(f"appworld: bundle digest {bundle_sha} != pinned {BUNDLE_SHA256}")
    # Use the verified bytes, not a second read from a potentially changed source.
    decrypted = _decrypt(raw, runtime)
    if hashlib.sha256(worker.read_bytes()).hexdigest() != worker_sha:
        raise ValueError("appworld preparation worker changed during decryption")
    with zipfile.ZipFile(io.BytesIO(decrypted)) as archive:
        entries = _archive_files(archive)
        members = set(entries)
        declared = {split: [line.strip() for line in archive.read(
            f"data/datasets/{split}.txt").decode().splitlines() if line.strip()]
            for split in SPLIT_SIZES}
        rows = []
        for split, ids in declared.items():
            for task in ids:
                prefix = f"data/tasks/{task}/"
                specs = json.loads(archive.read(prefix + "specs.json"))
                metadata = json.loads(archive.read(prefix + "ground_truth/metadata.json"))
                digest = hashlib.sha256()
                dbprefix = prefix + "dbs/"
                for name in sorted(n for n in members if n.startswith(dbprefix)
                                   and "/" not in n[len(dbprefix):] and n.endswith(".jsonl")):
                    digest.update(Path(name).name.encode())
                    digest.update(archive.read(name))
                rows.append({"task_id": task, "split": split, "difficulty": metadata["difficulty"],
                    "num_api_calls": metadata["num_api_calls"], "num_apps": metadata["num_apps"],
                    "instruction": specs["instruction"], "dbs": digest.hexdigest(),
                    "released_oracle": prefix + "ground_truth/api_calls.json" in members})
        pool_bytes = b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode()
                              for row in sorted(rows, key=lambda value: value["task_id"]))
        pool_sha = hashlib.sha256(pool_bytes).hexdigest()
        if pool_sha != POOL_SHA256:
            raise ValueError(f"appworld: pool digest {pool_sha} != pinned {POOL_SHA256}")
        if POOL.is_symlink() or (POOL.exists() and POOL.read_bytes() != pool_bytes):
            raise ValueError("appworld: existing pool differs; preserve it and use another dataset root")
        if SRC_DIR.is_symlink() or not SRC_DIR.is_dir():
            raise ValueError("appworld: source directory must not be a symlink")
        if UNPACKED.is_symlink():
            raise ValueError("appworld: unpacked tree must not be a symlink")
        reused = UNPACKED.exists()
        if reused:
            _verify_unpacked(archive, entries, UNPACKED)
        else:
            with tempfile.TemporaryDirectory(prefix=".appworld-stage-", dir=SRC_DIR) as temp:
                stage = Path(temp) / "unpacked"
                stage.mkdir()
                for name, member in entries.items():
                    target = stage / name
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(member) as source, target.open("xb") as destination:
                            shutil.copyfileobj(source, destination)
                _verify_unpacked(archive, entries, stage)
                _publish_unpacked(stage, UNPACKED)
    POOL.parent.mkdir(parents=True, exist_ok=True)
    if not POOL.exists():
        with POOL.open("xb") as output:
            output.write(pool_bytes)
    receipt_dir = DATASET_ROOT / "preparation-receipts"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt = {"schema": "harnessgrad.appworld-preparation.v1", "bundle_sha256": bundle_sha,
               "pool_sha256": pool_sha, "worker_sha256": worker_sha, "runtime": runtime,
               "reused_unpacked_tree": reused,
               "unpacked_tree_verified": True,
               "unpacked_verification_scope": "official archive members; additional files are not authenticated"}
    with (receipt_dir / (secrets.token_hex(16) + ".json")).open("x", encoding="utf-8") as out:
        json.dump(receipt, out, sort_keys=True, indent=2)
    _refresh_loaded_pool()
    return POOL


def _pool() -> list[dict]:
    if not POOL.is_file():
        raise FileNotFoundError("appworld: pool.jsonl is not prepared; run python -m tools.prepare_appworld after preparing the execution runtime")
    actual = hashlib.sha256(POOL.read_bytes()).hexdigest()
    if actual != POOL_SHA256:
        raise ValueError(f"appworld: pool digest {actual} != pinned {POOL_SHA256}")
    return [json.loads(line) for line in POOL.read_text(encoding="utf-8").splitlines() if line.strip()]


def _oracle_ids() -> set[str]:
    return {row["task_id"] for row in _pool() if row.get("released_oracle")}


def oracle_files(task: dict) -> dict[str, str]:
    task_id = task["task_id"]
    if task_id not in _oracle_ids():
        raise ValueError(
            f"appworld: no released reference for {task_id}; coverage={ORACLE_COVERAGE}"
        )
    path = UNPACKED / "data" / "tasks" / task_id / "ground_truth" / "api_calls.json"
    if not path.is_file():
        raise ValueError(f"appworld: released API-call log missing for {task_id}")
    calls = json.dumps(json.loads(path.read_text()))
    return {"reference.py":
            f"import json\ncalls = json.loads({calls!r})\n" +
            "for call in calls:\n" +
            "    method = call.get('method', 'get').lower()\n" +
            "    requester_call = f\"requester.{method}({call['url']!r}, data={call.get('data') or {}!r}, client=requester.client)\"\n" +
            "    exec(requester_call)\n"}


try:
    _pool_rows = _pool()
    _POOL_ERROR = None
except FileNotFoundError as exc:
    _pool_rows = []
    _POOL_ERROR = str(exc)

ORACLE_COVERAGE = (f"{sum(bool(row.get('released_oracle')) for row in _pool_rows)} of "
                   f"{TASKS_IN_RELEASE} tasks (released train+dev coverage)"
                   if _POOL_ERROR is None else "unavailable until prepared")
_ROWS = {row["task_id"]: row for row in _pool_rows}
_ALL = [row["task_id"] for row in _ROWS.values()]


def _study_ids() -> list[str]:
    """The study-side ids named by `HG_APPWORLD_STUDY_IDS`, in file order.

    A repeated id is refused rather than deduplicated: the study score is a mean over the
    study tasks, so a duplicated line would weight one task twice and quietly move the
    number that selects a checkpoint. Set-based validation downstream would not notice,
    because the id is a member either way.
    """
    if not _STUDY_FILE:
        return []
    text = Path(_STUDY_FILE).read_text(encoding="utf-8")
    ids = [chunk for line in text.splitlines()
           for chunk in line.replace(",", " ").split()]
    repeats = sorted({task_id for task_id in ids if ids.count(task_id) > 1})
    if repeats:
        raise ValueError(
            f"HG_APPWORLD_STUDY_IDS={_STUDY_FILE} names {len(repeats)} task id(s) more "
            f"than once ({', '.join(repeats[:5])}); a repeated id would be measured once "
            "and counted twice in the study score")
    return ids


def _sides() -> tuple[list[str], list[str]]:
    """`(study ids, eval ids)` for this run's two knobs.

    Both knobs are refused rather than repaired. A study side that silently dropped the
    ids it could not find, or an eval side that silently fell back to the default pool,
    would change what a score means without changing anything a reader can see.
    """
    if _EVAL_POOL == "train":
        raise ValueError(
            "HG_APPWORLD_POOL=train would measure the training split, which is the "
            "material the study set is drawn from; a score on it is not an evaluation. "
            "The eval pool is one of " + " / ".join(EVAL_POOLS))
    if _EVAL_POOL not in EVAL_POOLS:
        raise ValueError(
            f"HG_APPWORLD_POOL={_EVAL_POOL!r} is not an official split; the eval pool "
            "is one of " + " / ".join(EVAL_POOLS))
    pool = TEST_SPLITS if _EVAL_POOL == "test" else (_EVAL_POOL,)
    eval_ids = [task_id for task_id, row in _ROWS.items() if row["split"] in pool]
    if _STUDY_FILE:
        study_ids = _study_ids()
        if not study_ids:
            raise ValueError(
                f"HG_APPWORLD_STUDY_IDS={_STUDY_FILE} names no task ids; an empty study "
                "file would leave the study side empty")
        unknown = [task_id for task_id in study_ids if task_id not in _ROWS]
        if unknown:
            raise ValueError(
                f"{len(unknown)} of {len(study_ids)} task ids in {_STUDY_FILE} are not "
                f"in the release (first: {unknown[:3]})")
        return study_ids, eval_ids
    # **Study is the official `train` split, and nothing else.** The release happens to call
    # dev "development material" as well, and an earlier version of this default studied on
    # both (147); that makes dev both the material a method learns from and the split its
    # checkpoints are selected on, which is leakage inside our own protocol. Every published
    # AppWorld number was measured on the 90-task train side (the run receipts carry
    # `train_n: 90`), so this changes no measurement -- it makes the default say what the
    # experiments did. `HG_APPWORLD_POOL=dev` still scores dev; it no longer shrinks the
    # study side, because the study side is already train alone.
    study_ids = [task_id for task_id in _ALL if _ROWS[task_id]["split"] == "train"]
    return study_ids, eval_ids


#: The sides this run measures. A bad knob is reported by `load()` rather than raised
#: here: `published()` hides a module that will not import, and a dataset that vanished
#: from the chooser would say nothing about the environment variable or file that broke
#: it.
try:
    _STUDY_IDS, _EVAL_IDS = _sides()
    _LOAD_ERROR: str | None = None
except Exception as _exc:  # noqa: BLE001 - reported by load(), which is where it bites
    _STUDY_IDS, _EVAL_IDS = [], []
    _LOAD_ERROR = f"{type(_exc).__name__}: {_exc}"

#: The tasks this run can see: the study side and the eval pool, and nothing else. With
#: neither knob set these are every task in the release, in the order this adapter has
#: always loaded them.
_RUN_IDS = [task_id for task_id in _ALL
            if task_id in set(_STUDY_IDS) | set(_EVAL_IDS)]

SPLIT = {"train": list(_STUDY_IDS), "eval": list(_EVAL_IDS)}

#: The benchmark's own evaluation set, within this run's pool. Under the default
#: `pool=test` that is both test files. `pool=test_normal` declares the 168 tasks of that
#: file, and `pool=dev` declares nothing, because no task of the set the published
#: numbers are computed on is in a dev pool; `--side eval --split official` then refuses
#: on an empty side rather than inventing one.
OFFICIAL_EVAL = [task_id for task_id in _EVAL_IDS
                 if _ROWS[task_id]["split"] in TEST_SPLITS]


def _goal(row: dict) -> str:
    return row["instruction"]


def _runtime_prompt(task_id: str) -> str:
    task_arg = shlex.quote(task_id)
    action = f"./aw action --task {task_arg} --code"
    discovery = action + " 'print(apis.api_docs.show_app_descriptions())'"
    return f"""AppWorld provides a shell CLI in the working directory: ./aw.
Use your harness's shell/command tool to run these commands; Python snippets
belong in --code, not in the local shell or a local Python process.

Initialize once: `./aw init --task {task_arg}`
This returns the task, supervisor identity and current datetime. Reinitializing
resets the world, so keep using action after initialization.

Execute Python in the service: `{action} '<python>'`
The service interpreter already defines `apis`. Variables and API state persist
between action calls. Each action executes a whole Python snippet, which may
include multiple API calls, assignments, loops and conditionals. Print the
responses you need to inspect; one action is not one API call. Local files and
imports do not provide AppWorld APIs.

Start discovery with this executable API action: `{discovery}`
Then discover the relevant app's methods with
`apis.api_docs.show_api_descriptions(app_name="<discovered app>")` and inspect
parameters with `apis.api_docs.show_api_doc(app_name="<discovered app>",
api_name="<discovered API>")`, each inside a printed --code action.
Call the documented methods as `apis.<app>.<api>(...)`. Discover supervisor
APIs for account credentials when needed; use returned values and documented
parameters rather than guessing method names, passwords or IDs.

Perform the requested changes through these API actions and inspect their results.
The service records API calls automatically; its recording is not an actor file to
create or edit. A local script, plain text answer or print-only snippet does not
change the world.

Completing the task is itself a change the world records. If the APIs you discover
expose a completion or submission operation, invoke it through an --code action so
the world observes the completion, and then report what you did as your harness
answer. The harness answer is a report, not the completion: on its own it leaves
the task unfinished, however correct the state changes you made.
CLI syntax help: `./aw --help` and `./aw action --help`.
"""


def _setup(task_id: str) -> dict:
    return {"files": [{"path": "aw", "from": str(ASSETS / "cli" / "aw")}],
            "runtime_prompt": _runtime_prompt(task_id)}


def load():
    if _POOL_ERROR is not None:
        raise ValueError(_POOL_ERROR)
    if _LOAD_ERROR is not None:
        raise ValueError(f"appworld: {_LOAD_ERROR}")
    tasks = [{"task_id": task_id, "goal": _goal(_ROWS[task_id]), "inputs": []}
             for task_id in _RUN_IDS]
    return tasks, {task["task_id"]: "AppWorld TestTracker.success" for task in tasks}


def reporting_manifest(task_ids: list[str]) -> list[dict]:
    """Trusted, selected-only reporting metadata derived from public IDs/splits.

    Never add this manifest to actor tasks or SETUP. No instruction, database,
    difficulty, solution, evaluator or gold-derived metadata is exported.
    """
    return make_manifest([{"task_id": tid, "split": _ROWS[tid]["split"]}
                          for tid in task_ids])


ASSETS = Path(__file__).parent / "appworld"

#: Keyed by `_RUN_IDS`, not `_ROWS`: the platform refuses a declared
#: SETUP/VERIFY/ENV entry for a task `load()` did not return, so a narrowed
#: pool has to narrow all three with it.
SETUP = {
    task_id: _setup(task_id)
    for task_id in _RUN_IDS
}
def _verifier(task_id):
    return {
        "kind": "command",
        "scorer_bundle": {"isolation_class": "snapshot-service"},
        "argv": [
            "python3",
            "verify/check.py",
            "--task-id",
            task_id,
            "--source-split",
            _ROWS[task_id]["split"],
            "--log",
            f"/recordings/appworld/experiments/outputs/harness/tasks/{task_id}/logs/api_calls.jsonl",
            "--reward-file",
            "/verifier/reward.txt",
            "--evidence-file",
            "/verifier/evidence.json",
        ],
        "inputs": [{"path": "verify/check.py", "from": str(ASSETS / "verify" / "check.py")}],
        "reward_file": "/verifier/reward.txt",
        "evidence_file": "/verifier/evidence.json",
        "image": VERIFIER_IMAGE,
        "timeout_s": 900,
    }

VERIFY = {task_id: _verifier(task_id) for task_id in _RUN_IDS}

DEFAULT_ENV = {
    "kind": "exec",
    "image": IMAGE,
    "workdir": WORKDIR,
    "state": "mount",
    "network": "services",
    "placement": "inside",
    "services": [
        {
            "name": "appworld",
            "image": SERVICE_IMAGE,
            "cpus": 2,
            "memory_mb": 4096,
            "command": ["/usr/local/bin/appworld-server"],
            "port": 8000,
            "health": {
                "argv": [
                    "python",
                    "-c",
                    "import urllib.request as u; u.urlopen('http://localhost:8000/').read()",
                ],
                "timeout_s": 60,
            },
            "record": True,
            "needs_seed": False,
            "needs_model": False,
        }
    ],
    "cpus": 2,
    "memory_mb": 4096,
    "agent_timeout_s": 900,
    "verify_timeout_s": 900,
}
ENV = {task_id: DEFAULT_ENV for task_id in _RUN_IDS}


def _refresh_loaded_pool():
    """Refresh derived declarations after preparation in the same interpreter."""
    global _pool_rows, _POOL_ERROR, ORACLE_COVERAGE, _ROWS, _ALL
    global _STUDY_IDS, _EVAL_IDS, _LOAD_ERROR, _RUN_IDS, SPLIT, OFFICIAL_EVAL
    global SETUP, VERIFY, ENV
    rows = _pool()
    _pool_rows, _POOL_ERROR = rows, None
    ORACLE_COVERAGE = (f"{sum(bool(row.get('released_oracle')) for row in rows)} of "
                       f"{TASKS_IN_RELEASE} tasks (released train+dev coverage)")
    _ROWS = {row["task_id"]: row for row in rows}
    _ALL = list(_ROWS)
    try:
        _STUDY_IDS, _EVAL_IDS = _sides()
        _LOAD_ERROR = None
    except Exception as exc:
        _STUDY_IDS, _EVAL_IDS = [], []
        _LOAD_ERROR = f"{type(exc).__name__}: {exc}"
    selected = set(_STUDY_IDS) | set(_EVAL_IDS)
    _RUN_IDS = [task_id for task_id in _ALL if task_id in selected]
    SPLIT = {"train": list(_STUDY_IDS), "eval": list(_EVAL_IDS)}
    OFFICIAL_EVAL = [task_id for task_id in _EVAL_IDS if _ROWS[task_id]["split"] in TEST_SPLITS]
    SETUP = {task_id: _setup(task_id) for task_id in _RUN_IDS}
    VERIFY = {task_id: _verifier(task_id) for task_id in _RUN_IDS}
    ENV = {task_id: DEFAULT_ENV for task_id in _RUN_IDS}


def downloads() -> list[dict]:
    return [
        {
            "kind": "http",
            "name": "AppWorld encrypted data bundle",
            "version": "0.1.0",
            "url": "https://s3.us-west-2.amazonaws.com/appworld.dev/data-0.1.0.bundle",
            "dst": "_src/data-0.1.0.bundle",
            "size": "34280074 bytes",
            "sha256": "fd9f9608c2ec71ed0ac25c3633a738b9129a318a129e31230425b9188e508250",
            "note": "Official encrypted AppWorld 0.1.0 bundle. prepare.py decrypts and extracts it under the external dataset root, then writes pool.jsonl; the trusted service image contains the pinned appworld package and task data.",
        }
    ]


def provision_status() -> dict:
    return {
        "root": str(DATASET_ROOT),
        "present": BUNDLE.is_file() and POOL.is_file(),
        "bundle": str(BUNDLE),
        "pool": str(POOL),
        "tasks": len(_ROWS),
    }


__all__ = [
    "KIND", "DATASET_ROOT", "TASKS_IN_RELEASE", "SPLIT_SIZES", "TEST_SPLITS",
    "EVAL_POOLS", "SPLIT_SOURCE",
    "OFFICIAL_EVAL", "ORACLE_SOURCE", "ORACLE_VALIDATED_BY", "ORACLE_COVERAGE",
    "bundle_zip", "task_ids", "split_ids", "prepare", "load", "downloads",
    "provision_status", "oracle_files", "reporting_manifest", "SETUP", "VERIFY", "ENV",
    "DEFAULT_ENV", "SPLIT",
]
