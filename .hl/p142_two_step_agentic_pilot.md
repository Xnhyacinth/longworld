# P142 两步策略候选：冻结 P133 状态世界上的因果轨迹

P142 使用 P133 v4 的已冻结状态世界，独立编译两步模拟策略候选。每条轨迹先在同一条可见 `partial_reversal` 或 `hold` 事件上选择 `KEEP_EVENT`/`CANCEL_EVENT`；环境执行并返回所选范围的余额；随后选择 `ADD_500`/`REMOVE_500`，终局反馈为距目标余额的负绝对距离。两个首动作分支及两个次动作都实际执行；只有首动作造成不同余额、使最优次动作翻转，且终局反馈有唯一最优首动作时才准入。这不是将两条独立单步题串起来。

固定配置：[configs/p142_two_step_agentic_pilot_v1.json](../configs/p142_two_step_agentic_pilot_v1.json)。源 P133 v4 manifest SHA 为 `36c60c383f9d2297c47c9f866bff047d51d6e310f319fa5d772664beda871e79`。编译器接受任一具有相同 P133 输出 schema 的冻结批次 manifest 与其 SHA；机制、记录长度格、每格 train/eval 世界数量、距离阈值和 worker 数由配置控制。更换来源需创建新的配置与输出目录，不改写本次结果。每个世界的原始文件 SHA 和 receipt 在 worker 中核对，split 继承世界 seed，世界不可跨 train/eval。

首轮用 4 个 `mechanism × length_records` 格，每格 2 个 train 与 1 个 eval 来源世界，4 个 worker。12 个世界、42 个轨迹企图，10 个世界准入 34 条完整轨迹；8 个被拒企图分别是事件距首次问题不足 16,384 token（2）和无使两步决策分化的事件（6）。准入轨迹按机制为 partial reversal 12、authorization hold 22，train 22/eval 12，首动作 KEEP/CANCEL 各 17。每条轨迹分别产生第一与第二个 final-assistant-only 训练候选，共 68 行；第二行包含第一动作和真实环境观察，但只监督最后的次动作。最终完整 chat token 共 3,648,216，监督 token 646；第一行长度 29,718–85,297，第二行 29,766–85,346。第一行中事件到问题的实际 token 间距为 21,417–78,191。物理长度仅按第一行统计：<32K 8、32–64K 12、64–128K 14。

`data/candidates/p142_two_step_agentic_pilot_v1/` 保留 stage1/2 train/eval reader、逐行索引、轨迹 proof、mask 审计、尝试账本和 SHA manifest。编译后同一代码完整 byte replay `--verify-only` 退出 0；独立的最终 transcript 审计再次从 P133 原始世界执行 34/34 两首动作分支、68/68 final assistant mask，并核对 10/10 世界不跨 split，结果为 `independent_final_transcript_replay_passed`。相关命令：

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p142_two_step_agentic.py --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/audit_p142_two_step_agentic.py
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p142_two_step_agentic.py
less -R data/candidates/p142_two_step_agentic_pilot_v1/trajectory_proofs.jsonl
less -R data/candidates/p142_two_step_agentic_pilot_v1/attempt_ledger.jsonl
```

这是**单一控制规则、两步、全信息已可见**的模拟策略候选。第一动作需要查远端事件并比较两个分支；第二动作收到显式余额观察，因此第二步本身不是长文检索证明。事件距离是选择性证据间距，不是全部替代证明的最短下界。当前不具有复杂工具调用、未知环境信息、多轮记忆或真实 agent 任务的证据；`train_ready=false`，与 reader QA 分开，未运行训练或测得模型收益。
