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

The scheduled `Upstream sync candidate` workflow checks upstream daily. When
upstream has commits that are not in this fork's `main`, it creates or refreshes
`automation/upstream-zisu17-main`, runs the locked offline test suite on Python
3.11 and 3.13 with read-only permissions, and opens a pull request only after
both test jobs pass.

The workflow never merges the pull request, publishes a package, changes the
Legal Harness pin, deploys a server, or restarts production. A maintainer must
review and merge the candidate, then update the Legal Harness commit and archive
hash in a separate reviewed change.

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
