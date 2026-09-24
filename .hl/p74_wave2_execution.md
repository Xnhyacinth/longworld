# P74 第二波执行记录(2026-09-24,多 subagent 执行)

指令:用户 2026-09-24"15 多subagents按规划和设计执行"。章程 §16 切片冻结
(commit 2d0df55)。两批 agent,文件作用域互斥;GPU 不涉及;训练臂门控不变。

## 批 1 + 批 2 交付(全部完成)

| 工作包 | agent | 交付 |
|---|---|---|
| W2-A 快照→世界桥 | w2a-bridge | wiki_world_bridge.py + 17 tests;7 真实快照全部桥接(事实 196/195/44/197/6/24/143 无损;mention 丢弃 1003 与孤儿实体 460 可见于 BridgeReport;34 grounded 候选关系永不作 gold) |
| W2-B 通用任务银行 | w2b-bank | world_task_bank.py + tests;结构驱动,零逐题代码(grep 证实无主题字符串);demo 世界 4 族×≥6 任务;事实复用统计 |
| T3 证明与证书 | t3-proof | proof_certificates.py + tests;最小充分证据集(贪心删除+真重执行 47 次/44 世界);有界替代证明(重复事实 OR);D_min;四证书;W1≠W2 非唯一构造 |
| T5 长度控制器 | t5-length | length_controller.py + p74_length_report.py + tests;双模式;容量估计校准(≤0.85% 误差 @>44K,声明容差内);无 padding/截断(VER 证实) |
| T6 双路 renderer | t6-renderer | artifact_renderer.py + 21 tests;A 路原文 span 逐字节不变;B 路 5 文体 39/39 事实重验;跨表达答案不变 |
| W2-M 变异刷新 | w2m-mutants | wrong_rule_family 重设计(公式直接应用):4/84→**73/84**;rule_holdout 0.476→0.887;tighten 1→17/84;set_complete 0.250→0.345;C/D 重导 |
| W2-W 快照扩量 | w2w-freeze | 6 个新类目全 live 冻结(cantilever_bridges/cetaceans/deserts/flightless_birds/noble_gases/volcanoes_of_iceland)+ 天文 = 7 快照;独立 stdlib 验证 805 span 逐字全过;manifest 带 CLI commit 钉定 |
| W2-P 多档银行 | w2p-bands | p73_shared_v2:32K(196 行)+64K(196 行)双档;**8K 档结构性不可行如实入档**(records 侧地板 ~17.6K/F 侧 ~26.9K,多族共享的最小世界无法到 8K);witness 审计 392 行;门禁齐 |
| T7 支持矩阵 | t7-matrix | p74_support_matrix.py + 15 tests;真实输出矩阵 + 漏斗;**不可行格全部可见** |
| INT 真实世界端到端 | int-realworld | demo_p74_real_world.py:7 快照 × locate + 6/7 折叠门链(6-8 条/快照,证书 S/E/A/L 全填,D_min 记录,span 指向真实正文);JSON 摘要落 data/ |
| VER 对抗核查 ×2 | ver-data/ver-code | 数据侧(报告后补)+ 代码侧全文审查 |

## VER 代码侧发现与修复(本轮已修 3+2 处)

ver-code 的对抗式审查(读所有 wave-2 模块 + 自写旁路 probe)判定:
SOUND——proof_certificates(真重执行、W1/W2 无答案走私、OR 语义)、
length_controller(无 padding/截断、边界判定诚实)、artifact_renderer(两路
span 语义正确)、bridge 事实账目(无损)。发现并已修:

1. **dependency_ops._map_position 坐标系混淆**(真 bug,潜伏):同文档 ≥2 次
   编辑时,前面 edit 把 offset 移到新坐标系后与后面 edit 的原始区间比较,
   双重计数。ver-code 复现:span[69:72] 重映射到 81(正确 75)。已修:比较
   全在原始坐标系,delta 独立累积。验证:map(69)=75。
2. **dependency_ops.resolve_version $ref lineage**(真 bug,潜伏):关系是
   绑定引用时执行用解析值读事实、proof 扫描却用字面值,真实证据进不了
   lineage。已修:扫描用 bound 解析值。
