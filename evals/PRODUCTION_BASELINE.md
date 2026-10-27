# 产品实际配置回归基准

评测日期：2026-10-26；浏览器复核与交接日期：2026-10-27（Asia/Hong_Kong）。实现版本 `28762b9`，真实运行版本 `5ba4785`；两版本的产品及评测代码相同。入口 `scripts/run_evals.py --profiles production`。

**结论：真实模型80题完整运行；71道可回答题中68道正确（95.8%），9道应拒绝题中8道正确拒绝。** 这是固定合成数据、已公开旧题和严格SQL形状约束下的产品当前配置回归基准，不是新题盲测，也不能外推至任意电商问题。

## 已完成的核对

- 用户在离线页面实际完成正常查询、口径澄清并继续、跨查询金额一致性、预测拒绝。
- 实际容器数据库四张业务表逐行匹配固定种子生成记录。独立 Python 原始记录计算确认截图中的三个渠道实付、总体实付、退款及净收款一致。
- 9月总体实付176787.60元、成功退款10456.25元、期间净收款166331.35元。上述数值是合成数据的核对结果，不代表真实模型答对率。
- 独立参考检查80组、14个手算样例通过。
- 新增5项保护测试经历失败→实现→通过；统一验收98离线+18真实PostgreSQL=116项测试通过，PASS=10/FAIL=0/NOT_RUN=2。两项未执行为未开启的真实模型冒烟。

## 离线接线结果

运行命令：

```powershell
.\.venv\Scripts\python.exe scripts/run_evals.py --profiles production --out evals/runs/20261026-production-offline
```

80/80个结果完整保留，真实API调用0，24/24固定非法SQL被阻断。开发集行为40/40、旧冻结集行为32/40；后者有5个wrong_plan、1个model_protocol_error、2个wrong_behavior。离线解析器支持有限句式，这些结果只描述接线与假模型行为，不用于衡量DeepSeek质量。

该运行发生在实现提交前，manifest的source_commit是父提交；其中完整源码摘要对应新增入口的实际运行文件。旧冻结业务源码摘要匹配，题目已曝光，本次及后续重复测量均不称盲测。

证据：[manifest](runs/20261026-production-offline/manifest.json)、[完整结果](runs/20261026-production-offline/results.jsonl)、[汇总](runs/20261026-production-offline/summary.json)、[约束记录](runs/20261026-production-offline/constraints.json)。

## 真实模型配置与执行

首次真实试跑在进程启动前被自动审批拒绝；用户随后明确同意将合成问题、澄清、指标定义、允许字段、产品提示词及必要候选SQL/错误发送至 `https://api.deepseek.com/v1`。授权后才执行真实请求，不绕过审批。参考答案、原始订单记录和数据库凭据不进入模型输入；Key仅用于服务认证。

使用 `deepseek-chat`、温度0、每调用最多2048输出Token、网络重试0、最多一次SQL修复、两个评测线程。生产适配器及提示词保持不变，不加入旧semantic/repair配置中的示例。冻结源码摘要仍匹配；manifest明确旧冻结结果已曝光。

```powershell
.\.venv\Scripts\python.exe scripts/run_evals.py --real-model --profiles production --split dev --ids dev-01 dev-09 dev-16 dev-26 dev-31 dev-40 --out evals/runs/20261026-production-pilot
.\.venv\Scripts\python.exe scripts/run_evals.py --real-model --profiles production --out evals/runs/20261026-production-full
```

6题试跑6/6正确，13次调用；随后80题只跑一次，80/80结果完整保存，退出码0仅表示运行完成。试跑中的客单价题成功，完整评测中同题失败，两个结果均保留，不挑最好一次。本轮没有通过试跑修改模型或评分。

| 集合 | 全部题数 | 可回答题结果正确 | 应拒绝题正确拒绝 | 初次澄清正确 | 修复成功 |
|---|---:|---:|---:|---:|---:|
| 开发集 | 40 | 35/36（97.2%） | 4/4 | 4/4 | 0/1 |
| 旧冻结集（已曝光） | 40 | 33/35（94.3%） | 4/5 | 5/5 | 1/3 |
| 合计 | 80 | 68/71（95.8%） | 8/9 | 9/9 | 1/4 |

包含拒绝题的行为正确为76/80（95.0%）。可回答题没有错误拒绝，3题因SQL与计划不符失败；另1道应拒绝题返回模型协议错误。实际进入数据库执行的可回答题68/68成功，但不能用这个分母掩盖前面3题失败。固定非法SQL24/24阻断，不外推为现实攻击阻断率。

