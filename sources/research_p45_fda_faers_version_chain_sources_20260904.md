# P45 FDA FAERS two-quarter source register (2026-09-04)

P45 reuses the official-source rights/privacy comparison in
`sources/research_p35_fda_faers_primary_sources_20260903.md` and adds no
third-party source.

| Role | Official FDA source | Use |
| --- | --- | --- |
| Quarter index | https://fis.fda.gov/extensions/FPD-QDE-FAERS/FPD-QDE-FAERS.html | Establishes that Q3 and Q4 2013 are adjacent, non-cumulative ASCII releases. |
| 2013Q3 ASCII | https://fis.fda.gov/content/Exports/faers_ascii_2013q3.zip | Earlier observation for real case-version fallback. |
| 2013Q4 ASCII | https://fis.fda.gov/content/Exports/faers_ascii_2013q4.zip | Cutoff observation for latest-at-2013Q4 selection. |
| Case/version schema transition | https://www.fda.gov/drugs/fda-adverse-event-monitoring-system-aems/faers-quarterly-data-files-documentation | FDA explains the post-September-2012 case/version model used by both quarters. |
| Field semantics | https://open.fda.gov/fields/drugevent_reference.pdf | Defines report version, latest-version guidance, and the sensitive fields excluded from P45. |
| Public/reuse/privacy boundaries | https://www.fda.gov/about-fda/about-website/website-policies ; https://open.fda.gov/terms/ ; https://www.fda.gov/drugs/fda-adverse-event-monitoring-system-aems/instructions-requesting-individual-case-reports | Supports engineering reuse of public structure while preserving third-party exceptions and excluding narratives/identifiers. |
| Snapshot invalidation evidence | https://www.fda.gov/downloads/Drugs/GuidanceComplianceRegulatoryInformation/CDERFOIAElectronicReadingRoom/UCM548712.pdf | FDA warns that QDE snapshots can later mark cases inactive because manufacturers delete reports or FDA merges duplicates. |

## Authority and uncertainty

The direct quarterly archives are authoritative for observations present in
each frozen release, while openFDA is useful for field definitions and its
latest-only rule. The two-quarter union can prove an observed version transition
at the 2013Q4 cutoff, but the seven ASCII tables have no explicit deletion or
merge tombstone. Consequently, a `latest observed` program is executable; a
stronger `latest valid active case` claim is not admitted unless inactive-case
semantics are independently sourced and replayed.
