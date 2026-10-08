# 第三方参考与借鉴记录

> 规则来自总提示词第 10 条：GitHub 项目用于学习模块和设计；**实际复用代码**必须保留许可与出处，
> 在这里说明借鉴范围、做了哪些修改、如何验证。
> 不得把第三方成果写成自己的实测成果，也不得把「读过 README」写成「审计过实现」。

核对日期：2026-09-24。仓库会更新，学习时记录实际 commit 或版本。

## 1. 许可与署名政策

- GitHub 首页的 License 标签**不能**概括整个仓库。真正复制文件前，逐个检查该文件所在目录的
  `LICENSE` / `NOTICE` / 文件头注释。
- 一旦复制代码（不只是阅读思路），必须做三件事：
  1. 在本文记录来源仓库、commit、文件路径、许可类型；
  2. 在源文件顶部保留原始版权声明；
  3. 在本文写清「我改了什么、为什么改、哪条测试证明改完是对的」。
- 到目前为止（阶段 0），**没有复制任何第三方代码**。下面的条目全部是「阅读学习」状态。

## 2. 学习清单与进度

| 参考 | 只重点看什么 | 用在本项目哪里 | 状态 |
|---|---|---|---|
| [langchain-ai/langchain-academy](https://github.com/langchain-ai/langchain-academy) | 基础图、状态、消息、持久化、人工介入的 notebook | 阶段 3 的图编排与 checkpoint | 未开始 |
| [LangGraph 官方 SQL Agent 教程](https://docs.langchain.com/oss/python/langgraph/sql-agent) | 把「生成 -> 检查 -> 执行」拆成有约束的步骤 | 阶段 3 的查询流程骨架 | 未开始 |
| [Canner/WrenAI](https://github.com/Canner/WrenAI) | Schema 之外的指标定义、单位、关联与示例如何组织 | 阶段 1 的指标 YAML 结构 | 未开始 |
| [vanna-ai/vanna](https://github.com/vanna-ai/vanna) | 查数产品如何展示 SQL、表格、图表与执行过程 | 阶段 4 的界面参考（不引入框架） | 未开始 |

## 3. 阶段 0 的阅读结论（仅限官方文档，无代码复用）

阶段 0 只读了官方文档，用来确定接口形状，没有读大型仓库。两条实测结论：

1. **LangGraph 的条件边与 reducer 是独立的两件事。**
   官方 Graph API 页面确认：`add_conditional_edges(node, routing_fn, mapping)` 的 `routing_fn`
   返回的是**目标节点名**；而状态字段是否累加由 `Annotated[..., reducer]` 决定，
   与边无关。`scripts/learning_demo.py` 里的 `DemoState.log` 用 `Annotated[list[str], add]`
   保留完整轨迹，正是为了把这两件事分开演示。

2. **LangChain 1.x 的定位已经明确写成「Agent = Model + Harness」。**
   官方 Overview 页面把 `create_agent` 描述为「a minimal, highly configurable harness」，
   并明确 LangGraph 用于 "advanced needs combining deterministic and agentic workflows"。
   这与本项目「确定性校验不让模型决定」的设计一致，因此主流程用自定义图而不是 `create_agent`。

## 4. 学习记录卡模板

每读一个模块填一张，避免「读完了但说不清」：

```text
来源：仓库/文档链接、commit 或版本、日期
问题：它解决什么具体问题？
输入/输出：调用者传什么，返回什么？
机制：关键步骤和约束是什么？
取舍：它为什么这样做，有什么局限？
本项目应用：采用了哪一个思想或代码片段？
个人修改：为什么需要改？
验证：哪条测试/实测说明修改有效？
许可与署名：实际复制代码是否保留要求的声明？
```

## 5. 阶段 3 官方接口核对（2026-10-02）

- [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)：核对 `interrupt`、`Command(resume=...)` 与节点恢复时的重放行为；项目把等待状态和中断节点分开，中断前不调用模型或执行 SQL。
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)：核对 checkpoint 与 thread_id 的作用；项目在应用凭据和 `app_state` schema 下使用 PostgreSQL saver，两个独立 CLI 进程实际验证恢复。
- [ChatDeepSeek](https://docs.langchain.com/oss/python/integrations/chat/deepseek)：核对结构化输出与 usage。当前锁定版本使用 `method="function_calling", include_raw=True`；本地协议 stub 通过，当前无 Key，真实服务商的阶段 3 验证未执行。

仅参考接口说明并检查本机已安装包源码，没有复制第三方业务实现。

## 6. 阶段 4 官方接口核对（2026-10-02）

- [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/)：在应用生命周期创建/关闭连接池、工作线程和进程锁，健康探针独立于业务服务可用性。
- [PostgreSQL explicit locking](https://www.postgresql.org/docs/18/explicit-locking.html)：用会话 advisory lock 限制单个 API 进程；澄清由带状态/版本/有效期条件的 UPDATE 原子领取。
- [MDN Set-Cookie](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie)：核对 HttpOnly、SameSite、Secure、Path、Max-Age。项目另用 CSRF header 与 Origin 校验，Cookie 原值不进入日志/截图。
- [Playwright web server](https://playwright.dev/docs/test-webserver) 和 [API testing](https://playwright.dev/docs/api-testing)：测试独占 Vite 服务与独立浏览器 context，各 context 的 request 使用自己的 Cookie；后端子进程由测试显式终止并重启。
- [ECharts 基础折线图](https://echarts.apache.org/handbook/zh/how-to/chart-types/line/basic-line/)：固定代码把类型化 labels/values 转为 line/bar 配置；未使用模型 JavaScript。版本锁定为 ECharts 6.1.0、Playwright 1.63.0；界面为项目自行实现，没有复制参考仓库界面或业务代码。

## 7. 阶段 5 官方接口核对（2026-10-08）

- [ChatDeepSeek 集成](https://docs.langchain.com/oss/python/integrations/chat/deepseek)：核对 tool calling、结构化输出和 Token usage；沿用锁定版本及 `function_calling/include_raw`，真实查询图冒烟 4/4 通过。评测记录解析失败时仍返回的 usage，不将协议 stub 算作真实模型结果。
- 尝试访问 [DeepSeek 官方价格页](https://api-docs.deepseek.com/quick_start/pricing)，本次读取超时，未核实 2026-10-08 适用价格。不填人民币费用，不使用未核实单价。

评测题、原始记录参考计算、评分、预算账本和报告均由项目自行实现，没有复制第三方评测题或标准答案。
