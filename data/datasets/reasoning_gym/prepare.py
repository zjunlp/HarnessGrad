#!/usr/bin/env python3
"""Import or inspect the existing pinned Reasoning Gym pool.

Use --import-pool with an operator-supplied snapshot, then --check. Diagnostic
--generate uses the pinned library, seed and family sizes, but these do not
guarantee the historical pool bytes; mismatches are retained and refused.
Generation runs in a private working directory, not a network or OS sandbox.
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import stat
import urllib.error
import urllib.parse
import urllib.request
import http.client
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from harnessgrad.paths import dataset_root

PACKAGE = "reasoning-gym"

#: The retained package version defines the scoring contract.
VERSION = "0.1.25"

#: One seed for the whole pool and one item count per family. Both are part of the pin:
#: These inputs fix each family's pseudorandom generator, but do not alone guarantee
#: byte-identical output: some families also depend on hash-randomized set order.
SEED = 20261008
PER_FAMILY = 20
POOL_SHA256 = 'c0ff4eaef318cd59afcc61472ebe206362fc0211c4b589ed0d53946a569bd177'

#: Retained families selected by the historical reference/empty-answer controls.
#: Sorted names make diagnostic generation differences easier to inspect.
FAMILIES = (
    "ab", "acre", "aiw", "arc_1d", "base_conversion", "basic_arithmetic", "bf",
    "binary_alternation", "binary_matrix", "bitwise_arithmetic", "caesar_cipher",
    "calendar_arithmetic", "chain_sum", "circuit_logic", "codeio", "coin_flip",
    "complex_arithmetic", "count_bits", "count_primes", "course_schedule", "cryptarithm",
    "decimal_arithmetic", "decimal_chain_sum", "dice", "family_relationships",
    "figlet_font", "futoshiki", "game_of_life", "game_of_life_halting", "gcd",
    "group_anagrams", "gsm_symbolic", "intermediate_integration", "isomorphic_strings",
    "jugs", "kakurasu", "knight_swap", "knights_knaves", "largest_island", "lcm",
    "leg_counting", "letter_counting", "letter_jumble", "list_functions",
    "mahjong_puzzle", "manipulate_matrix", "maze", "mini_sudoku", "modulo_grid",
    "n_queens", "needle_haystack", "number_filtering", "number_format",
    "number_sequence", "number_sorting", "palindrome_generation",
    "palindrome_partitioning", "path_star", "polynomial_equations",
    "polynomial_multiplication", "pool_matrix", "products", "quantum_lock",
    "ransom_note", "rectangle_count", "rotate_matrix", "rotten_oranges",
    "self_reference", "shortest_path", "simple_equations", "simple_geometry",
    "simple_integration", "sokoban", "spell_backward", "spiral_matrix",
    "string_insertion", "string_manipulation", "string_splitting", "string_synthesis",
    "sudoku", "survo", "syllogism", "time_intervals", "tower_of_hanoi", "tsumego",
    "word_ladder", "word_sequence_reversal", "word_sorting", "zebra_puzzles",
)

DATASET_ROOT = dataset_root('reasoning_gym')
POOL = DATASET_ROOT / "pool.jsonl"


def _entry(name: str, index: int) -> dict:
    """One task, and the entry object the official scorer needs.

    The **entry** travels with the task on purpose: `Dataset.score_answer(answer, entry)`
    reads `entry["answer"]` and, for some families, `entry["metadata"]`, so a check that had
    only the answer string could not call the official scorer at all.
    """
    from reasoning_gym import create_dataset

    dataset = create_dataset(name, size=PER_FAMILY, seed=SEED)
    item = dataset[index]
    # No `task_id` here on purpose: the platform names the tasks (`reasoning_gym/eval/<n>`,
    # `QASpec.records()`), and a second id in the row would disagree with it -- the first
    # version carried `reasoning_gym/<family>/<index>` and a test duly looked for an id that
    # does not exist. The row's coordinates are `family` and `index`.
    return {
        "family": name,
        "index": index,
        "question": str(item["question"]).strip(),
        "entry": json.loads(json.dumps(item, default=str)),
    }


def _build_in_runtime() -> tuple[str, int]:
    """Write the pool and return `(sha256, task count)`."""
    DATASET_ROOT.mkdir(parents=True, exist_ok=True)
    rows = [_entry(name, i) for name in FAMILIES for i in range(PER_FAMILY)]
    body = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    POOL.write_text(body, encoding="utf-8")
    return hashlib.sha256(body.encode()).hexdigest(), len(rows)


def build() -> tuple[str, int]:
    """Materialize only through the explicitly prepared dependency artifact."""
    from harnessgrad.dataset_runtime import admit_binding, bind, resolve_execution
    binding = bind(resolve_execution('reasoning_gym'), 'verifier')
    admit_binding(binding, 'verifier')
    root = Path(DATASET_ROOT).resolve()
    destination = str(root)
    if any(path.exists() or path.is_symlink() for path in
           (root / POOL.name, root / 'pool.runtime.json', root / 'pool.snapshot.json')):
        raise ValueError('Reasoning Gym pool or its runtime receipt already exists; use a fresh root')
    bootstrap = ("import contextlib,json,runpy,sys\n"
                 "assert sys.executable==sys.argv[2]\n"
                 "m=runpy.run_path(sys.argv[1])\n"
                 "with contextlib.redirect_stdout(sys.stderr):\n"
                 " value=m['_build_in_runtime']()\n"
                 "print(json.dumps(value))\n")
    with tempfile.TemporaryDirectory(prefix='hg-reasoning-gym-prepare-') as temporary:
        env = {'PATH': os.defpath, 'HOME': temporary, 'TMPDIR': temporary,
               'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
               'HG_REVIEWED_ENVIRONMENT_ONLY': '1', 'HG_DATA_REASONING_GYM': destination,
               'PYTHONDONTWRITEBYTECODE': '1'}
        result = subprocess.run([binding['python'], '-I', '-B', '-c', bootstrap,
                                 str(Path(__file__).resolve()), binding['python']],
                                cwd=temporary, env=env, capture_output=True, text=True, timeout=600)
    if result.returncode:
        raise ValueError('Reasoning Gym pool generation failed in its prepared runtime')
    value = json.loads(result.stdout)
    if (not isinstance(value, list) or len(value) != 2 or not isinstance(value[0], str)
            or len(value[0]) != 64 or type(value[1]) is not int or value[1] <= 0):
        raise ValueError('Reasoning Gym pool generation returned invalid provenance')
    admit_binding(binding, 'verifier')
    if hashlib.sha256((root / POOL.name).read_bytes()).hexdigest() != value[0]:
        raise ValueError('Reasoning Gym pool changed during generation')
    if value[0] != POOL_SHA256:
        raise ValueError('Reasoning Gym generated pool differs from the pinned digest; '
                         'retain it for diagnosis and use a fresh data root')
    receipt = {'schema': 'harnessgrad.reasoning-gym-generation.v1', 'operation': 'generate', 'package': PACKAGE,
               'version': VERSION, 'seed': SEED, 'per_family': PER_FAMILY,
               'pool_sha256': value[0], 'tasks': value[1], 'python_runtime': binding}
    with (root / 'pool.runtime.json').open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, sort_keys=True, indent=2)
        stream.write('\n')
    return value[0], value[1]


def digest() -> str:
    return hashlib.sha256(POOL.read_bytes()).hexdigest()


def _publish_snapshot(stage: Path, root: Path) -> None:
    """Atomically publish without replacing even an empty concurrent destination."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise ValueError('Atomic snapshot import requires Linux renameat2; no pool was published')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                       ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(stage), -100, os.fsencode(root), 1) != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise ValueError('Reasoning Gym data root already exists; use a fresh root')
        raise OSError(error, os.strerror(error), str(root))


