# P150：冻结 Wiki HTML grid 上的数值区间行扫描

P148 的 26 个合法 grid 实际来自 7 个页面，其中 `List of strikes` 独占 14 表；按表数宣称来源多样性会失真。P150 在这批**不改动的冻结 grid**上增加一条 parser-backed 操作：在指定 `Dead`/`Injured`/`Year`/`Date` 列，计算包含在闭区间内的纯整数行数，同时返回全部合格纯整数行数。空值、范围、注释、近似数字和带脚注数字不计；年份必须是单独的四位数。对于伤亡列，单位仅声明为“表中整数”，不把限定的死亡/受伤人数解释升级成无条件现实真值。

编译器将表头、每一行的原 HTML cell span/hash 与规范化 reader span/token span绑定；按最终 reader 的表格程序独立计算 gold。准入要求至少 10 行纯整数、至少 3 个命中和 3 个未命中，并保持非退化上限。每题对 reader 正文做三种有界干预：未命中改成命中（答案 +1）、命中改为未命中（答案 -1）、未命中改为另一个未命中（答案不变）。这验证了所选行值参与当前完整表格计数；表外替代证据和自然语言最短证明未穷尽。

| 项目 | 当前冻结结果 |
| --- | ---: |
| P148 原始/合法 grid | 73/26 |
| P150 可支持数值操作的 grid | 8 |
| 合格任务 / 独立来源页 | 7 / **4** |
| train/eval | 6/1 |
| 任务操作 | 整数伤亡区间 4、四位年份区间 3 |
| 全部被检查表格行 | 556 |
| 完整 chat / 监督 token | 80,531 / 99 |
| 最短/最长完整 chat | 694 / 29,168 token |

两个 school attack 页面各产出 2 题，strikes 页面 2 题，motu proprios 页面 1 题（eval）。只有一个路由 `domain=wiki_list_index`；`lists_of_latin_phrases` 之类 topic 来自自动类别路径，不是 motu proprio 页的可靠语义标签，训练筛选时不应把它当成新的真实 domain。7 题都在 `<32K`，其中一个含 170 行的真实表格接近 29K，但没有 32K 以上或跨文档长距能力证据。

来源输出为 `data/candidates/p150_wiki_numeric_interval_v1/`，manifest SHA `3638bf230fe9f5a5e9546c7321869222dc78606cbf9db55488b7809f15703a01`，冻结来源池为 `data/candidates/p148_wiki_category_catalog_v1/p119/source_pool.json`（SHA `619841dde94341a5408a9c353cb173ae523a960bbb3569b43e8e35d6dad1370d`）。同 P148 P126 的 2 条短表格题相比，P150 的 7 条任务 sample ID/task key 重叠 0；双方正常共用一个 strikes 来源组，split/train、domain、topic 全一致。两套合计 9 题、4 个独立来源组。

编译器完整 `--verify-only` 逐字节重放退出 0；[独立审计](/volume/pt-dev/qjiu/longworld-worlds/data/candidates/p150_wiki_numeric_interval_audit_v1/report.json)（SHA `8eac1040f1bacb652c37d5d1827734ce4721cdc065f5de5b313f276dd66623cd`）再次从最终 reader 与原 HTML 检查 7/7 gold、556/556 行证据、21 个有界编辑、7/7 assistant mask，来源跨 split 0。`train_ready=false`，尚无模型收益证据。

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p150_wiki_numeric_interval.py --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/audit_p150_wiki_numeric_interval.py --output data/candidates/p150_wiki_numeric_interval_audit_v1/report.json
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p150_wiki_numeric_interval.py
cat data/candidates/p150_wiki_numeric_interval_v1/manifest.json
less -R data/candidates/p150_wiki_numeric_interval_v1/decision_ledger.jsonl
less -R data/candidates/p150_wiki_numeric_interval_v1/audit.jsonl
```
