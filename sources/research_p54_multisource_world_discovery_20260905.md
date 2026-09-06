# P54 multi-source world discovery source register

Observed 2026-09-05 UTC. This register records the official entry points and
bounded anonymous probes used to choose a P54 aggregate-only preflight. The
preferred `parallel-cli` research path was unavailable in this checkout, so the
fallback was direct HTTPS retrieval plus browser search of official domains.
No fetched legal-document body is stored in the repository.

## NTSB investigation lifecycle

- Candidate relation chain: investigation -> public docket -> final report ->
  safety recommendation.
- Official CAROL entry point:
  <https://data.ntsb.gov/carol-main-public/basic-search> (HTTP 200).
- Official docket search:
  <https://data.ntsb.gov/Docket/Forms/searchdocket>.
- NTSB data and statistics landing page:
  <https://www.ntsb.gov/safety/data/Pages/Data_Stats.aspx> (HTTP 200). It routes
  public investigation and recommendation discovery through CAROL and other
  interactive search/download surfaces.
- Official reports and recommendation entry points:
  <https://www.ntsb.gov/investigations/AccidentReports> and
  <https://www.ntsb.gov/investigations/Pages/safety-recommendations.aspx>.
- Access verdict: **PARTIAL**. The official pages are public, but this probe did
  not establish an anonymous, documented and bounded API that freezes the
  cross-object investigation/docket/report/recommendation edges. P54 therefore
  does not synthesize or count NTSB candidates.

## NHTSA recall lifecycle

- Candidate relation chain: recall -> manufacturer chronology -> remedy ->
  amendment.
- Official recalls API probe:
  <https://api.nhtsa.gov/recalls/recallsByVehicle?make=tesla&model=model%203&modelYear=2024>
  (HTTP 200; five recall records observed). The response exposed campaign and
  remedy fields, so the discovery surface itself is usable.
- Official dataset/API catalogue:
  <https://www.nhtsa.gov/nhtsa-datasets-and-apis>.
- Official recall bulk-file entry point:
  <https://www.nhtsa.gov/file-downloads?p=nhtsa/downloads/Recalls/>.
- Access verdict: **PARTIAL**. Both document/bulk-download pages returned HTTP
  403 from this environment. The probe therefore could not freeze the
  manufacturer chronology and amendment documents behind a selected campaign.
  P54 does not synthesize or count NHTSA candidates.

## EUR-Lex legislative lifecycle

- Candidate relation chain: Commission proposal -> European Parliament
  first-reading position -> adopted regulation -> corrigendum.
- Official web-service documentation:
  <https://eur-lex.europa.eu/content/help/data-reuse/webservice.html>. The search
  web service requires registration; stable CELEX document representations can
  still be retrieved anonymously from the official legal-content endpoints.
- Official reuse information:
  <https://eur-lex.europa.eu/content/help/data-reuse/reuse-contents-eurlex-details.html?locale=en>.
- Official legal reuse basis selected for a byte-stable rights receipt:
  Decision 2011/833/EU,
  <https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32011D0833>.
  It defines commercial and non-commercial reuse and requires source
  acknowledgement, but its operative scope is Commission documents or
  documents produced on the Commission's behalf. It does not by itself verify
  coverage of every selected EUR-Lex document. A broad official legal-notice
  receipt, attribution, and exclusions review remain required; this is not a
  legal opinion.
- Access verdict: **PASS for source/capacity measurement; PARTIAL for topology
  and rights**. It does not establish candidate or training eligibility.

### Selected official chain and frozen receipts

All receipts below were retrieved with `User-Agent: curl/8.10.1` and
`Accept-Encoding: identity`; each byte length and SHA-256 was reproduced in two
bounded retrievals before being pinned. EUR-Lex injected request-specific
observability markup for application-like user agents, so the preflight pins the
byte-stable curl representation. Raw bodies were processed ephemerally only.

| Role | Identifier | Official representation | Bytes | SHA-256 |
| --- | --- | --- | ---: | --- |
| Proposal | `52012PC0542` | <https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:52012PC0542> | 500720 | `324d7104f7e21e6df7d2c28d66428ac302ea35f2cb0a30345a546092b9683dcd` |
| First-reading position | `52014AP0266` | <https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:52014AP0266> | 1209684 | `f9b43d0f3cb0f5197b1b75ba4661014e828e0183216c0317d5e9c13f79991906` |
| Adopted act | `32017R0745` | <https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0745> | 1709984 | `3ed27dd77a9614fb00350c3031321a43cd3ce62143e9571690d3e219104546a5` |
| Corrigendum | `32017R0745R(01)` | <https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0745R%2801%29> | 20990 | `2f94f5313e3a2928fae84f660d8ea5922bbe230f644ea345c9a5ea4ebaa21fe1` |
| Adopted-act metadata | `32017R0745` | <https://eur-lex.europa.eu/legal-content/EN/TXT/XML/?uri=CELEX:32017R0745> | 1988977 | `6828cefc760cf398c4517a01d76b20bb5f05144455c59f6298c5bd1e0404b99b` |
| Reuse decision | `32011D0833` | <https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32011D0833> | 50410 | `ee6bd2e5bbb4eedc1f2e8118df6a372465e0ab18abc52f6ccfef9d73df5fb370` |

The adopted-act XML contains occurrences of the selected proposal, position,
ordinary legislative procedure `2012/0266/COD`, and a structurally checked
`corrected by` relation to the corrigendum. Only the corrected-by edge is
currently parsed as a relation; the other identifier occurrences do not yet
establish dossier or adoption edges. The corrigendum contains 14 explicit `On
page` correction entries. These facts support source/capacity and role-presence
preflight, not an executable P54 answer oracle.

## Selection decision

EUR-Lex is the only one of the three probes that simultaneously supplied a
publicly retrievable, hash-pinnable multi-document set, one explicit corrected-
by relation, and enough authentic text for a 128K aggregate capacity check. It
is therefore selected for further P54 adapter research, not candidate
generation. Full topology and selected-document reuse coverage remain open.
NTSB and NHTSA remain discovery results only until their document relations can
be frozen without browser-only or access-denied dependencies.
