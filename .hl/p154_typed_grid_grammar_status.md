# P154：冻结真实 HTML grid 上的类型化数值任务语法

P154 从 P131 已冻结的 144 个 Wikipedia HTML grid 读取行、列、原始 HTML cell span/hash 与来源快照。任务由表头类型（calendar year 或 capacity integer）、闭区间和操作（完整行计数或条件求和）组合；domain/topic 标签不控制任务代码，也不被当成新增能力。每一条问题都从最终 reader 表格重新执行得到答案，并对命中、移除、未命中对照做正文单元格编辑；条件求和另做区间内值变动，验证答案需要数值本身而非仅需成员数量。

来源门禁接受单独可解析的四位年份或正整数容量（逗号只能作千分位），排除缺失、范围、近似、注释、脚注以及隐藏或跨行/跨列数字。若一个数值列存在表面纯数字但原 HTML 支持含糊，该列整体拒绝，以免 reader 的清洁表格误把它计入。审核保留所有显示行及其列、原 HTML cell 哈希、reader 字符/token 位置。此处的“完整”仅指当前展示的表格行，不保证整页其他表格没有替代答案。

| 冻结结果 | 数值 |
| --- | ---: |
| 输入 grid / 合格独立任务 | 144 / 114 |
| 真实来源页面（source group） | 21 |
| 区间计数 / 条件求和 | 67 / 47 |
| train / eval | 22 / 92 |
| domain | aviation 8、energy 22、sports 84 |
| topic | lists_of_airports 8、power_stations 4、power_stations_by_country 18、stadiums 84 |
| 完整 chat / 监督 token | 457,919 / 1,644 |
| 单题完整 chat min / median / max | 528 / 2,052.5 / 21,575 |
| 首个数值证据至问题 min / median / max | 362 / 1,891 / 21,372 token |
| 实际长度 | 114/114 均 `<32K` |

P154 的求和是新操作；区间计数与 P150 的同类机制共享计算形式，但在 P131 的不同冻结表格上生成不同独立任务。114 条不是 114 种机制，也不是 21 个新 domain。当前来源结构限制了可用表头，只覆盖 3 个既有 domain，且 eval 明显多于 train，不能直接按原分布投入训练。长距离最大 21,372 token 是首个行证据到问题的位置，不是所有证明的最短距离，也不证明模型一定使用该行。

输出 `data/candidates/p154_typed_grid_grammar_v1/` 的 manifest SHA 为 `de0764c87d9d6e47bfde17b44286c4c49ed2e468e2552e9eaab706258b295aad`；输入 P131 campaign SHA `0a1c207092bd983650bcb1b34522c8352990ee2d918a5ac11f51ab8f01123bc6`。独立审核 `data/candidates/p154_typed_grid_grammar_audit_v1/report.json`（SHA `88d83d9c1056940c27214b92f3694598db00da739dbaae285f57ef7d9bf602d6`）从最后 reader 重算 114/114 答案、6,442/6,442 表格行、可见正文编辑、HTML cell、token span 和 assistant-only mask；来源组跨 split 为 0。编译器完整逐字节 `--verify-only` 重放、相关 7 个测试、Ruff 均通过。`train_ready=false`；没有模型增益或长于 32K 的结果。

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p154_typed_grid_grammar.py --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/audit_p154_typed_grid_grammar.py --input data/candidates/p154_typed_grid_grammar_v1
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p154_typed_grid_grammar.py tests/test_p150_wiki_numeric_interval.py
cat data/candidates/p154_typed_grid_grammar_v1/manifest.json
less -R data/candidates/p154_typed_grid_grammar_v1/decision_ledger.jsonl
less -R data/candidates/p154_typed_grid_grammar_v1/audit.jsonl
```
