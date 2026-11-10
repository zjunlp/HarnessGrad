"""Historical sealed scorer pins for explicit archive reproduction.

Current built-in scoring uses public-native-local-v1 and local execution recipes.
These immutable pins preserve old evidence; they are not new-machine prerequisites.
"""

NATIVE_PINS = {
    "alfworld": {
        "dataset": "alfworld",
        "manifest": "data/runtimes/alfworld-native.json",
        "sha256": "ef0be1521477f66baf7859f1a25399211770dc69fdbd82675b130a9956f39939"
    },
    "bird_minidev": {
        "dataset": "bird_minidev",
        "manifest": "data/runtimes/bird_minidev-native.json",
        "sha256": "0ecaf2a6e99b64ee035e210192fdb45789fb290e9586dc3197fc26ec06df41e0"
    },
    "chessbench_puzzles": {
        "dataset": "chessbench_puzzles",
        "manifest": "data/runtimes/chessbench_puzzles-native.json",
        "sha256": "1dd47b9a27c0598c89c55c08579e7f85655b821166bc8a8150645e9379479599"
    },
    "jericho": {
        "dataset": "jericho",
        "manifest": "data/runtimes/jericho-native.json",
        "sha256": "66980eb3446bddfa1f27dec7664ce18d0e1161d572a0fb6c1c70b24aa9e93e4f"
    },
    "scienceworld": {
        "dataset": "scienceworld",
        "manifest": "data/runtimes/scienceworld-native.json",
        "sha256": "3b63271f56e0ef248855811f8853530b869844d08a6c58cc635e439e65156f7a"
    }
}
