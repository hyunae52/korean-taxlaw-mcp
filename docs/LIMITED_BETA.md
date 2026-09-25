# Limited beta scope

The supported beta build is the Hyunae downstream release identified by both a
package version and a `taxlab-v*` Git tag. Installation instructions must not
silently substitute `zisu17/main` or an unreviewed branch.

## Guarantees

- Exact document-number matching never treats a partial match as the requested
  document.
- Duplicate exact numbers return an ambiguity error with bounded candidate
  metadata: document type and number, title, tax type, registration date,
  summary, and `ntstDcmId` when the provider supplies them.
- A context query selects a document only when it narrows the known candidates
  to one ID. Otherwise ambiguity remains explicit.
- Every result identifies the original data source and provides a route back to
  the source document.

## Limits

- Candidate counts cover the bounded provider response inspected by the tool;
  they are not a representation that every historical provider page was
  enumerated.
- Candidate `registrationDate` is not renamed to production date. A selected
  detail response can separately contain `productionDate`.
- Provider availability, undocumented response shapes, and source-site changes
  can interrupt retrieval.
- The tool retrieves and structures source material. It does not provide tax
  advice or certify legal completeness, currency, or authority.

Use only public, synthetic, or irreversibly redacted queries. Do not place
taxpayer identifiers, credentials, unpublished client facts, or privileged
material in search terms, logs, issues, or correction proposals.

The code is MIT licensed. Source data remains subject to its provider's terms,
attribution, and legal status. Test fixtures are not a reusable dataset and are
excluded from the installed wheel.
