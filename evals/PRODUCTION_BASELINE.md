# 产品实际配置基准：准备完成，真实评测待授权

记录日期：2026-10-26（Asia/Hong_Kong）。实现版本 `28762b9`，入口 `scripts/run_evals.py --profiles production`。

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

## 真实评测：未执行

6道开发题真实试跑的命令在启动前被自动审批拒绝，原因是需要明确授权发送到外部DeepSeek服务的具体载荷。没有创建真实试跑输出目录，没有新增API调用。预算仍为累计580/800次、676213/2000000 Token，未知与在途预留均为0。

待授权目的地：`https://api.deepseek.com/v1`，模型`deepseek-chat`。发送内容为固定合成问题、必要澄清、固定参考日期、指标字典、允许的视图字段、产品提示词/结构化输出协议，以及SQL修复需要的候选SQL和错误类别。参考答案、原始订单记录、数据库凭据不作为模型输入；模型API Key仅用于既有服务的请求认证。

授权后先跑6道已公开开发题确认接线，再跑80道旧题的production配置；二者与后续少量实际页面接线检查共用现有预算，耗尽即停止，保留失败/未知消耗。本轮不修改产品提示词、业务逻辑或评分标准。新保留题在后续质量优化前另行建立，不能把旧题改名当盲测。

runner采用产品查询内核及内存checkpoint隔离评测任务；完整80题不等同于HTTP、持久化恢复或并发负载验收。网页当前仍为离线模式。
