# Platform dataset runtime import baseline

`before.json` contains public source-only scoring and measurement identities at
revision `3dc7c562`, before platform consumers changed their dataset runtime
import. StrongREJECT uses an authored judge name and loopback endpoint hash.
There are no task examples, answers, scores, credentials or run receipts.

A subsequent provider-worker import migration changes ChessBench’s loader
fingerprint, while its declared data and scorer identities remain the same.

The subsequent native QA import migration similarly changes the MATH and
MATH-500 loader fingerprints without changing their data or scorer identities.

The subsequent Reasoning Gym preparation import migration changes its loader
fingerprint, while its pool declaration and scorer identity remain the same.