## 全部4个失败案例

| 题目ID | 问题与实际行为 | 已观察到的证据 / 后续方向 |
|---|---|---|
| dev-26 | “2026年9月客单价是多少？”最终失败 | 计划为总体、无分组，SQL却额外按日期/渠道/类别分组；一次修复后仍不符。优先作为下一步教学修复案例。 |
| frozen-26 | 查询2026-10-31至2026-11-01的客单价，最终失败 | 计划无分组，SQL增加day分组；修复后仍不符。后续改进不能将空结果问题用分组SQL掩盖。 |
| frozen-28 | “上周客单价是多少”，最终失败 | 计划区间是9月21日至9月28日且无分组，SQL增加day分组；修复未消除。 |
| frozen-38 | 查询超过一年区间的实付金额，返回协议错误 | 应明确拒绝超出支持范围，实际为model_protocol_error，未执行SQL；生产适配器没有保留原始协议失败usage，本轮不猜测原始输出的更多细节。 |

上述前三题被校验挡住，没有把错误分组的金额当作正确总体返回。`frozen-25`是本轮唯一修复后正确的题目。当前记录只支持这些具体观察，不代表一次修复普遍有效；本轮没有同时间、同配置的“关闭修复”对照，不能从1/4推导整体收益。

## 延迟和预算

| 集合 | p50 | p95 | API调用 | 已知Token | usage未知调用 |
|---|---:|---:|---:|---:|---:|
| 开发集 | 2351ms | 3709ms | 81 | 97445 | 0 |
| 旧冻结集 | 2317ms | 4441ms | 86 | 103489 | 1 |

完整80题共167次调用、已知200934 Token；协议失败有1次usage未知，保留22236 Token预算预留，不能把未知消耗写成0。6题试跑另13次/15380 Token，真实浏览器另8次/9642 Token。本轮新增188次调用、已知225956 Token，另有22236未知预留。

累计账本为768/800次调用、已知902169 Token＋22236未知预留＝924405预算占用，小于200万；在途预留0。剩余调用32次。未核实带日期价格，不报人民币费用。小样本分位数受网络、缓存和并发影响；模型别名和温度0不保证复跑一致。不能把本轮95.8%与阶段5不同配置/不同时间的88.6%直接解释为优化收益。

## 真实浏览器核对

2026-10-27，实际Edge页面→HTTP→生产模型→受限SQL→真实本机PostgreSQL完成4场景；独立原始记录评分及历史刷新/双会话隔离合计5/5通过。没有模拟响应。正常查询2次调用、澄清3次、退款2次、禁止操作1次；禁止操作没有execute节点。

使用已有 `scripts/delivery_api.py --real-model --budget .local/eval-budget-20261008.json` 启动18006；Vite代理到该端口。复用 `frontend/e2e/delivery.mjs`，临时副本仅将baseURL设为4173、输出目录设为新的20261027-production-browser；评分命令为 `scripts/check_delivery.py .local/20261027-production-browser/results.json`。

最初误选5174端口，被项目既有Origin允许列表拒绝，浏览器导航超时且模型调用0。改用已有允许的4173端口；未放宽Origin/CSRF检查。期间自动审批服务因额度不足不可用，用户要求继续后恢复执行，完整80题未重跑。此前失败记录保留在本地忽略目录，并附非敏感JSON诊断。真实澄清截图仅追问指标口径，没有重复要求日期；不能把用户见到的离线文案问题直接归于真实模型。

## 证据与边界

- [6题试跑汇总](runs/20261026-production-pilot/summary.json)、[试跑全部记录](runs/20261026-production-pilot/results.jsonl)
- [完整80题manifest](runs/20261026-production-full/manifest.json)、[完整汇总](runs/20261026-production-full/summary.json)、[全部成功与失败](runs/20261026-production-full/results.jsonl)
- [真实浏览器原始记录](../docs/evidence/production-20261027-browser.json)、[独立评分](../docs/evidence/production-20261027-browser-summary.json)、[首次端口失败诊断](../docs/evidence/production-20261026-browser-port-failure.json)
- [正常查询截图](../docs/assets/production-20261027-normal.png)、[真实澄清截图](../docs/assets/production-20261027-clarification.png)

80题runner采用产品查询内核及内存checkpoint隔离任务；它不替代HTTP、持久化或负载验收。浏览器4个场景通过也不能覆盖80题的全部页面表现。未部署公网、未做负载测试。本轮仅建立修改前基准，未修客单价失败、离线重复日期追问或增加连续追问；后续优化前应另建未参与调试的新保留题。