def import_pool(source: Path, *, source_origin: str | None = None) -> tuple[str, int]:
    """Publish a pin-matching snapshot and receipt as one directory transaction."""
    requested = Path(DATASET_ROOT)
    if requested.is_symlink():
        raise ValueError('Reasoning Gym data root must not be a symlink')
    root = requested.resolve()
    root.parent.mkdir(parents=True, exist_ok=True)
    if root.exists():
        raise ValueError('Reasoning Gym data root already exists; use a fresh root')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    fd = os.open(source, flags)
    stage = None
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('Reasoning Gym snapshot must be an ordinary file')
        stage = Path(tempfile.mkdtemp(dir=root.parent, prefix='.reasoning-snapshot-'))
        with os.fdopen(fd, 'rb') as stream:
            fd = -1
            h = hashlib.sha256()
            with (stage / POOL.name).open('xb') as output:
                while block := stream.read(1024 * 1024):
                    h.update(block)
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            after = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('Reasoning Gym snapshot changed while copying')
        observed = h.hexdigest()
        if observed != POOL_SHA256:
            raise ValueError('Reasoning Gym snapshot does not match the pinned digest')
        value = {'schema': 'harnessgrad.reasoning-gym-snapshot.v1', 'operation': 'import',
                 'acquisition': 'operator-supplied-local-snapshot',
                 'source_sha256': observed, 'pool_sha256': observed, 'bytes': before.st_size}
        if source_origin is not None:
            value['acquisition'] = 'operator-supplied-url-snapshot'
            value['source_origin'] = source_origin
        with (stage / 'pool.snapshot.json').open('x', encoding='utf-8') as out:
            json.dump(value, out, sort_keys=True, indent=2)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        _publish_snapshot(stage, root)
        stage = None
        return observed, before.st_size
    finally:
        if fd >= 0:
            os.close(fd)
        if stage is not None:
            import shutil
            shutil.rmtree(stage)


