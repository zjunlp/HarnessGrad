# Native Web relay identity before migration

`before.json` is an authored verification-context envelope from revision
`34e7d59f`, before the relay changed its Web gateway import. It contains only
hashes and an authored task ID, not dataset tasks, responses or credentials.
The relative program path makes this source-only control independent of checkout
location; production task descriptors retain their existing absolute path.
