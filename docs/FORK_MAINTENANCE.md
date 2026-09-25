# Fork maintenance

This fork keeps reviewed TaxLab-specific fixes while following
`zisu17/korean-taxlaw-mcp:main`.

TaxLab production and public installation instructions use this fork, not a
moving upstream branch. The package version `2.0.0.post1` identifies the first
downstream release based on upstream `v2.0.0`; the release tag is
`taxlab-v2.0.0.post1`. Future upstream releases receive a new reviewed
downstream version and tag before activation.

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
