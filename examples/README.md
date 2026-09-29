# EdgeProc end-to-end example

A 15-doc outdoor-gear catalog driven through every shipped CLI verb —
`keygen` → build a local FAISS index → `publish` → `sync` → `route` — over an
ephemeral temp workspace. Run it:

```bash
bash run_loop.sh
```

You'll see five stages of output ending in a routed `SEARCH` result. The
heavier registry-wiring + saved-index Python lives in
[`quickstart.py`](./quickstart.py); the Python example in
[`docs/QUICKSTART.md`](../docs/QUICKSTART.md#use-it-from-python) is the one-line
`LocalVecRuntime.from_texts` variant of the same thing.
