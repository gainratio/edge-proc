# EdgeProc

A Python library for sending data files to many devices: each device checks they came from you, downloads only what changed, and can search them offline.

**Try it in a minute: `pip install "edge-proc[bundles]"`, then run the commands under [Try it](#try-it). No server or account needed.**

Say your app needs the same data on many devices you don't control: a product catalog, a
search index, a price list, a small AI model. The usual way is to put the file on a web
server and have each device download it. That has two problems. A device can't tell if the
file was corrupted or swapped on the way, so it may quietly give wrong answers. And when one
line changes, every device downloads the whole file again.

EdgeProc handles both. You publish a folder once, on your own machine: it cuts the files into
small pieces and signs a list of them with your private key. You upload the result to any
plain web server. Each device downloads only the pieces it doesn't have yet, checks them all
against your public key, and refuses the whole update if anything is wrong. Once the data is
on the device, EdgeProc can also search it there, with no network.

**Technical docs:** [Architecture](docs/ARCHITECTURE.md) · [Getting started for developers](docs/GETTING_STARTED.md) · [Full walkthrough with search](docs/QUICKSTART.md) · [Configuration](docs/CONFIGURATION.md) · [Operations and security](docs/OPERATIONS.md)

[![CI](https://github.com/hseshadr/edge-proc/actions/workflows/dagger.yml/badge.svg)](https://github.com/hseshadr/edge-proc/actions/workflows/dagger.yml)
[![PyPI](https://img.shields.io/pypi/v/edge-proc)](https://pypi.org/project/edge-proc/)
[![License](https://img.shields.io/github/license/hseshadr/edge-proc)](LICENSE)

## Try it

This uses local folders instead of a web server, so you can see every step. You need
Python 3.13 or newer.

1. Install EdgeProc from PyPI in a fresh virtual environment. The `[bundles]` extra adds
   signing and syncing; it is small.

   ```bash
   python3.13 -m venv .venv && source .venv/bin/activate
   pip install "edge-proc[bundles]"
   ```

2. Make a two-product catalog, a key pair, and publish it. Then pull it onto a "device"
   folder.

   ```bash
   mkdir -p demo/src && cd demo
   echo '{"p1": "red running shoes", "p2": "waterproof hiking boots"}' > src/catalog.json
   edgeproc keygen --out keys
   edgeproc publish --src src --origin-dir origin --key keys/private.key --bundle-id catalog --version 1.0.0 --pretty
   edgeproc sync --base-url origin --cache-dir cache --key keys/public.key --materialize-to device --pretty
   cat device/catalog.json
   ```

   ```text
   wrote keys/private.key and keys/public.key
   key_id 87207cf94bb9c28b
   published v1.0.0 manifest=71d733a8dddb
   synced v1.0.0 manifest=71d733a8dddb chunks_fetched=1 chunks_reused=0 bytes_fetched=70
   {"p1": "red running shoes", "p2": "waterproof hiking boots"}
   ```

   `keys/private.key` stays with you. `keys/public.key` is what you give every device.
   `origin/` is what you would upload to a web server. Your `key_id` will differ; the rest
   repeats exactly.

3. Add one file and publish a new version. The device downloads only the new piece.

   ```bash
   echo 'spring price list' > src/prices.txt
   edgeproc publish --src src --origin-dir origin --key keys/private.key --bundle-id catalog --version 1.0.1 --pretty
   edgeproc sync --base-url origin --cache-dir cache --key keys/public.key --materialize-to device --pretty
   ```

   ```text
   published v1.0.1 manifest=18499ab39ead
   synced v1.0.1 manifest=18499ab39ead chunks_fetched=1 chunks_reused=1 bytes_fetched=27
   ```

4. Try to sync without the public key. EdgeProc refuses; there is no "trust it this once" mode.

   ```bash
   edgeproc sync --base-url origin --cache-dir other-device --pretty; echo "exit=$?"
   ```

   ```text
   [config.missing] no trust root: pass --key or set EDGEPROC_TRUST_ROOT_PUBKEY_PATH (refusing to sync)
   exit=1
   ```

5. Corrupt one piece on the "server" and sync onto a new device. The whole update is refused.

   ```bash
   printf 'tampered' > "origin/chunk/$(ls origin/chunk | head -1)"
   edgeproc sync --base-url origin --cache-dir new-device --key keys/public.key --pretty; echo "exit=$?"
   ls cache new-device
   ```

   ```text
   [bundle.integrity_failed] sync failed: stored chunk failed to decompress
   exit=1
   cache:
   active
   chunks
   manifests

   new-device:
   chunks
   manifests
   ```

   The good device in `cache/` has an `active` pointer file that marks the version it
   serves. `new-device/` never got one, so it has nothing to serve rather than bad data. The
   code in square brackets is a stable error code a script can check. Set
   `EDGEPROC_ERROR_FORMAT=json` to get it as JSON.

This is real output from `edge-proc` 0.5.0, installed from PyPI on 2026-09-25.

To see on-device search as well, clone the repo and run `bash examples/run_loop.sh`. It
builds a search index over a 15-product catalog, ships it together with the search model,
and searches on the "device" with no network. The query "trail running shoes for the
morning" returns the "Trailhead Pro running shoes" first. The first run downloads an 87 MB
search model and about 1 GB of dependencies (PyTorch and FAISS); it took about 2 minutes. The
[full walkthrough](docs/QUICKSTART.md) does the same thing one step at a time.

## How it works

`edgeproc publish` cuts each file into pieces of about 64 KB. Each piece is named by a
fingerprint of its contents (a SHA-256 hash), so a changed or damaged piece no longer
matches its name. A list of all the pieces is fingerprinted too, and one small file that
points to that list is signed with your private key. `edgeproc sync` checks that signature
against the public key the device was given, downloads only the pieces it is missing, and
checks each one. Only when every check passes does the new version go live; otherwise the
device keeps the last good version.

For search, EdgeProc turns text into vectors with a small model and looks them up in a FAISS
index, optionally mixed with keyword search. Ship the model inside the same signed release
and the device never needs to download it. Without a local model, search refuses to run
rather than fetch one.

## How it fits with the related projects

- **[edgeproc-core](https://github.com/hseshadr/edgeproc-core)** (Python, on PyPI) is a
  smaller library underneath this one. EdgeProc uses two things from it: a common interface
  for vector search indexes, which EdgeProc's FAISS index implements, and the stable error
  codes, like `bundle.integrity_failed`. It installs automatically with EdgeProc. You would
  only use it on its own to keep each customer's results apart in a shared search index.
- **[@edgeproc/browser](https://github.com/hseshadr/edgeproc-browser)** (TypeScript, for web
  pages) does the checking half of EdgeProc inside a browser tab. It reads the same signed
  format that `edgeproc publish` writes, so you can publish with this library and load the
  data in a web page.
- **[edge-reco](https://github.com/hseshadr/edge-reco)** is a demo online store
  ([edge-reco.com](https://edge-reco.com)) that runs product search in the shopper's
  browser. It uses these libraries.
- **[privacy-core](https://github.com/hseshadr/privacy-core)** (published on npm as
  `@edgeproc/privacy-core`) is a separate project despite the shared name. It hides card
  numbers and similar IDs from AI prompts in the browser, and does not use EdgeProc.

## What it does not do

- **It is not a service.** There is nothing to sign up for. It is a library and a command-line
  tool that your app and your build machine run.
- **Data flows one way.** One publisher signs; many devices read. It is not a two-way folder
  sync like Dropbox.
- **It can't protect a device or build machine that is already compromised**, or a private key
  that was stolen. You can revoke a key, but each device only learns about it when you update
  its key ring. There is no online revocation list.
- **The memory budget is not a hard memory limit.** EdgeProc refuses new work when the declared
  budget is used up, but it does not cap what FAISS or PyTorch actually allocate.
- **Search is heavy to install.** The `[localvec]` extra pulls in PyTorch and FAISS, about
  950 MB.
- **CI tests on Linux only.** It is used on macOS too, but Windows and macOS are not tested in CI.
- **Search quality on your data is not measured.** The default model is a small general one
  (`all-MiniLM-L6-v2`). Check it against your own data.

## When to use something else

| If you need | Use |
| --- | --- |
| No code on the device, always-fresh data, and per-search cost is fine | A hosted search or vector database API |
| One small file that rarely changes, from a server you fully trust | A plain HTTPS download with a checksum |
| Several signing roles, threshold signing, or online revocation | A full software-update framework such as [TUF](https://theupdateframework.io/) |
| Two-way sync between machines you control | rsync or a file-sync tool |
| The same data on devices you don't control, checked before use, with small updates and offline search | EdgeProc |

## Install

EdgeProc is on PyPI as [`edge-proc`](https://pypi.org/project/edge-proc/). You import it as
`edgeproc`, and the command is also `edgeproc`. The heavy parts are optional extras:

```bash
pip install edge-proc                    # the command-line tool and the task router
pip install "edge-proc[bundles]"         # + publish and sync (signing, checking, small updates)
pip install "edge-proc[localvec]"        # + on-device search (FAISS and PyTorch, about 950 MB)
pip install "edge-proc[localvec,bundles]"
```

This README documents EdgeProc 0.5.0. It needs Python 3.13 or newer and installs
`edgeproc-core>=0.4.3` from PyPI. Release notes are in the [CHANGELOG](CHANGELOG.md).

## Develop

```bash
git clone https://github.com/hseshadr/edge-proc.git
cd edge-proc
uv sync --all-extras
uv run poe gate
```

The last command runs lint, type checks, the complexity limit, and the full test suite. It
took about 3.5 minutes. [Getting started for developers](docs/GETTING_STARTED.md) covers
setup, a map of the code, a first change, and how to open a pull request.

## More detail

- [Getting started for developers](docs/GETTING_STARTED.md): from a fresh clone to a green
  local build and your first change.
- [Full walkthrough](docs/QUICKSTART.md): build a search index, ship it with its model, and
  search it on the device, step by step. Also shows the Python API.
- [Architecture](docs/ARCHITECTURE.md): the modules, the bundle format, the security model,
  key rotation, and what the tests prove.
- [Explore the interactive architecture map](docs/architecture/index.html): the same, as a
  clickable diagram that works offline.
- [Configuration](docs/CONFIGURATION.md): every setting and environment variable.
- [Operations](docs/OPERATIONS.md): threat model, privacy and data flow, recovery, the key
  rotation runbook, measured performance, and how releases are made.
- [Roadmap](ROADMAP.md): what is planned and not built yet.
- [CHANGELOG](CHANGELOG.md): what changed in each release.
- [CONTRIBUTING](CONTRIBUTING.md) and [SECURITY](SECURITY.md): how to contribute and how to
  report a vulnerability privately.

## License

MIT. See [LICENSE](LICENSE). To cite EdgeProc, use [CITATION.cff](CITATION.cff).

EdgeProc (also written `edge-proc` and `edgeproc`) lives at
[hseshadr/edge-proc](https://github.com/hseshadr/edge-proc), with a project page at
[edge-reco.com/edgeproc](https://edge-reco.com/edgeproc). It is not affiliated with any other
product or company named "EdgeProc".
