# Security Policy

EdgeProc is a **signing- and verification-adjacent** library: it mints trust-root keys,
signs version pointers, and verifies content-addressed bundles fail-closed before promoting
them. We treat cryptographic and supply-chain issues with the seriousness that posture
demands. If a verification, signature, content-addressing, or trust-pinning path can be
bypassed, weakened, or made to silently accept tampered data, that is a high-severity bug.

## Reporting a vulnerability

**Please do not open a public GitHub issue, pull request, or discussion for a security
vulnerability** — that discloses it before a fix exists.

Instead, report it privately by email to:

> **harish.seshadri@gmail.com**

Please include, as far as you can:

- A description of the issue and the impact (what a malicious publisher / CDN / network
  attacker could achieve).
- The affected version(s) or commit.
- Steps to reproduce, or a minimal proof of concept.
- Any suggested remediation.

You can expect an acknowledgement within a few days. We will work with you to confirm the
issue, develop a fix, and coordinate disclosure. We are happy to credit you in the release
notes unless you prefer to remain anonymous.

## Supported versions

EdgeProc is pre-1.0 (beta). Security fixes land on `main` and ship in the next release. We
support the **latest released version**; please upgrade to it before reporting, in case the
issue is already fixed.

| Version | Supported          |
| ------- | ------------------ |
| 0.5.1   | :white_check_mark: |
| < 0.5.1 | :x:                |

## Scope notes

- The trust model is **pinned-trust-root, fail-closed**: a consumer trusts only the key —
  or keyring of keys — it pins out-of-band; `sync` refuses to run without one. Reports that
  demonstrate trust without a pinned root, signature acceptance after tampering, a revoked
  key's signature being accepted, an expired pointer being promoted, or content-address
  bypass are in scope and prioritized.
- Key rotation and a compromised signing key are handled by the runbook in
  [docs/OPERATIONS.md](docs/OPERATIONS.md#key-rotation-and-compromised-key-runbook). The
  trust root is a raw Ed25519 `public.key` (a keyring of one) or a JSON keyring: pointers
  may name their signing `key_id`, a revoked key never verifies at any `sequence`, and a
  signed `expires_at` bounds how long a withheld pointer is accepted. Rotation is an
  overlap window (pin `{old, new}`, switch the publisher, pin `{new, revoked old}`), and
  the pointer `sequence` keeps increasing across it so the anti-rollback floor holds.
  Revocation takes effect when a consumer's pinned keyring is updated; there is no remotely
  fetched revocation list, and expiry bounds a freeze, not a key compromise.
- Issues in third-party dependencies (e.g. `cryptography`, `faiss-cpu`) should be reported
  upstream; if EdgeProc's *use* of them is the weakness, report it here.
