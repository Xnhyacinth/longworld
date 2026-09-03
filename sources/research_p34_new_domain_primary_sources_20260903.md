# P34 primary-source registry (2026-09-03)

## Research question and constraints

Which of three underrepresented real-work settings can support an authentic,
executable LongWorld state transition with enough non-duplicated source material
for 64K and 128K contexts: structured table work, multi-actor review work, or
claim-source correction research?

The comparison requires official or primary sources, explicit artifact
relations, a deterministic oracle, an answer-changing counterfactual, frozen
source bytes, and a defensible privacy/license boundary. Static QA, prompt-only
category changes, synthetic debate, padding, cloned views, and relaxed gates are
out of scope. Capacity for unselected routes remains an unverified hypothesis.

## Structured table/data work: FDA AEMS/FAERS quarterly extracts

- [FDA AEMS latest quarterly data files](https://www.fda.gov/drugs/fda-adverse-event-monitoring-system-aems/fda-adverse-event-monitoring-system-aems-latest-quarterly-data-files)
  states that quarterly files are non-cumulative and provide separate
  demographic/administrative, drug, reaction, outcome, report-source, and
  README artifacts in ASCII or SGML. It explicitly expects relational-database
  use. Access is public through the linked FDA export service.
- [FDA quarterly data documentation](https://www.fda.gov/drugs/fda-adverse-event-monitoring-system-aems/faers-quarterly-data-files-documentation)
  records the 2012 change from ISR-based records to case/versioning and lists
  separate drug, indication, outcome, reaction, report-source, and therapy
  tables. This supports authentic case-version and foreign-key relations rather
  than a fabricated spreadsheet join.
- [FDA website policy](https://www.fda.gov/about-fda/about-website/website-policies)
  says FDA website text and graphics are public domain unless otherwise noted,
  while recommending dated copies because the source is updated.
- [FDA FAERS FAQ](https://fis.fda.gov/extensions/FPD-FAQ-OCAC/FPD-FAQ-OCAC.html)
  says the released dataset does not contain PII and is HIPAA compliant, but
  also warns of duplicate reports, incomplete reporting, and the absence of a
  proven product-event causal relation. Therefore a valid oracle may reconcile
  keys/versions and counts, but must not infer incidence or drug causality.

Assessment: strong multi-table structure and likely ample capacity, but health
data sensitivity, case-version de-duplication, and a field-level redistribution
review are required before fetch. No P34 capacity claim was made for this route.

## Agent/multi-actor work: public Gerrit review disposition

- [Gerrit `/changes/` REST API](https://gerrit-review.googlesource.com/Documentation/rest-api-changes.html)
  exposes all revisions, commits, files, messages, review comments, reviewer
  updates, labels, submit requirements, related changes, submitted-together
  changes, and final status. Anonymous queries are supported where an instance
  makes a change public.
- [AOSP contribution licenses](https://source.android.com/docs/setup/contribute/licenses)
  documents Apache 2.0 as the preferred Android userspace software license and
  contribution agreements for submitted code. This does not by itself prove
  that review discussion is redistributable under the repository's code
  license.
- [GitHub pull-review API](https://docs.github.com/en/rest/pulls/reviews) is a
  technically viable fallback and returns chronological reviews with a bound
  commit ID, but [GitHub's current terms](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service)
  distinguish public viewing/forking rights from broader user-content rights.
  Repository code licensing cannot be assumed to cover every issue or review
  comment without an inbound-license check.

Assessment: the strongest interaction topology—patch-set revision, requested
change, disposition, vote, submit requirement, merge decision—but the least
clear redistribution boundary. Detailed account fields and email addresses must
not be fetched; account IDs would need deterministic pseudonymization. Capacity
was not measured because rights review is an earlier gate.

## Retrieval/deep-research work: EPA PFAS proposal/final/correction dossier

- [FederalRegister.gov API documentation](https://www.federalregister.gov/developers/documentation/api/v1)
  says its public API needs no key. The same page warns that its XML rendering is
  not the official legal edition and directs legal verification to GovInfo.
- [GovInfo policy](https://www.govinfo.gov/about/policies) describes permanent
  public access and the general 17 U.S.C. 105 public-domain rule for U.S.
  Government works, but explicitly warns that government publications can
  contain third-party copyrighted material.
- Official proposal PDF: [2023-05471](https://www.govinfo.gov/content/pkg/FR-2023-03-29/pdf/2023-05471.pdf).
- Official final-rule PDF: [2024-07773](https://www.govinfo.gov/content/pkg/FR-2024-04-26/pdf/2024-07773.pdf).
- Official correction PDF: [2024-12645](https://www.govinfo.gov/content/pkg/FR-2024-06-11/pdf/2024-12645.pdf).
- The corresponding Federal Register metadata records share docket
  `EPA-HQ-OW-2022-0114` and RIN `2040-AG18`. The correction explicitly names FR
  Doc. 2024-07773, replaces the obsolete `141.61(c)(34)-(40)` effective-date
  reference with `141.61(c)(2)(i)-(vii)`, and is effective on the same date as
  the final rule.

Assessment: selected for measurement because the relation and counterfactual
are explicit in official records, the oracle is exact rather than judged, no
API key or participant content is required, and official GovInfo bytes can be
frozen. Docket comments, CBI, incorporated standards, and contact blocks remain
excluded.

## Contradictions and uncertainty

- FederalRegister.gov is easier to parse but is not the official legal edition;
  the P34 measurement therefore binds metadata to hashed GovInfo PDFs and uses
  extracted text only as a derived, non-authoritative view.
- FDA says FAERS contains no PII, but it remains sensitive health-event data and
  cannot support causal safety conclusions. “No PII” is not a blanket ethical
  or quality clearance.
- Gerrit/GitHub APIs expose ideal review state, but public accessibility and a
  repository code license do not automatically settle downstream rights for
  contributor discussion. This remains a blocking uncertainty, not a minor
  caveat.