SNAPSHOT_MAX_BYTES = 8 * 1024 * 1024
SNAPSHOT_TIMEOUT_SECONDS = 15
SNAPSHOT_TOTAL_SECONDS = 60
NETWORK_ENVIRONMENT = ('HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY',
                       'http_proxy', 'https_proxy', 'no_proxy',
                       'SSL_CERT_FILE', 'SSL_CERT_DIR')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_snapshot(url: str) -> tuple[str, int]:
    """Acquire explicit operator bytes, then use the existing atomic importer.

    No redirect is followed, including same-host redirects. Credentials and paths
    are never reported; provenance keeps only scheme, hostname and explicit port.
    """
    try:
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.fragment or not url.isascii() or any(ord(c) < 33 for c in url)):
            raise ValueError
        port = parsed.port
        host = parsed.hostname
        if ':' in host:
            host = '[' + host + ']'
        origin = f'{parsed.scheme}://{host}' + (f':{port}' if port is not None else '')
    except ValueError:
        raise ValueError('Snapshot URL requires HTTP(S), a host, and no userinfo or fragment') from None
    root = Path(DATASET_ROOT)
    if root.exists() or root.is_symlink():
        raise ValueError('Reasoning Gym data root already exists; use a fresh root')
    with tempfile.TemporaryDirectory(prefix='hg-reasoning-download-') as attempt:
        source = Path(attempt) / 'snapshot.jsonl'
        # A separate process supplies a hard wall-clock bound even for a peer
        # dripping response headers or for an unusually slow DNS resolver.
        bootstrap = ("import json,runpy,sys\n"
                     "m=runpy.run_path(sys.argv[1])\n"
                     "try:\n"
                     " m['_download_snapshot'](sys.stdin.read(),m['Path'](sys.argv[2]),int(sys.argv[3]),float(sys.argv[4]))\n"
                     "except Exception as e:\n"
                     " print(json.dumps({'error':str(e)}));sys.exit(1)\n")
        try:
            result = subprocess.run(
                [sys.executable, '-I', '-B', '-c', bootstrap, str(Path(__file__).resolve()),
                 str(source), str(SNAPSHOT_MAX_BYTES), str(SNAPSHOT_TIMEOUT_SECONDS)],
                input=url, capture_output=True, text=True, timeout=SNAPSHOT_TOTAL_SECONDS,
                cwd=attempt, env={'PATH': os.defpath, 'HOME': attempt, 'TMPDIR': attempt,
                                  'HG_REVIEWED_ENVIRONMENT_ONLY': '1',
                                  'PYTHONDONTWRITEBYTECODE': '1',
                                  **{key: os.environ[key] for key in NETWORK_ENVIRONMENT
                                     if key in os.environ}})
        except subprocess.TimeoutExpired:
            raise ValueError('Snapshot download exceeded the total time limit; no pool was published') from None
        if result.returncode:
            # Only helper-owned messages may reach the public CLI. Never print
            # an unexpected exception, traceback, URL, server header or body.
            try:
                message = json.loads(result.stdout)['error']
            except (ValueError, KeyError, TypeError):
                message = ''
            allowed = (
                'Snapshot download requires HTTP 200; no pool was published',
                'Snapshot download exceeds the byte limit or has invalid length',
                'Snapshot download exceeds the byte limit',
                'Snapshot download interrupted before the declared length',
                'Snapshot download has an invalid Content-Length',
                'Snapshot download failed or was interrupted; no pool was published',
            )
            if message not in allowed and not (
                    isinstance(message, str) and message.startswith('Snapshot download failed: HTTP ')
                    and message.removeprefix('Snapshot download failed: HTTP ').removesuffix(
                        '; redirects are refused').isdigit()
                    and message.endswith('; redirects are refused')):
                message = 'Snapshot download failed; no pool was published'
            raise ValueError(message)
        # The existing importer hashes the exact downloaded bytes while copying
        # into its transaction, and is the only code that publishes a ready pool.
        return import_pool(source, source_origin=origin)


