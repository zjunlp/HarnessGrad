Current evaluation uses the public-native-local-v1 profile. Prepare this dataset's
execution dependencies with `python tools/prepare_dataset.py --dataset scienceworld --runtime execution`.
No original scorer-machine archive is required. See [public native scoring](../../../docs/public-native-scoring.md).
The exact-byte archive material below is retained as historical evidence or explicit
legacy reproduction guidance; it is not the current default installation requirement.

ScienceWorld actor dependency boundary
=====================================

The task actor receives `scw-relay` as `scw` and `scw_client.py`. These files use
only Python's standard library. The managed trusted service owns ScienceWorld,
Py4J, the pinned JRE and the selected task. It rebuilds and replays a fresh
world for each request, with the original 100-step limit and no simplifications.
Only look, inventory and task requests with action histories are accepted;
there is no environment-object, reference, gold-path or arbitrary-call endpoint.
The CLI appends actions only after a successful replay, as before.

`execution-runtime.json` declares Python 3.12, ScienceWorld 1.2.3 and Py4J
0.10.9.9. ScienceWorld's required installed metadata declares only Py4J;
webserver/dev extras are excluded. Prepare the execution runtime explicitly
with the dataset-runtime tooling before materializing SETUP/VERIFY. Resolution
is read-only and gives a preparation hint when the artifact is absent. Catalog
imports neither install dependencies nor start Java. The managed payload JAR
must have SHA256 f4e1fdce0cdabe1bab7128a7e78ea820210d9db3c28d7272bcbbc3f3a1c5f0d7,
the unchanged sealed scorer's original JAR identity. Platform Python does not
need to import or locate ScienceWorld. Native verification remains the original
FrozenRuntime and its existing manifest, scorer, JRE and licensing pins.

The private setup declaration commits the selected task, JAR path, Java
executable digest and source digest. Verification-context capture additionally
commits exact service source/read paths (including the JRE tree), the selected
managed binding and original verifier inputs. Actor-visible task identity is
unchanged; actors receive no private service recipe or dependency package.

Synthetic tests exercise replay, stop conditions, request rejection, cleanup,
error redaction and payload lookup. These are authored-engine controls, not a
ScienceWorld benchmark episode or evidence that the native scorer completed a
real case. Full native-scored episode behavior remains unmeasured here.

Follow-up launcher evidence
--------------------------
The relay explicitly adds its admitted sibling directory to sys.path: managed
launches use Python -I -B, which omits the script directory. Actual launcher
controls run tool_relay.start, exact binding admission, python_service.command,
bubblewrap, the original relay.main and the standard-library CLI in a second
bubblewrap view with the published /run socket. A trusted child bootstrap
replaces only scw_env.make_env with an authored engine; main still authenticates
the real prepared JAR and original Java executable. Controls check selected
identity, no gold generation, source read-only access, invisible private neighbor,
cleanup, error-to-unknown receipts, and exact original CLI stdout/stderr/exit/log
behavior for all commands, terminal replay, malformed histories and the 100-step
limit. Another control launches the unmodified main with zero requests. These
controls do not instantiate a ScienceWorld world or run native verification.

Official operator controls
--------------------------
The retained --oracle/--noop and --platform spellings all enter run_one with
the complete setup declaration, the managed relay and the original native
verifier. --noop does not generate a reference. --oracle obtains references
only in oracle_worker.py launched with the admitted managed Python, -I -B and
the shared read-only bubblewrap service view; the worker authenticates the
same JAR and Java identity and closes its environment. That private operator
worker is not in the normal relay's read paths and is not a relay operation.
Only the explicit control harness receives .oracle/actions.json. It is limited
to the existing 100-move budget. Compatibility helper drive() starts the relay
and uses the actor sandbox; run_check() uses the native platform verifier.
Control tests use source-fixture runner results and authored managed-worker
engines, not real ScienceWorld cases or scorer results.
