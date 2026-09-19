# P71 执行章程(基于 plans/9-rippling-stearns.md 批准版)

Status: active. 2026-09-19 启动。前序:P70 章程已 superseded(G70-4/G70-6 诚实修正见该文件状态行)。

**总目标(P71)**:同一个版本化银行,稳定导出语义可比的主臂/格式臂/机制臂;一致正文/依赖验证;相同训练设置下可直接比较。GPU 训练保持用户门控。

## Track A — 实验完整性

- [x] **A1** L4 结构 split:config `trained_rule_family` + `split_for_rule_family` 接线;调度解耦(cell_index 奇偶替代 seed%2);manifest `split_basis` + structure×split 计数;两侧 depth/target 覆盖断言(测试)。v2 不可变,章程已修正。commit e6df788。
- [x] **A2** sample_index.jsonl:runner finalize 写入 + `build_record_bank_index.py` v2 事后回填(3,476 行,869 group);字面 `group_id`/`full_message_tokens`;group_id=world_id 语义注记;`configs/p70_superset_v2.json` 追溯钉定。v2 首个真实曝光数:696 文档/6.0@1.5ep。commit e6df788/aeca553。
- [x] **A3** budget_recommendation 进 manifest:`derive_budget_report` 库化 + runner 合并(gbs+规则文案)。commit e6df788。
- [x] **A4** hash 分层取臂:`scripts/extract_arms.py`(world 原子性 hash;stratum=family×depth×target×split×rule_family;每臂 train.jsonl+索引切片+预算;arms.json 绑定银行 shas+overlap 声明);字节一致重跑验证。commit ad30fb7。**待办**:格式臂渲染视图(A5 后)+ token 重测。
- [x] **A5** renderer F 族扩展:族分派重构(六个函数按属主模块解析),prose/table 各 4-12 模板/行类型、per-family 表列、`# extra:` 契约头第三行(携带 rule_holdout 结构声明且篡改/丢弃即拒)、lexical_overlap 证据类型修正。168 测试(原 82 全过);独立冒烟:v2 真实 shard 三格式 round-trip 逐行相等;jsonl=属主原字节。π² 词汇门**推迟**(容器词在钉定问题骨架内,改骨架=契约变更另议,计划允许)。实测记入 B3 参考:alias locate_empty 终端证据分母~8 token,prose 重叠可到 0.375——终端属性非 renderer 缺陷。commit 512f97e。
- [x] **A6** verification.json:solve_visible 全量复核收据(micro bank 16/16)。commit e6df788。**待办**:v2 跑一次进收据(生成侧无脚本,收据随 v3 波)。
- [x] **resume 修复**:--resume 采纳已完成 shard(receipt+指纹+哈希三重验证);ledger 原子重写。中断恢复演练通过。commit e6df788。

## Track B — 数据供给

- [x] **B1** v3 扩容波:`p71_pool_v1` 银行落地(1,504 shards / **6,016 行**=4,656 train+1,360 eval;7 族含 join_unanswerable;L4 结构 252/252 两侧零交叉;**求解器复核 6,016/6,016 进 verification.json**;塌缩门双口径 exit 0,曝光 4,1164 文档实测;监督 token 占比 manifest)。波三跑:首跑 finalize 死于 unanswerable refusal(修 66ec416);二跑暴露 **+1 种子微调孪生世界缺陷**(错题探针 84/504 假泄漏+meta 18.7%,根因=微调 seed 撞下一槽位;修 5a2ea89 为 +10007 大步长);终跑全套验收通过。d2@32k 随机 ~18% infeasible(与 v2 同源)+ F 族 d2@32k 全灭(网格误排,记录在案)——1,840 计划→1,504 完成。臂提取落地(main/format 配对 3,972,mechanism 1,168,extreme 684;各臂独立过门)。commits 372d6f9/66ec416/5a2ea89/d2ed9fa。
- [x] **B2** set_complete 族落地,实测 55.2% < 60% 目标 → **按用户裁定不入 v3 波,只留实测记录**(96 任务 53 形状;per-terminal:set_list 91.7%/count 50%/missing 27%/contains 10.4%——verdict 终端是结构性地板,P69 F2 卡预测属实)。补齐杠杆(终端配比/verdict 嵌套键)是族规格决策,后续单议。加一干预(insert_hit)已实现并验证。commit 7ead630。
- [x] **B3-a** 错题上下文基线:`measure_wrong_context_baseline.py`,v2 实测 198/0 泄漏。commit bf12f0b。
- [ ] **B3-b** 元特征可预测性(可顺延)。**B3-c** 词法重叠 audit 化(可顺延)。
- [x] **B4** unanswerable 脊柱适配器:`capability_unanswerable_adapter.py`(43 新测试,两世界证书双拷贝+篡改拒绝,行形与 join_lookup 可答行匹配;FAMILY_MODULES 注册与 5-10% 配比留给 B1 接线)。commit 1356f88。

## Track C — 真实性与训练准备

- [ ] **C1** IETF + Finance IR grounded 适配器(里程碑外,用户已选)。
- [ ] **C2** 短锚诚实降配比 ~23%(用户已裁定;配比文档待改)。
- [x] **C3** 训练实验冻结:`.hl/design/p71_experiment_freeze.md`(臂/对照/预注册指标/成功标准;GPU 用户门控)。commit 44065f0。

## Track D — 卫生

- [x] 评测脚本 + 汇总器提交。commit ed8556c。
- [x] security hook 修复(2026-09-19):根因不是 hook 逻辑而是模型名——plugin 默认 `claude-opus-4-7`(此端点不存在)+ 端点 Anthropic 路径要求**不带 `[1M]` 后缀**的名字(带后缀 model_not_found)。修复:`~/.claude/settings.json` env 增 `SECURITY_REVIEW_MODEL=zai-org/glm-5.3`(官方支持的 env 覆盖,未改 plugin 文件)。已验证 LLM 往返(`{'verdict':'ok'}`)+ hook 对 .md-only commit 的 skip-30 是正确行为(无可审源文件)。此前"commit 无安全审查"状态解除。

## 红线(继承 P70 + 用户裁定)

不调门/不放松 fail-closed/padding 不计语义规模;π² 词汇门是渲染契约校验非难度门;GPU 用户门控;不为 30-40% 短锚虚报;solver 过≠模型会学,门只作风险诊断,行为结论由 C3 预注册实验回答。
