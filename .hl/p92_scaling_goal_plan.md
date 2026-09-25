# P92 scaling goal and execution plan

Goal: replace manual one-source additions with a reusable, evidence-first
source/world × legal task × length compiler, and demonstrate it on a bounded
multi-process batch. This is an active synthesis goal, not a claim that the
whole long-context training program is complete.

Success for this wave:

1. Inventory the current bank by **source type, source/world identity,
   operation, capability, actual length, evidence/dependency status and mask**.
   Report missing natural reports, books, general QA and agentic feedback
   honestly. Baseline: `.hl/p92_current_data_taxonomy.md`.
2. Build a reusable source capability router over frozen inputs. It must
   distinguish native parser support from mere labels or prior candidate
   observations, record source hashes/splits and every unsupported cell.
3. Build a declarative legal-cell scheduler with stable job identities,
   bounded process concurrency, resumable receipts and explicit world/task/
   length dimensions. It must invoke existing compilers, not synthesize gold
   labels by replacing domain nouns.
4. Sweep real frozen tables with a conservative schema-driven L2 recipe; log
   every rejected page/table and verify admitted final reader text, answer,
   intervention, actual tokenizer length and assistant mask.
5. Execute and filter a sizable multi-process candidate batch; report
   candidate views, independent semantic tasks, source/world groups,
   multi-operation groups, domain/topic labels, actual length bins, source
   types, rejection reasons, throughput and supervised-token distribution.
   Keep source/world-level train/eval separation.
6. Integrate accepted native readers through the existing candidate contract
   and immutable sharded reference index. Rebuild deterministically, run
   relevant tests and final-reader mask checks, and leave a reviewable
   manifest and case paths. Do not promote `train_ready` or start GPU training
   based on candidate counts alone.

Scaling claim boundary: domain/topic vocabulary substitution can increase
surface diversity for a fixed valid mechanism, as MRCR illustrates, but it
does not create new source semantics, relations or long-document necessity.
The reported scaling unit is an admitted, independent task backed by a
frozen source/world and verified reader bytes. Report source acquisition,
mechanism diversity and task/length rendering as separate axes.