def _download_snapshot(url: str, source: Path, max_bytes: int, socket_timeout: float) -> None:
    """Private worker: download only; parent applies the hard total deadline."""
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        request = urllib.request.Request(url, headers={'Accept-Encoding': 'identity'})
        with opener.open(request, timeout=socket_timeout) as response:
            if response.status != 200:
                raise ValueError('Snapshot download requires HTTP 200; no pool was published')
            length = response.headers.get('Content-Length')
            try:
                expected = int(length) if length is not None else None
            except ValueError:
                raise ValueError('Snapshot download has an invalid Content-Length') from None
            if expected is not None and (expected < 0 or expected > max_bytes):
                raise ValueError('Snapshot download exceeds the byte limit or has invalid length')
            size = 0
            with source.open('xb') as out:
                while True:
                    block = response.read1(min(65536, max_bytes - size + 1))
                    if not block:
                        break
                    size += len(block)
                    if size > max_bytes:
                        raise ValueError('Snapshot download exceeds the byte limit')
                    out.write(block)
            if expected is not None and size != expected:
                raise ValueError('Snapshot download interrupted before the declared length')
    except urllib.error.HTTPError as exc:
        raise ValueError(f'Snapshot download failed: HTTP {exc.code}; redirects are refused') from None
    except (OSError, urllib.error.URLError, http.client.HTTPException):
        raise ValueError('Snapshot download failed or was interrupted; no pool was published') from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check", action="store_true",
                        help="only report whether the pool on disk is the pinned one")
    actions.add_argument('--import-pool', type=Path,
                         help='copy an operator-supplied historical pool only if its SHA-256 matches the existing pin')
    actions.add_argument('--snapshot-url', help='fetch an operator-supplied HTTP(S) snapshot; no default URL')
    actions.add_argument('--generate', action='store_true',
                         help='diagnostically generate in the prepared runtime; output may not match the existing pin')
    args = parser.parse_args(argv)

    if args.check:
        if not POOL.is_file():
            print(f"missing: {POOL}")
            return 1
        observed = digest()
        print(f"{POOL.name}: {POOL.stat().st_size} bytes sha256={observed}")
        return 0 if observed == POOL_SHA256 else 1

    if args.snapshot_url is not None:
        try:
            sha, size = fetch_snapshot(args.snapshot_url)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f'imported {POOL} ({size} bytes) sha256={sha}')
        return 0
    if args.import_pool is not None:
        sha, size = import_pool(args.import_pool)
        print(f'imported {POOL} ({size} bytes) sha256={sha}')
        return 0
    if not args.generate:
        parser.error('supply --import-pool <snapshot>, --snapshot-url <URL> or --check; --generate is for diagnosis only')
    sha, count = build()
    print(f"wrote {POOL} ({count} tasks over {len(FAMILIES)} families)")
    print(f"sha256={sha}")
    print('generated bytes match the existing pinned pool; runtime receipt recorded')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
