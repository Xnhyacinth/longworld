# P148：官方类别索引自动路由真实 Wiki 来源

P148 不手填 12 个根目录或 domain 名称。从 MediaWiki 官方 `Category:Lists` 的直接子类与下一层子类读取目录，再批量查 `categoryinfo.pages`，筛选 `Category:Lists of …` 且至少有 8 个直接页面的类别；排除 P119 先前根目录，跨父类确定性采样。12 个根分属 12 个父类，三个根路由 eval，其余 train。所有来源的 `domain=wiki_list_index` 只是路由标签，不算新增语义 domain。根目录、API 请求/响应原文及 SHA、生成的 P119 配置，均固定在 `data/candidates/p148_wiki_category_catalog_v1/`。

来源链完整运行：12 根→165 条 P119 目录发现行→22 个去重后标题→22 个 oldid 快照→22 页 HTML→73 个 gross wikitable→26 个合法 HTML grid→2 个 P126 任务，来自**一个**来源世界。P119 wikitext 简单表格门禁在这 22 页上仍为 0；HTML 结构解析才获得 26 个 grid。P126 拒绝计数：`key_cell_ambiguous_or_qualified` 14、`no_semantically_legal_key_target_pair` 25、`target_cell_missing_multiline_spanned_or_qualified` 5。这些是候选规则的累计拒绝次数，不以 26 为分母相加。

目录阶段 33 个官方索引分支、243 个 gross 候选根、175 个满足直接页数阈值的根，40 次 API 尝试并保留全部请求/响应 SHA。P119 发现和逐页冻结阶段 122 次 API 尝试，22/22 页冻结成功；`fetch_ledger.jsonl`、每页 `.freeze-log.json`、P119 的 `source_ledger.jsonl` 和 `shape_ledger.jsonl` 保留来源、去重、revision 与形状判断。P122 在 0.5 请求/秒上限下取得 exact-oldid HTML，每页 HTML 与 receipt 绑定 oldid、页面、字节数及 SHA。

两个 task 均为 `real_wiki`、`wiki_list_index`、`lists_of_strike_actions`，train 2/eval 0，长度桶 `<32K`：每条输入 748 token，合计完整 chat 1,600 token、监督 104 token。与 P149 已有候选的 sample ID 和 source group 重叠均为 0。它们是短结构化表格完整集合题，不能证明自然长文依赖或类别广度带来的多能力监督，因此保持独立候选，未并入 P149。

固定配置：[configs/p148_wiki_category_catalog_v1.json](../configs/p148_wiki_category_catalog_v1.json)；编译器：[scripts/p148_wiki_category_catalog.py](../scripts/p148_wiki_category_catalog.py)。`--phase verify` 对类别响应、P119 快照/来源池/形状账本、P122 HTML/grid、P126 gold/reader/mask 做离线逐字节重放，退出 0。所有产物 `train_ready=false`。

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p148_wiki_category_catalog.py --phase verify
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p148_wiki_category_catalog.py
cat data/candidates/p148_wiki_category_catalog_v1/catalog_manifest.json
cat data/candidates/p148_wiki_category_catalog_v1/manifest.json
less -R data/candidates/p148_wiki_category_catalog_v1/fetch_ledger.jsonl
less -R data/candidates/p148_wiki_category_catalog_v1/p119/shape_ledger.jsonl
less -R data/candidates/p148_wiki_category_catalog_v1/p126_tasks/sample_index.jsonl
```

P148 的主要实验结论是**来源发现可自动扩量，但现有 P126 操作在这些来源上的合格任务产率只有 2/22 页**。后续应该在冻结 grid 上分析列类型，并只对有明确行/列/单位证据的数值或关系任务增加新的通用操作；更换类别标签或填充长度不能修正这个瓶颈。
