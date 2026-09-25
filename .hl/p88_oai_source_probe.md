# P88 official OAI-PMH source probe

arXiv's [current OAI documentation](https://info.arxiv.org/help/oa/index.html) lists `https://oaipmh.arxiv.org/oai` as the base URL and says `arXivRaw` includes version history while arXiv metadata provides license information. The P88 probe makes at most two requests, sequentially and at least 3.2 seconds apart, under the [API terms](https://info.arxiv.org/help/api/tou.html). Its planned second request would fetch one source archive for local research and record its hash and parsed text capacity; one archive cannot establish revision-pair task capacity.

The live OAI `ListRecords` request returned **HTTP 406** with an empty response body. The probe stopped after **one** request. The receipt at `data/capability_records/p88_oai_source_probe_v1/manifest.json` records zero metadata records, zero source archives, no selected work, and `train_ready=false`. This corroborates P87's separate legacy API 406 but does not identify the cause of either failure. There is no new source, domain, or task count from P88.

Offline fixture tests cover `arXivRaw` version and license parsing, work-level exclusion, source archive hashing/text parsing, the two-request cap and pacing, and fail-closed behavior. Two tests passed; Ruff check and format passed.

```bash
cat data/capability_records/p88_oai_source_probe_v1/manifest.json
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_p88_oai_source_probe.py
```
