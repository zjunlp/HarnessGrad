# Runtime import migration identities

`before.json` contains public source-only metadata captured at `c8264b76`
before migrating three adapters to the canonical dataset runtime import. The
normalized AST replaces the old import path with the canonical path before
hashing; it allows checking that the adapter syntax otherwise stays identical.
The fixture contains no task inputs, answers, scores or receipts.
