# ChessBench loader runtime

The loader uses a managed Python 3.12 provider with `chess==1.11.2`, matching
the existing adapter dependency declaration. Prepare it explicitly with
`python tools/prepare_dataset.py --dataset chessbench_puzzles`; `--check` is
read-only. The separately pinned native scorer remains unchanged. The provider
identity records its dependency artifact; it does not seal all host libraries.
The scoring identity separately records the adapter, loader, verifier and native
scorer manifest source digests so changed code cannot reuse prior measurements.

The interactive stepwise controller uses the same bound provider in a trusted
worker. Its private reference and move history are not sent to the actor.
