# P72 银行口径统一表(全部派生自 sample_index,禁止口头汇总)

生成:2026-09-19,派生脚本见 git 历史(G72-6)。口径约定:
- **行 = 语义任务**(一问一行契约);CF/视图是行的属性,不是额外行。
- **世界 = group_id** = world_id(曝光分母);shard = 一个 (family,H,L,seed,target) 槽 = 4 行。
- **臂 = 银行的 hash 分层导出**(world 原子);臂行数 ≠ 池行数(main+extreme 是覆盖式)。
- 训练剂量只认:臂的 train.jsonl + 其 sample_index 切片 + 其 budget_recommendation。

## 各池(不可变,递进)

| 池 | 行(总/train/eval) | 世界(总/train) | shard(完/计) | 全对话 token |
|---|---|---|---|---|
| p69_validation_wave_v1 | 348/278/70 | 87/… | 174/… | 27.5M |(索引未回填)
| p70_superset_v1 | 734/588/146 | 187/… | 187/… | 64.4M |(索引未回填)
| p70_superset_v2 | 3,476/2,784/692 | 869/696 | 869/1,440 | 332.0M |
| **p71_pool_v1(基线)** | **6,016/4,656/1,360** | **1,504/1,164** | 1,504/1,840 | 570.3M |

注:p69/p70v1 的索引可随时用 build_record_bank_index.py 回填(银行写一次原则,回填=新增文件)。

## p71 臂(v2,含物化格式视图)

| 臂 | train 行 | 世界 |
|---|---|---|
| main | 3,972 | 1,278 |
| format(配对 main) | 3,972 | 1,278 |
| mechanism | 1,168 | 374 |
| extreme | 684 | 226 |

格式视图(三格式并行,gold 字节一致,每任务过 verify_format_equivalence):
- jsonl/prose/table 各 3,564 行(= format 臂 3,972 − 408 行 join_unanswerable
  显式排除:其世界钉 records join_lookup 契约而任务程序带适配器 family,gold 门
  无法按 family 匹配验证;jsonl 臂训练导出须同步剔除同 408 行保任务组成一致)。
- 视图 token 尚未实测(行内注明);训练导出时统一重测——C3 管线前置项。

## 曝光口径(G72-6 分离,替换旧的单值对比)

- P64(事故):E_source = 989(11 个源文档,5.57 epoch)。
- p71:E_world = 6.0 @1.5ep(1,164 train 世界,4 行/世界)——**世界原子性口径**,
  与 P64 的源文档口径**不可直接相除**;E_task = 1.5(一问一行,epoch 上限内
  每任务见 1.5 次);E_rule-structure:train 侧仅 threshold_class 一种(结构隔离)。
- 模板曝光:shape-exposure 4.75@1.5ep(门红线 10;P64 68.4)。

## 语义任务独立性声明(用户质疑 §3.2 的回应)

一问一行 ≠ 每行独立任务——4 行/世界是 4 个**不同查询目标**(q0..q3:不同 program、
不同措辞、不同消费行集;同世界=共享上下文,分属 train/eval 时世界不跨侧)。
证据:distinct instructions 2,688/2,784(v2)、消费行集 remove-one 必要性逐行验证。
CF/事实对是设计属性(provenance.decoy),当前波内**未**作为独立行计数。

## p72_short_dense_v1(长度补全 + 密集 + research domain,811 shards)

| 池 | 行(总/train/eval) | 世界(总/train) | 全对话 token |
|---|---|---|---|
| p72_short_dense_v1 | 3244/2464/780 | 811/616 | 108,743,610 |

- **长度覆盖(train)**:8K 648 / 16K 840 / 64K 976——三档全部 ≥300 行(p71 双峰之外的补全)。
- **族(train 10 族)**:dense_aggregate 80(必读密度 0.5-0.8,K/L=0.61@8K)、
  research_run 136 + research_join 284(simulated_domain)、join_unanswerable 192。
- 求解器复核 3244/3244;门双口径 exit 0(曝光 6.0);matched-entity 探针 200/0 重现。
- 预算:154/231 步,shape-exposure 7.26(红线 10 内)。
- 诚实账:352 infeasible(K/干扰地板的系统性格)+ 7 rejects(dense 密度带 4 + asof 干扰地板 3),全部带理由。
- **P71+P72 合并视野**:8K/16K/32K/64K/128K 五档连续覆盖(p71 供 32K/128K,p72 供 8K/16K/64K),
  十一族(含 set_complete 实测未入选),两 domain(模拟记录 + research)。
