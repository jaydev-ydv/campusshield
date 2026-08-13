# ml/data

Empty by design, and git-ignored.

No external dataset is currently licensed for use by this project — see
[../DATASET.md](../DATASET.md). The synthetic corpus is generated on demand by
`ml/src/synthetic.py` rather than stored, so that it can never be mistaken for
collected data sitting in a data directory.

If SafeCity permission is obtained, place the extracted files here and set
`dataset.source: safecity` in `ml/configs/default.yaml`. Record the permission in
DATASET.md before doing so.
