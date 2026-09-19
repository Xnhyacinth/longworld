# P71-C3 训练实验设计冻结(2026-09-19)

状态:冻结候选。数据侧前提 = p71_pool_v1 银行(生成中)+ 本文档定义的臂。
GPU 启动仍为用户门控;本文档是解锁件,不是启动令。

## 1. 总原则(继承用户裁定)

- **预算是上限建议不是许可证明**:steps = floor(epochs × train_rows / GBS) 由
  manifest 的 budget_recommendation 块给出;实际执行步数回填。
- 训练从**未退化 Base** 起步;分阶段评测(每 ~1/3 预算存 ckpt)。
- 预注册指标先行(§6);不以事后择优解释。
- 配比四种口径分报:样本数/输入 token/监督 token/训练步。
- 红线:不合成 benchmark 格式行;needle-copy ≤10-15%(仅 grader 合规);
  短锚按 ~23% 现货诚实配比(扩源后回升,不重复采样凑数)。

## 2. 银行与臂(数据对象)

银行:`data/capability_records/p71_pool_v1`(七族,~1,540 shards 预计;
group_id=world_id;L4 结构隔离 split;unanswerable 行含两世界证书)。
臂 = `scripts/extract_arms.py` 的确定性导出(hash 分层,world 原子,配对 strata)。

| 臂 | 组成 | 对照回答 |
|---|---|---|
| **M-main** | 全族 ~5,000 train 行(实际池推导配额) | 主效果:多能力机制 vs 基线 |
| **F-format** | 共同支持集上 jsonl vs prose/table **配对**(同 world 同 gold,token 重测) | 表达分布是否是收益/退化来源 |
| **X-mech** | 机制消融(remove-one/干预子集;依据 provenance 的必要性采样) | 干预验证的行是否承载增益 |
| **E-extreme** | L/K/H 极端单元(池内 completed 极值格) | 访问距离/抗干扰/计算量分层效应 |

eval 保持银行级(种子隔离 + L4 结构隔离);"未见 renderer"eval 视图由
A5 渲染路径事后生成。

## 3. 训练设置(固定项)

- Backbone:Qwen3.5-4B-Base(与 P64/ACC/LongTrace 同源)。
- 路径:8-GPU Megatron-SWIFT(`run_p64_8gpu.sh` 形态;TP4/CP2,cutoff 262144)。
- GBS 16,steps 按臂行数推导(每臂自己的 budget_recommendation)。
- 每臂同 LR(1e-5,与两基线一致)、同调度、同 save 节奏:预算 0/1/3、1/3、2/3、满。
- 短锚:全臂统一 ~23% token 配比(stage 结构按配方 §4.2)。

## 4. 对照(用户评审的四象限,P67 A0-A5 映射)

| 对照 | 固定 | 回答 | P67 映射 |
|---|---|---|---|
| M-main vs MRCR-like 单类 | Base/回放/预算 | 多能力机制是否比单类检索更广有效 | A3 的机制半边 |
| F-format(jsonl vs 混合格式) | 同任务/同 gold/共同支持集/配对 | 收益是否来自表达分布 | 评审实验 A |
| 模拟 vs 真实 grounded(C1 后) | 尽量匹配任务族与难度 | 真实语义是否改善域外迁移 | 后续波 |
| 紧凑证据 vs 长干扰 vs 更大必读 | 分别控制计算与长度 | 训练的是访问距离、抗干扰还是全局计算 | 评审实验 B |

基线(已存在,不重训):A0 未训 Base(评测已齐);R1 acc_base_ckpt680;
R2 longtrace_base_ckpt680;A1 P64-680(失败对照)。A2 仅在 ckpt200 显示
未塌缩时补(当前证据:已塌缩,免)。

## 5. 评测集(分层,未见优先)

- 内部 held-out(银行 eval split):未见实例(全族)+ 未见规则结构(L4)+
  未见 renderer 视图(A5 事后渲染)。
- 外部:0912 对齐协议全套(MRCR 2/4、GraphWalks Parents/BFS、IFEval、
  GPQA、MMLU-Pro;hash-prefix 与自由格式双口径)。
- 保持性:短数学/代码/知识(短锚源的 held-out 切片)。
- 按 L1-L4 族分曲线,不只平均分;同 world 任务聚类统计。
- 失败显性化:response-shape 熵探针(固定探针集,P67 §4.2 的塌缩探测器)
  + 格式失败率与内容正确率分开报。

## 6. 预注册指标与成功标准

指标(训练全程记录):train loss、held-out eval loss、**output-shape entropy
探针**、GraphWalks format-fail、监督 token 占比。

成功标准(用户定义):
1. 完整长输入的内容表现提升,**紧凑证据/oracle 表现不退化**;
2. 未见来源与结构有收益(未见实例/规则/格式三档);
3. 无再次协议捕获(shape 熵探针不掉、MMLU-Pro 非零);
4. 若仅内部 JSON 语法上涨而 prose/真实/未见规则全不动 → 判"局部解释器",
   如实报告,不外推。

## 7. 判定纪律

- 每对照单独归因;机制消融不可能全同配,明确声明改变量(哪个轴动了)。
- 结果无论正负进 `.hl/`;负结果照 P67 §6 定位为可发表资产。
