# P35 FDA FAERS/AEMS primary-source register (2026-09-03)

Only official FDA/HHS sources were used. No raw individual case row or case
identifier is stored in this file.

| Evidence role | Official source | Admission-relevant evidence |
| --- | --- | --- |
| Current system and non-cumulative extracts | https://www.fda.gov/drugs/fda-adverse-event-monitoring-system-aems/fda-adverse-event-monitoring-system-aems-latest-quarterly-data-files | AEMS is replacing the FAERS name; the listed FAERS extracts are raw, relational, non-cumulative quarterly files with demographic/administrative, drug, reaction, outcome, source, and README content. |
| Quarter inventory and archive | https://fis.fda.gov/extensions/FPD-QDE-FAERS/FPD-QDE-FAERS.html | Official quarter-by-quarter ASCII/XML download inventory; 2013Q3 ASCII is a 22 MB post-2012 case/version release. |
| Schema/version transition | https://www.fda.gov/drugs/fda-adverse-event-monitoring-system-aems/faers-quarterly-data-files-documentation | FDA documents the September 2012 shift from ISR-based legacy extracts to case/version-based FAERS extracts and the corresponding ASCII field changes. |
| Publicly releasable scope and topology | https://open.fda.gov/apis/drug/event/ | The endpoint exposes publicly releasable FAERS records, describes report/product/reaction multiplicity, and explicitly warns that products cannot be connected causally to reactions. |
| Field reference | https://open.fda.gov/fields/drugevent_reference.pdf | Defines report ID/version, latest-version behavior, patient, product, reaction, reporter, date, lot, dose, and other fields used to set exclusions. |
| Privacy boundary | https://www.fda.gov/drugs/fda-adverse-event-monitoring-system-aems/instructions-requesting-individual-case-reports | Case narratives are absent from quarterly files and the public dashboard because narratives may contain identifiers and require redaction before FOIA disclosure. |
| Additional privacy evidence | https://www.fda.gov/drugs/cder-conversations/understanding-cders-postmarket-safety-surveillance-programs-and-public-data | FDA states that narrative and other non-public fields are withheld to protect patient and reporter privacy. |
| System privacy context | https://www.hhs.gov/sites/default/files/fda-cder-fda-adverse-event-reporting-system.pdf | HHS Privacy Impact Assessment confirms the internal FAERS system collects adverse-event information from organizations and individuals; public availability is not a waiver for unrestricted handling of every internal field. |
| General FDA reuse boundary | https://www.fda.gov/about-fda/about-website/website-policies | Unless otherwise noted, FDA website text and graphics are public domain; FDA recommends source credit, retrieval date, and links because content changes. |
| Machine-readable data rights | https://open.fda.gov/terms/ | openFDA data are generally public domain/CC0 unless marked otherwise; third-party copyright exceptions remain possible. This supports an engineering reuse gate, not a formal legal opinion for every source submission. |
| Latest-version semantics | https://open.fda.gov/apis/drug/event/searchable-fields/ | Lists `safetyreportid` and `safetyreportversion`; the reference states openFDA exposes only the most recent version while QDE can contain versions. |
| Responsible-use limitations | https://open.fda.gov/apis/drug/event/ | Reports are not extensively verified; they cannot establish causality or incidence and should not drive clinical conclusions. |

## Source comparison and boundary

The quarterly extract and openFDA agree on the public report/product/reaction
topology, but they serve different purposes. The quarterly ASCII package keeps
relational tables and case versions suitable for reconciliation; openFDA
normalizes to JSON and exposes only the latest version. The explicit openFDA
CC0 statement is stronger than the general FDA website policy, so the direct
quarterly archive is treated as an engineering-reuse source with attribution
and source-date/hash binding, not as a blanket legal conclusion about every
possible third-party submission.

The internal FAERS PIA confirms that the source system handles personal data,
whereas FDA's public documentation confirms narratives are withheld. Therefore
the preflight still excludes direct identifiers, demographics, detailed dates,
reporter/manufacturer identifiers, product and clinical terms, dose/lot fields,
and treatment dates. This deliberately leaves only structural categorical data
for capacity testing.
