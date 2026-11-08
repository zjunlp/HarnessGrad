# MATH and MATH-500 scorer runtime

Both datasets load prepared records with the Python standard library. Their
trusted verifier uses the shared `math` execution profile. Prepare it once:

```
python tools/prepare_dataset.py --dataset math --runtime execution
python tools/prepare_dataset.py --dataset math --runtime execution --check
```

The existing scorer requires `math-verify==0.9.0`. The complete CPython 3.12
lock was resolved from that release's package metadata using `uv pip compile
--only-binary :all:`; the additional exact versions are this runtime contract,
not a claim about historical upstream installations. The measured runtime
identity covers the selected venv and base interpreter, not the whole OS.
The native mathematical grading algorithm and original source pins are unchanged.

Math500 shares the `math` execution artifact. You may prepare or check it by
the dataset name: `python tools/prepare_dataset.py --dataset math500 --runtime
execution [--check]`. The result reports both the requested dataset and the
`math` runtime profile.
