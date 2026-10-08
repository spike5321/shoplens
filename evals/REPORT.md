# 阶段 5 评测报告

评测日期：2026-10-08（Asia/Hong_Kong）。

完整真实评测为 80 题 × 3 配置，共 240 个结果；所有失败保留。固定版本 `86d129b`，`deepseek-chat`、温度 0、每调用输出最多 2048 Token、网络重试 0、两个工作线程。

运行时间（Asia/Hong_Kong）：`2026-10-08T19:49:18.006945+08:00` 至 `2026-10-08T19:53:24.929605+08:00`；数据摘要 `147338c9c0a986c6c9f65375aeb260393eb08bda6c20ca2d405d005c32544f6f`，题库摘要 `f812eed17bab00e958a76bcb3998c30c23d95b9e1f3e67a784ea66a5cfdb65a8`。完整源码、依赖和权限参数见 [manifest](runs/20261008-real-full/manifest.json)。

## 正确与执行行为

结果正确率仅以可回答题为分母：开发集 36 题，冻结集 35 题。另有开发集 4 题、冻结集 5 题应拒绝。SQL 成功是通过验证后执行的结果，不能代表所有生成的 SQL 正确。

| 配置/集合 | 结果正确 | SQL 成功/可回答 | 初始澄清 | 正确拒绝 | 错误拒绝数 | 修复成功 |
|---|---|---|---|---|---|---|
| schema/dev | 4/36（11.1%） | 4/36（11.1%） | 3/4（75.0%） | 4/4（100.0%） | 0 | 0/0（无样本） |
| schema/frozen | 2/35（5.7%） | 2/35（5.7%） | 4/5（80.0%） | 2/5（40.0%） | 0 | 0/0（无样本） |
| semantic/dev | 32/36（88.9%） | 32/36（88.9%） | 4/4（100.0%） | 4/4（100.0%） | 1 | 0/0（无样本） |
| semantic/frozen | 31/35（88.6%） | 31/35（88.6%） | 5/5（100.0%） | 4/5（80.0%） | 0 | 0/0（无样本） |
| repair/dev | 32/36（88.9%） | 32/36（88.9%） | 4/4（100.0%） | 4/4（100.0%） | 1 | 0/3（0.0%） |
| repair/frozen | 30/35（85.7%） | 30/35（85.7%） | 5/5（100.0%） | 4/5（80.0%） | 0 | 0/5（0.0%） |

结论：增加指标语义/示例后，冻结集从 2/35 提升至 31/35；允许一次修复为 30/35，没有进一步提升。完整评测 8 次修复均未得到正确结果。初次试跑曾有一次修复成功，该个例不能代替完整评测结论。

## 延迟、调用和 Token

| 配置/集合 | p50 ms | p95 ms | API 调用 | 已知输入 Token | 已知输出 Token | 总 Token |
|---|---|---|---|---|---|---|
| schema/dev | 1896 | 2759 | 78 | 59184 | 9657 | 68841 |
| schema/frozen | 2013 | 3544 | 79 | 60705 | 10680 | 71385 |
| semantic/dev | 1926 | 2981 | 79 | 92167 | 9994 | 102161 |
| semantic/frozen | 1884 | 4377 | 83 | 98023 | 10428 | 108451 |
| repair/dev | 1985 | 3057 | 82 | 95453 | 10439 | 105892 |
| repair/frozen | 1995 | 3757 | 88 | 103292 | 11312 | 114604 |

本次阶段累计（含 4 场景冒烟及两轮试跑）：**572 次调用、666,561 Token**；上限 800 次 / 2,000,000 Token。未知 usage 调用为 0，未触及预算。完整 240 次评测使用 489 次 API 调用、571,334 Token。

未核实带日期价格配置，费用为 NULL。分位数样本小，API 缓存和并发会影响延迟；温度 0 和模型别名不能保证服务商每次返回相同结果。

## 失败分类

- schema/dev：`{"plan_mismatch": 28, "unsupported_sql": 2, "invalid_sql": 1, "model_protocol_error": 1}`；额外澄清 0 次。
- schema/frozen：`{"plan_mismatch": 23, "invalid_sql": 8, "model_protocol_error": 5}`；额外澄清 0 次。
- semantic/dev：`{"plan_mismatch": 3, "false_rejection": 1}`；额外澄清 0 次。
- semantic/frozen：`{"invalid_sql": 1, "plan_mismatch": 3, "model_protocol_error": 1}`；额外澄清 0 次。
- repair/dev：`{"plan_mismatch": 3, "false_rejection": 1}`；额外澄清 0 次。
- repair/frozen：`{"invalid_sql": 2, "plan_mismatch": 3, "model_protocol_error": 1}`；额外澄清 0 次。

## 开发集修改与逐题复测

唯一实现改动：公开 Schema 的视图名从 `sales_lines/refund_events/cash_events` 改为 `analytics.*`，没有放宽 AST 或业务形状验证。以下六个失败案例共享这个来源错误；不把它们称为六种独立根因。