3. **world_task_bank as_of_state 自答退化**(活缺陷,真实数据):年份值
   时间线("established in" 1976 问 as-of 1976)答案印在问题里;天文快照
   3/6 中招,_nondegenerate_check 对该族无条件放行。已修:候选过滤 +
   守卫双保险;修复后真实世界 0 个自答任务。
   根因注记(ver-code):桥接的 LIST 实体表格列被挤进单一时间线,不同时刻
   多值静默折叠到最大时刻——feeds 退化;这是桥的已知语义不匹配(见其
   docstring 诚实声明),下波改进。
4. **dependency_ops.describe_program 对 entity_id-only bind 崩溃**(INT
   seam 2):桥的链构造器恰好产出该形态,execute() 接受但渲染路径
   KeyError。已修:渲染分支支持 entity_id。
5. **dependency_ops._mutated_world mention 重偏移不复检**(INT seam 3):
   文本编辑破坏标签 mention 后,真实世界(数千 mention)的变异副本过不了
   SemanticWorld 验证,干预检查全挂。已修:被编辑破坏的 mention 诚实丢弃
   (实体与其余 mention 存活),新增回归测试。
   两条均补回归测试(tests/test_dependency_ops.py 19 tests 含 2 新增)。

结构性注记(未修,文档化):折叠门是**结构性**的(存在前驱绑定),不是答案
级依赖证明——ver-code 构造了过门但语义平坦的程序(退化 resolve、死键
链);语义依赖由 check_intervention 独立认证。两者分工如实入档。

## VER 数据侧结论(全确认)

ver-data 五项声明全部 CONFIRMED、零实质缺陷:805/805 span 逐字精确
(非仅包含);v2 银行 30 行种子复检(重生成/重解/答案一致)、10 世界 token
统计与存储逐 token 相等、8K 不可行独立复探(records 地板 20.5-25.0K,
比 manifest 原措辞更高——措辞已更正)、0 切分泄漏;witness 审计重跑字节
一致,wrong_rule_family 5/5 手推公式复验是真错误程序非洗白报错;INT demo
与 T7 矩阵重跑深相等。两个 cosmetic nit(8K 地板措辞低报、demo 链值
int 2023 vs '2023')均已修正(值修后 span 逐字复验仍过)。

## W2-M 刷新后的 C/D 数字(最终口径)

D 360→368 行(rule_holdout 56→62、set_complete 31→33),C 360→368,
重叠 274(76.1%)→288(78.3%)。诚实代价:rule_holdout 的 poor 池 8→2 行
(更多行真 witness-rich 的正确方向,但该族 C/D 对照现在只剩 2 行 poor)。
w2m 结论:要强 C/D 分离,杠杆是更多 parity_vote 世界,不是继续改变异。

## 里程碑判定(章程 §11-1)

**部分达成,如实记录**:7 个真实快照零逐题代码产出 locate 任务 + 6/7 快照
通过折叠门依赖链(证书齐全、span 真实);但 aggregate/multi_hop/as_of_state
在真实 wiki 结构上被跳过(infobox 型事实缺数值三元组、无版本时间线)——
T7 矩阵的 skipped 格 + INT 的 milestone FAIL 行全部可见。"新源接入零出题
代码"在 locate+链 上成立,在数值聚合与状态族上不成立,需要更富的源结构
(表格型类目/论文表格)或 B/C 路线,不是代码能凭空补的。

## 其他诚实注记

- 任务列表在 VER 阶段被清空(误操作,任务状态以本记录为准)。
- ver-code 路径备注:probe 脚本在 job tmp/ver_code/。
- 8K 档不可行(多族共享地板)是设计事实,不是缺陷;单族 8K 世界是下波选项。
- 202 测试全绿(全 wave-2 文件)。

## 未做(下波)

真实源结构扩展(表格密集类目/论文路线 B/代码路线 C 的依赖链)、AND-OR
证明图、256K 容量示例、渲染视图到 T1/T2 的正式接线契约、C/D 对照的
witness-poor 池放大、训练臂(用户门控)。
