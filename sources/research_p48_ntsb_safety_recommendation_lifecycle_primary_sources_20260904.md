# P48 NTSB safety-recommendation lifecycle primary-source register

Observed 2026-09-04 UTC. Only official NTSB hosts were used. HTML, PDF, and
CAROL JSON were downloaded to `/tmp` for inspection and were not committed.
This register records technical provenance and reuse boundaries; it is not a
legal opinion.

## Official documentation

| Source | Official URL | Evidence used | Observed SHA-256 |
| --- | --- | --- | --- |
| CAROL help | https://www.ntsb.gov/Pages/carol.aspx | CAROL covers more than 15,000 NTSB-issued safety recommendations; searches are live database views and query results can be downloaded as JSON or CSV. | `672cadc337f8d9697bdeb6ff93e51dd6a01333e75d909f57a64052276098a048` |
| Field descriptions | https://www.ntsb.gov/investigations/Pages/field-descriptions.aspx | Defines the open and closed recommendation classifications, including awaiting response, acceptable/alternate/unacceptable response, acceptable action, superseded, reconsidered, and no-longer-applicable states. | `bf5c6ecdda7a59d48b6f25395d67f4ca3fe0f2ce775de2a540b3dbf1aae18baa` |
| Recipient guidance | https://www.ntsb.gov/investigations/Pages/RecipientTipRecommendations.aspx | States that a new recommendation starts `Open—Await Response`, asks the recipient to report planned/completed actions, and describes NTSB review and classification. | `549413410dfc766be26868748e32eb72ecae52138c8dc48c98493065e403f8c6` |
| CAROL Guide | https://www.ntsb.gov/Documents/CAROL-Guide.PDF | Documents CAROL search/detail fields and correspondence-history event types. | `e35cfaff88eb588068360f83fbc3d79f4682d9363c08e3022e01b9348298afaa` |
| Website policies | https://www.ntsb.gov/about/Pages/Website-Policies.aspx | NTSB staff-prepared reports, recommendations, and public-docket content are placed in the public domain; separately identified copyrighted/third-party material is not thereby licensed. | `28b3c939a77af673a81932d8e3f059fcd8876f23fd6298043975ad6aa3e1488b` |

The CAROL help page explicitly says its saved searches are dynamic. The page
hashes above are observation receipts, not immutable publication identifiers.
Any future conversion must preserve a fresh source receipt and must not silently
substitute a changed page or record bundle.

## Frozen detail cohort

The official CAROL detail application exposes one public JSON record per safety
recommendation at:

```text
https://data.ntsb.gov/carol-main-public/api/Query/GetSrRecord/{recommendation_id}
```

The endpoint is the backing source for the official public detail view, but it
is not presented as a versioned bulk API. That makes it usable for a hash-frozen
preflight snapshot, not as an immutable historical archive.

The P48 config freezes 125 sorted recommendation IDs: 25 each from Aviation,
Highway, Marine, Pipeline, and Railroad. Concatenating
`id:sha256(raw-json)\n` in that exact ID order gives bundle SHA-256:

```text
85d7b92adf2a3880b8aadc99cb8cfef5da1cc71756633b5d2be3d826752e5ec4
```

The 125 responses total 4,946,787 bytes. The reproducer validates HTTPS host,
endpoint path, response size, record ID, status lookup, correspondence direction,
timezone-aware dates, and the aggregate bundle digest before measuring anything.
It persists only aggregate counts and public recommendation IDs for rejected
records. Raw JSON, response summaries, addressee names, correspondence IDs, and
contact strings remain out of the repository.

The enhanced CAROL search service uses a client subscription key in the public
front-end. That key was used only ephemerally to discover the cohort and is not
required by the frozen detail-record reproducer. It is not recorded here, in the
config, or in any report.

## Rights and privacy boundary

The public-domain statement applies to material prepared by NTSB staff. It does
not justify treating recipient-authored correspondence, third-party quotations,
or personal contact information as public-domain training text. Accordingly:

- recipient-authored `ResponseSummary` text is excluded from the conservative
  capacity result;
- paragraphs with detected email addresses, telephone numbers, or an explicit
  `Mr./Ms./Mrs./Miss/Dr. Surname` pattern are excluded;
- addressee names and correspondence identifiers are never serialized;
- the remaining text is **not privacy-cleared**, because the narrow scanner does
  not detect every personal name or every embedded third-party quotation.

This is why the source and capacity preflight can pass while candidate generation
remains fail-closed. A future transform needs a deterministic entity/contact
redactor, a third-party quotation audit, and an independently reviewed sample
before any text leaves the preflight boundary.

## Lifecycle and machine-verification boundary

Each executable record must have all of the following source-observed facts:

1. a current closed status and closure date;
2. at least one recipient-to-NTSB response event;
3. a later NTSB-authored correspondence event;
4. an explicit canonical spelling of the current status in that later NTSB text.

The fourth condition prevents a model or data builder from inferring a closure
classification that the retained NTSB-authored text does not state. Five of the
125 records fail these conditions and are excluded, rather than repaired or
padded. Historical state labels are extracted only when explicitly present in
NTSB-authored correspondence; current database fields are not back-projected as
unobserved historical state.

No claim is made that CAROL's current JSON response reconstructs every database
revision. A future candidate freeze must bind all retained event text to the
observed bundle hash and must fail if the mutable endpoint changes.