| 问题 | 预期 | 初次实际/证据 | 修改 | 同题复测 |
|---|---|---|---|---|
| 2026年8月实付金额是多少？ | paid_amount [2026-08-01,2026-09-01) [] | `forbidden_source`：`FROM sales_lines` 无 schema，无数据库结果 | 公开 Schema 加 `analytics.` | `plan_mismatch`，来源错误消除但仍未得到正确结果 |
| 2026年9月各渠道成功退款金额 | successful_refund [2026-09-01,2026-10-01) ['channel'] | `forbidden_source`：`FROM refund_events` 无 schema，无数据库结果 | 公开 Schema 加 `analytics.` | `plan_mismatch`，来源错误消除但仍未得到正确结果 |
| 2026年9月各类别期间净收款 | period_net_receipt [2026-09-01,2026-10-01) ['category'] | `forbidden_source`：`FROM cash_events` 无 schema，无数据库结果 | 公开 Schema 加 `analytics.` | `plan_mismatch`，来源错误消除但仍未得到正确结果 |
| 2026年9月客单价是多少？ | average_order_value [2026-09-01,2026-10-01) [] | `forbidden_source`：`FROM sales_lines` 无 schema，无数据库结果 | 公开 Schema 加 `analytics.` | `plan_mismatch`，来源错误消除但仍未得到正确结果 |
| 2026年9月销售额 | 先澄清；paid_amount [2026-09-01,2026-10-01) [] | `forbidden_source`：`FROM sales_lines` 无 schema，无数据库结果 | 公开 Schema 加 `analytics.` | `plan_mismatch`，来源错误消除但仍未得到正确结果 |
| 2026年9月各渠道成功退款金额Top 2 | successful_refund [2026-09-01,2026-10-01) ['channel'] | `forbidden_source`：`FROM refund_events` 无 schema，无数据库结果 | 公开 Schema 加 `analytics.` | `plan_mismatch`，来源错误消除但仍未得到正确结果 |

这项小改动没有提高六题总体正确数：schema 0/6→0/6，semantic 4/6→4/6，repair 5/6→4/6。剩余失败涉及聚合、重复别名、误分组及模型契约；两轮小样本不能归因出修正本身的质量收益。

独立原始记录计算的六题预期表格（不进入模型提示词）：

```json
{
  "dev-01": [
    {
      "columns": [
        "value"
      ],
      "rows": [
        [
          "17298101"
        ]
      ]
    }
  ],
  "dev-09": [
    {
      "columns": [
        "channel",
        "value"
      ],
      "rows": [
        [
          "广告",
          "350417"
        ],
        [
          "站外",
          "355224"
        ],
        [
          "自然",
          "339984"
        ]
      ]
    }
  ],
  "dev-16": [
    {
      "columns": [
        "category",
        "value"
      ],
      "rows": [
        [
          "家居",
          "4261020"
        ],
        [
          "数码",
          "4839152"
        ],
        [
          "服饰",
          "3813035"
        ],
        [
          "食品",
          "3719928"
        ]
      ]
    }
  ],
  "dev-26": [
    {
      "columns": [
        "value"
      ],
      "rows": [
        [
          "18727.5"
        ]
      ]
    }
  ],
  "dev-31": [
    {
      "columns": [
        "value"
      ],
      "rows": [
        [
          "17678760"
        ]
      ]
    }
  ],
  "dev-40": [
    {
      "columns": [
        "channel",
        "value"
      ],
      "rows": [
        [
          "站外",
          "355224"
        ],
        [
          "广告",
          "350417"
        ]
      ]
    }
  ]
}
```

## 其他失败及限制

- `dev-25/dev-26/dev-30`：预期总体客单价，模型在 `group_by=[]` 时仍生成 `day/GROUP BY day`。完整 repair 先 validate 失败，再 repair 后仍 `plan_mismatch`，没有进入 execute。当前改动未处理这一根因；下一步可在开发集验证明确的空分组输出契约。
- `dev-40`：按渠道 Top 2 是已支持请求，模型却返回 unsupported，理由把它误认为比较组合。规划拒绝不能被 SQL 修复挽回。保留错误拒绝，未增加自动改写规划；可在未来开发集检查支持范围说明。
- `frozen-02/frozen-20`：单个期间的 SqlDraft 返回两条查询，实际 `invalid_sql` 是数量与期间计划不符；修复后数量仍不符。没有将其中一条悄悄取出执行。未用冻结题修改提示词。
- `frozen-38`：超过一年应明确拒绝，模型输出无法通过 QueryPlan 契约，系统以 model_protocol_error 失败并未查库，拒绝表达不合格。usage 从 raw 返回值单独保存，未将该失败计为零成本。

## 约束、冻结与可复现边界

24/24 固定非法 SQL 被 AST 阻断，不能外推成现实攻击阻断率。独立参考检查/真实 reader/权限、超时和时区另由统一验收覆盖。离线开发集三配置均 40/40 行为通过，是同一假模型的接线验证，不能替代真实质量。

冻结题的问法与交互家族没有用于此次开发集迭代；指标/视图和部分 SQL 聚合形状共享。本轮 `frozen_used_for_iteration=false`、冻结摘要匹配，完整真实运行前未执行冻结题。结果发布后该集已暴露，未来据此调试必须标记迭代，不能称为盲测。评分在当前严格 SQL 形状约束下进行，不能外推至任意等价 SQL 或通用 BI。

三配置是评测对照，不自动切换产品默认配置。默认真实模型适配器不包含评测的三条示例；这些数值不能直接宣称为已部署浏览器版本准确率。真实 HTTP/浏览器模型链路、容器和公网未在本阶段验收。

## 原始结果与复现

- [初次开发试跑](runs/20261008-real-pilot/results.jsonl)、[修改后复测](runs/20261008-real-retest/results.jsonl)、[完整评测](runs/20261008-real-full/results.jsonl)：成功和失败均保留。
- [完整汇总](runs/20261008-real-full/summary.json)、[图冒烟](runs/20261008-real-smoke.json)、[冻结配置](freeze.json)。
- 命令/评分/预算规则见 [评测说明](README.md)。报告可用 `python scripts/report_evals.py` 从上述原始结果重新生成；初次版本为 `56210b8`，完整模型/业务配置为 `86d129b`。重放使用当前 runner/freeze（包含 LF/CRLF 摘要归一），原始记录与模型输入未改写。
