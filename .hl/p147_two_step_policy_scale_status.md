# P147 两批冻结状态世界的两步策略扩量

沿用 P142 两步策略编译器，不改 P133/P144 来源与 P145 reader QA。分别从 P144 的 `batch_000` 和 `batch_001` 冻结 campaign 输入，按 `partial_reversal`/`authorization_hold`、400/800 记录、train/eval 的 8 格，每格尝试 8 个 train 或 3 个 eval 世界；每批 44 个来源世界、4 worker。两个输入 manifest SHA 分别为 `5568a1015c40b5002466df89a1225fa97531322c10483dc0cc680518e6674e89`、`2f19ba2b04511be2d79c2a8d32ba011a2967684043e1b0d2ad2d176d4b55a43e`。配置为 [batch 000](../configs/p147_two_step_p144_batch_000_v1.json) 与 [batch 001](../configs/p147_two_step_p144_batch_001_v1.json)。

| 仅计新 P147 | batch 000 | batch 001 | 合计 |
| --- | ---: | ---: | ---: |
| 尝试/产出来源世界 | 44/37 | 44/37 | 88/74 |
| 完整两步轨迹 | 128 | 122 | 250 |
| 最终 assistant-only 行 | 256 | 244 | 500 |
| train/eval 轨迹 | 92/36 | 90/32 | 182/68 |
| KEEP/CANCEL 首动作 | 64/64 | 61/61 | 125/125 |
| 完整 chat token | 15,164,705 | 14,665,499 | 29,830,204 |
| 监督 token | 2,432 | 2,318 | 4,750 |
| 最短事件→首问 token 间距 | 17,174 | 18,938 | 17,174 |
| 拒绝企图 | 25 | 29 | 54 |

拒绝包括无可使两步决策分化的事件（48）和最终 token 间距不足 16,384（6）；未静默舍弃。两批分别覆盖全部 8 个机制×长度×split 格；第一阶段物理长度跨 29,357–91,391 token，无 128K 以上候选。两个分片逐字节 `--verify-only` 均退出 0。

[跨分片独立审计](/volume/pt-dev/qjiu/longworld-worlds/data/candidates/p147_policy_cross_audit_v1/report.json)将 P142 pilot 与两批 P147 一并从**最终 transcript**重新计算：84 个不重复来源世界、142 个不重复来源任务、284 条不重复轨迹、568 条 stage 行、33,478,420 完整 chat token、5,396 监督 token。284/284 条双分支动作与终局反馈、568/568 条 final assistant mask、事件最终 token offset 重放通过；来源世界/任务/轨迹重叠 0，train/eval 世界重叠 0。合计第一阶段长度桶 <32K 30、32–64K 120、64–128K 134。

每批 `data/candidates/p147_two_step_p144_batch_00{0,1}_v1/` 存放固定 manifest、stage1/2 train/eval reader、样本索引、proof、mask、拒绝账本。复核及逐条查看：

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p142_two_step_agentic.py --config configs/p147_two_step_p144_batch_000_v1.json --output data/candidates/p147_two_step_p144_batch_000_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p142_two_step_agentic.py --config configs/p147_two_step_p144_batch_001_v1.json --output data/candidates/p147_two_step_p144_batch_001_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/audit_p147_policy_shards.py --output data/candidates/p147_policy_cross_audit_v1/report.json
less -R data/candidates/p147_two_step_p144_batch_000_v1/trajectory_proofs.jsonl
less -R data/candidates/p147_two_step_p144_batch_001_v1/attempt_ledger.jsonl
```

**范围边界：**仍是同一记录 grammar 上的两种状态机制与二步余额策略，不是多工具 agent 或真实交互环境。第二阶段收到显式余额，不能计为一次独立远程检索；第一阶段的远距事件必要性仅在所选干预范围内成立。`train_ready=false`，P147 未接入 P145 reader bank，也没有 GPU 训练或模型收益证据。虽然形成 250 个新轨迹，监督密度仅约 0.016%，不能凭数量宣称解决长文训练效果。
