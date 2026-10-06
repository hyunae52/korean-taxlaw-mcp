# Fork maintenance

This fork keeps reviewed TaxLab-specific fixes while following
`zisu17/korean-taxlaw-mcp:main`.

TaxLab production and public installation instructions use this fork, not a
moving upstream branch. The package version `2.1.0.post1` identifies the reviewed
downstream release based on upstream `v2.1.0`; the release tag is
`taxlab-v2.1.0.post1`. Future upstream releases receive a new reviewed
downstream version and tag before activation.

The machine-readable mapping is [`.github/taxlab-release.json`](../.github/taxlab-release.json).
The existing `Tests` workflow checks all three package version declarations and runs
the release checker's regression suite. Before merging a release candidate, run
`python .github/scripts/check_release_version.py` in a checkout with full history and
tags to verify the upstream commit/version and existing release-tag runtime contents.
The CI metadata check alone is not release approval. Documentation and test-only
changes do not create a new deployed package. Follow the
[release version procedure](RELEASE_VERSIONING.md), and keep the release history in
[GitHub Releases](https://github.com/hyunae52/korean-taxlaw-mcp/releases).

As authorized on 2026-10-07, `Automatic upstream sync` checks upstream daily at
02:10 KST (GitHub schedules may be delayed). It imports runtime/package files and
upstream tests from an immutable zisu17 main commit, preserving fork workflows,
policy documents and fork-only tests. An unexpected local runtime patch, changed
license, rewritten upstream history, conflicting test or version downgrade stops
the sync for review. It does not overwrite such changes.

The candidate runs full-history release verification and locked offline tests on
Python 3.11 and 3.13 in read-only jobs. After both pass, a separate trusted job
records a PR, atomically fast-forwards unchanged main to the exact tested commit,
and publishes an immutable `taxlab-v` release. Same-version runtime patches bump
`.postN`; a new upstream version starts `.post1`. Missing release publication is
retried on the next run. Existing tags are never moved. General correction PRs
are outside this automation.

The Legal Harness separately follows published releases at 02:40 KST, updates
its immutable pin and hash, runs integration gates and verifies actual retrieval
on GCE before activation. Its existing 03:30 KST email includes sync/deployment
failures. No personal GitHub token is stored in the workflows or on GCE.

If an equivalent downstream fix is accepted upstream, retain the regression
test, remove only the redundant patch after comparison, and publish a new
downstream release. Upstream activity is helpful but is not an availability or
support commitment for TaxLab.

## v2.1.0 synchronization (2026-10-01)

Upstream commit `a91872fed2c12cd51fffdc4c2dbbfcabe997262b` includes our
document-number and special-source contributions, plus pagination, unified
search routing, date validation, and response-consistency fixes. All upstream
implementation and tests are retained; the only runtime source difference is
the downstream version. The dependency lock changes only the project version.

Keep the fork's scheduled candidate workflow, security and data notices, and
fork installation URLs. Merge this synchronization with a merge commit so
upstream remains an ancestor and the daily check does not rediscover the same
release. A conflict in a future upstream merge stops candidate preparation;
it requires a reviewed resolution, not an automatic overwrite.
