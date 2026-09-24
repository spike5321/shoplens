"""阶段 0 独立学习样例：LangChain 声明式工具 + LangGraph 条件边小图。

这个文件**不是**业务代码，也不会被后端应用 import。
它的唯一目的是让你亲手看到两件事，并且能自己改参数观察变化：

1. LangChain 的 ``@tool`` 到底产出了什么契约
   —— 工具名、描述、参数 JSON Schema、校验失败长什么样。
2. LangGraph 的 ``StateGraph`` 如何用「状态 + 条件边」表达分支和有界重试
   —— 这正是后面查询流程（解析 -> 澄清 / 生成 SQL -> 校验 -> 最多修复一次）的骨架。

运行方式::

    python scripts/learning_demo.py

全部逻辑离线可跑，不需要任何 API Key。
真实模型的可选冒烟在文件末尾，未配置 Key 时会明确打印「未验证」。
"""

from __future__ import annotations

import sys
from operator import add
from pathlib import Path
from typing import Annotated, Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import TypedDict

# Windows 的默认 gbk 编码不能表示部分演示符号（例如 ¥）；保持独立脚本可运行。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="backslashreplace")

# 让脚本能 import 到 `backend/app` —— 与 scripts/smoke_real_model.py 同一套做法。
# 不这样做的话，`python scripts/learning_demo.py` 里的 `from app.config import ...`
# 会 ModuleNotFoundError：Python 只把**脚本所在目录**（scripts/）放进 sys.path，
# 不是当前工作目录。pytest 那边靠 pyproject.toml 的 pythonpath 解决了，脚本要自己解决。
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))

# ============================================================================
# 第 1 部分：用 LangChain 声明两个确定性工具
# ----------------------------------------------------------------------------
# 「确定性」= 同样的输入永远给同样的输出，不调模型、不查库、不联网。
# 业务上的精确计算就应该放在这种地方，而不是交给模型心算。
# ============================================================================


class NetReceiptArgs(BaseModel):
    """显式声明入参 schema，而不是让框架从函数签名自动推断。

    为什么要费这个事？因为实测发现：

        Pydantic v2 生成的 JSON Schema 默认**不含** ``additionalProperties: false``，
        模型多传一个参数名（例如把 refund_cents 写成 refund）不会报错，会被静默丢弃，
        工具照样返回一个"看起来正常"的数字。

    对查数系统来说这是最危险的那类 bug：错了但没有报错。所以这里显式
    ``extra="forbid"``，让多余字段直接变成校验失败。同时把 description 写清楚，
    它是模型唯一能看到的语义提示。
    """

    model_config = ConfigDict(extra="forbid")

    paid_cents: int = Field(
        description="期间内的成功支付订单实付金额合计，单位：分（整数，不能是元或小数）"
    )
    refund_cents: int = Field(
        default=0,
        description="同期内完成且状态成功的退款金额合计，单位：分（整数）",
    )


class FormatCentsArgs(BaseModel):
    """金额只在展示层转成元；内部一律整数分，避免浮点误差。"""

    model_config = ConfigDict(extra="forbid")

    amount_cents: int = Field(description="要格式化的金额，单位：分（整数，可为负）")


@tool(args_schema=NetReceiptArgs)
def net_receipt_cents(paid_cents: int, refund_cents: int = 0) -> int:
    """计算期间净收款，单位「分」。

    定义（与 PROJECT_PLAN.md 指标字典一致）：
    同期实付金额 - 同期成功退款金额。

    注意：刻意**不**校验「退款必须小于实付」。因为按退款完成时间入账时，
    一个期间的退款完全可能超过该期间的实付（上期订单在本期退款）。
    这种业务约束要靠 SQL 口径处理，不能写成工具的入参校验。
    """
    if paid_cents < 0 or refund_cents < 0:
        raise ValueError("金额不能为负（单位：分）")
    return paid_cents - refund_cents


@tool(args_schema=FormatCentsArgs)
def format_cents_as_cny(amount_cents: int) -> str:
    """把整数分格式化成人民币字符串。例：123456 -> '¥1234.56'。"""
    sign = "-" if amount_cents < 0 else ""
    yuan, cent = divmod(abs(amount_cents), 100)
    return f"{sign}¥{yuan}.{cent:02d}"


LEARNING_TOOLS = [net_receipt_cents, format_cents_as_cny]


def inspect_tool_schema(t: Any) -> dict[str, Any]:
    """把一个工具的内部契约摊开成可以打印的字典。

    ``args_schema`` 是 Pydantic 模型；模型看到的是它的 JSON Schema。
    这份 Schema 就是「模型能填什么参数」和「代码会校验什么」之间唯一的契约。
    """
    schema = t.args_schema.model_json_schema()
    return {
        "name": t.name,
        "description": t.description,
        # 模型依据 description + schema 决定是否调用、怎么填参数
        "description_first_line": t.description.splitlines()[0],
        "parameters": {k: v for k, v in schema.get("properties", {}).items()},
        "required": schema.get("required", []),
        "additionalProperties": schema.get("additionalProperties"),
    }


def demo_tool_contract() -> dict[str, Any]:
    """打印工具契约，并真实触发一次「模型填错参数」的校验失败。"""
    print("=" * 74)
    print("第 1 部分：LangChain @tool —— 它到底产出了什么契约？")
    print("=" * 74)

    contracts = []
    for t in LEARNING_TOOLS:
        info = inspect_tool_schema(t)
        contracts.append(info)
        print(f"\n工具名 : {info['name']}")
        print(f"描述   : {info['description_first_line']}")
        print("参数   :")
        for pname, pspec in info["parameters"].items():
            default = pspec.get("default", "<必填>")
            print(
                f"    - {pname}: {pspec.get('type')} "
                f"(default={default})  {pspec.get('description', '')}"
            )
        print(f"必填   : {info['required']}")
        print(f"禁止多余字段(additionalProperties=False): {info['additionalProperties'] is False}")

    # --- 正常调用：不经过模型，Python 直接调 ---
    print(
        "\n[正常调用] net_receipt_cents.invoke({'paid_cents': 12_345_00, 'refund_cents': 2_345_00})"
    )
    ok = net_receipt_cents.invoke({"paid_cents": 12_345_00, "refund_cents": 2_345_00})
    print(f"  -> {ok}  (分) = {format_cents_as_cny.invoke({'amount_cents': ok})}")

    # --- 参数填错：看 Pydantic 校验在模型之外兜底 ---
    print("\n[参数填错] net_receipt_cents.invoke({'paid_cents': '一万二千'})")
    try:
        net_receipt_cents.invoke({"paid_cents": "一万二千"})  # type: ignore[arg-type]
        print("  -> 竟然没报错，说明校验没生效（不符合预期）")
        validation_error = None
    except Exception as exc:
        # 演示代码：这里要把"任何"参数错误都展示出来，所以故意捕获宽异常
        validation_error = f"{type(exc).__name__}"
        print(f"  -> 被拦下：{type(exc).__name__}")
        print(f"     第一行错误：{str(exc).splitlines()[0][:120]}")

    # --- 消息流转：tool_calls 与 tool_call_id 必须配对 ---
    print("\n[消息流转] HumanMessage -> AIMessage(tool_calls) -> ToolMessage")
    call_id = "call_demo_0001"
    messages = [
        HumanMessage(content="9 月净收款是多少？"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "net_receipt_cents",
                    "args": {"paid_cents": 12_345_00, "refund_cents": 2_345_00},
                    "id": call_id,
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content=str(ok), tool_call_id=call_id),
    ]
    for m in messages:
        extra = ""
        if isinstance(m, AIMessage) and m.tool_calls:
            extra = f"  tool_calls={[(c['name'], c['id']) for c in m.tool_calls]}"
        if isinstance(m, ToolMessage):
            extra = f"  tool_call_id={m.tool_call_id}"
        print(f"  {type(m).__name__:<14} content={m.content!r}{extra}")
    print(
        "  要点：模型只负责提出 tool_call（name + args），"
        "真正的执行发生在我们自己的代码里；\n"
        "        ToolMessage 靠 tool_call_id 与那次调用配对，配对错误会直接报错。"
    )

    return {"contracts": contracts, "validation_error": validation_error}


# ============================================================================
# 第 2 部分：自定义 LangGraph —— 状态 + 条件边 + 有界重试
# ----------------------------------------------------------------------------
# 这张小图刻意模仿本项目真实查询流程的形状：
#   信息不足 -> 去澄清；信息够 -> 生成 SQL -> 校验 -> 失败最多修一次 -> 成功 / 明确失败
# 它不含任何业务逻辑，只用来把流程图变成可运行、可断言的代码。
# ============================================================================


class DemoState(TypedDict):
    """图状态。

    ``log`` 用 ``Annotated[list[str], add]``：默认 reducer 是「覆盖」，
    加了 ``add`` 之后每次节点返回的列表会**累加**，这样能保留完整轨迹。
    """

    question: str
    needs_clarification: bool
    clarification: str | None
    draft_sql: str | None
    validation_errors: list[str]
    repair_count: int
    status: Literal["running", "awaiting_clarification", "succeeded", "failed"]
    log: Annotated[list[str], add]


# 服务端拥有的上限，模型和用户都改不了它。
MAX_REPAIR_COUNT = 1

# 允许列表之外的列名。真实项目里这份清单来自 SQLGlot 解析 + 视图列白名单，
# 这里用一个常量模拟，好在离线环境下也能复现「校验失败 -> 修复 -> 仍失败」。
DISALLOWED_COLUMNS = ("nonexistent_column", "another_bad_column")


def node_classify(state: DemoState) -> dict[str, Any]:
    """判断问题是否信息不足。

    真实项目里这一步是模型做的（结构化输出）。
    这里用关键词模拟，保持离线可复现。
    """
    q = state["question"]
    ambiguous = "销售额" in q or "业绩" in q
    return {
        "needs_clarification": ambiguous,
        "log": [f"classify: 问题={q!r} 需要澄清={ambiguous}"],
    }


def node_ask_clarification(state: DemoState) -> dict[str, Any]:
    """真实项目在这里会 interrupt 并落库等待用户补充；本样例只标记状态。"""
    return {
        "status": "awaiting_clarification",
        "clarification": "你说的「销售额」是指实付金额，还是期间净收款？",
        "log": ["ask_clarification: 暂停，等待用户补充口径"],
    }


def node_draft_sql(state: DemoState) -> dict[str, Any]:
    """生成候选 SQL。

    样例约定（这样整条链路都留在图内部，不会被外部注入绕过）：
    * 问题里带 ``bad-bad`` -> 产出**两处**坏列名，用来演示「修一次也修不完」；
    * 问题里带 ``bad``     -> 产出**一处**坏列名，用来演示「修一次就成功」；
    * 其它                -> 正常 SQL。
    """
    q = state["question"]
    if "bad-bad" in q:
        sql = "SELECT nonexistent_column, another_bad_column FROM analytics.sales_lines"
    elif "bad" in q:
        sql = (
            "SELECT channel, SUM(nonexistent_column) AS paid_cents "
            "FROM analytics.sales_lines GROUP BY channel"
        )
    else:
        sql = (
            "SELECT channel, SUM(paid_cents) AS paid_cents "
            "FROM analytics.sales_lines GROUP BY channel ORDER BY paid_cents DESC"
        )
    return {"draft_sql": sql, "log": [f"draft_sql: 生成 {len(sql)} 字符的 SQL"]}


def node_validate(state: DemoState) -> dict[str, Any]:
    """确定性校验。

    真实项目里这里是 SQLGlot 解析 AST + 允许列表 + 视图/列白名单。
    样例只检查列名是否在允许列表内。
    """
    sql = state["draft_sql"] or ""
    errors = [
        f"column_not_allowed: {col} 不在允许列白名单中" for col in DISALLOWED_COLUMNS if col in sql
    ]
    if errors:
        return {"validation_errors": errors, "log": [f"validate: 失败 {errors}"]}
    return {"validation_errors": [], "log": ["validate: 通过"]}


def node_repair(state: DemoState) -> dict[str, Any]:
    """修复一次。

    刻意**只**修第一个发现的坏列名 —— 这正是真实情况：
    一次修复未必能把所有问题修完。修复完还要再走一次校验，
    校验不过且额度用完，就必须明确失败，而不是硬凑一个结果。
    """
    sql = state["draft_sql"] or ""
    replaced: str | None = None
    for col in DISALLOWED_COLUMNS:
        if col in sql:
            sql = sql.replace(col, "paid_cents", 1)
            replaced = col
            break
    return {
        "draft_sql": sql,
        "repair_count": state["repair_count"] + 1,
        "log": [f"repair: 第 {state['repair_count'] + 1} 次修复，把 {replaced} 换成 paid_cents"],
    }


def node_succeed(state: DemoState) -> dict[str, Any]:
    return {"status": "succeeded", "log": ["done: 结果可用"]}


def node_fail(state: DemoState) -> dict[str, Any]:
    return {
        "status": "failed",
        "log": [f"fail: 修复 {state['repair_count']} 次后仍失败，明确结束"],
    }


def route_after_classify(state: DemoState) -> str:
    """条件边 1：信息是否完整。"""
    return "ask_clarification" if state["needs_clarification"] else "draft_sql"


def route_after_validate(state: DemoState) -> str:
    """条件边 2：校验通过就结束；失败且还有修复额度就修一次；否则明确失败。

    ``MAX_REPAIR_COUNT`` 是硬上限，所以这张图不会无限循环。
    """
    if not state["validation_errors"]:
        return "succeed"
    if state["repair_count"] < MAX_REPAIR_COUNT:
        return "repair"
    return "fail"


def build_demo_graph() -> Any:
    """按官方 Graph API 组装并编译这张小图。"""
    builder = StateGraph(DemoState)

    builder.add_node("classify", node_classify)
    builder.add_node("ask_clarification", node_ask_clarification)
    builder.add_node("draft_sql", node_draft_sql)
    builder.add_node("validate", node_validate)
    builder.add_node("repair", node_repair)
    builder.add_node("succeed", node_succeed)
    builder.add_node("fail", node_fail)

    builder.add_edge(START, "classify")

    # 条件边 1：澄清分支
    builder.add_conditional_edges(
        "classify",
        route_after_classify,
        {"ask_clarification": "ask_clarification", "draft_sql": "draft_sql"},
    )
    builder.add_edge("ask_clarification", END)

    builder.add_edge("draft_sql", "validate")

    # 条件边 2：有界修复
    builder.add_conditional_edges(
        "validate",
        route_after_validate,
        {"succeed": "succeed", "repair": "repair", "fail": "fail"},
    )
    builder.add_edge("repair", "validate")
    builder.add_edge("succeed", END)
    builder.add_edge("fail", END)

    return builder.compile()


def run_demo_graph(question: str) -> dict[str, Any]:
    """跑一次图，返回最终状态（含完整轨迹 log）。"""
    graph = build_demo_graph()
    return graph.invoke(
        {
            "question": question,
            "needs_clarification": False,
            "clarification": None,
            "draft_sql": None,
            "validation_errors": [],
            "repair_count": 0,
            "status": "running",
            "log": [],
        }
    )


def demo_graph() -> dict[str, Any]:
    print("\n" + "=" * 74)
    print("第 2 部分：LangGraph StateGraph —— 条件边 + 有界重试")
    print("=" * 74)
    print(
        "\n图结构：\n"
        "  START -> classify\n"
        "  classify -[条件边: 信息是否完整]-> ask_clarification | draft_sql\n"
        "  ask_clarification -> END\n"
        "  draft_sql -> validate\n"
        "  validate -[条件边: 校验结果 + 剩余修复额度]-> succeed | repair | fail\n"
        "  repair -> validate   (最多回边 MAX_REPAIR_COUNT=1 次)\n"
        "  succeed/fail -> END\n"
    )

    scenarios = [
        ("正常路径", "2026 年 9 月各渠道实付金额是多少？"),
        ("信息不足", "上月销售额多少？"),
        ("一次修复后成功", "bad 问题：查上周各渠道实付金额"),
    ]

    results: dict[str, Any] = {}
    for label, question in scenarios:
        out = run_demo_graph(question)
        results[label] = out
        print("-" * 74)
        print(f"场景：{label}")
        print(f"  问题      : {question}")
        print(f"  最终状态  : {out['status']}")
        print(f"  修复次数  : {out['repair_count']} (上限 {MAX_REPAIR_COUNT})")
        if out.get("clarification"):
            print(f"  澄清追问  : {out['clarification']}")
        if out.get("draft_sql"):
            print(f"  最终 SQL  : {out['draft_sql'][:88]}")
        print("  节点轨迹  :")
        for line in out["log"]:
            print(f"      · {line}")

    # 再跑一个「修复一次仍然失败」的场景，证明上限真的会停。
    # 注意：不要把 draft_sql 从外部注入 —— node_draft_sql 会覆盖它。
    # 让场景完全在图内部产生，才说明约束是在流程里生效的。
    print("-" * 74)
    print("场景：修复上限确实生效（SQL 里有两处坏列名，修一次修不完）")
    stuck = run_demo_graph("bad-bad 问题：查一下各渠道金额")
    print(f"  最终状态  : {stuck['status']}")
    print(f"  修复次数  : {stuck['repair_count']} -> 到达上限后不再回边，直接 fail")
    print("  节点轨迹  :")
    for line in stuck["log"]:
        print(f"      · {line}")
    results["修复上限"] = stuck

    return results


# ============================================================================
# 第 3 部分（可选）：预构建 create_agent 与自定义图的差别
# ----------------------------------------------------------------------------
# create_agent 把「模型 -> 工具 -> 模型」的循环整个封装好了；
# 代价是你无法插入确定性的校验、权限和修复次数上限。
# 本项目主流程因此选择自定义图（见 docs/decisions.md）。
# 这里只在配置了真实 Key 时才跑，避免把没跑过的东西说成跑过。
# ============================================================================


def demo_prebuilt_agent_optional() -> dict[str, Any]:
    print("\n" + "=" * 74)
    print("第 3 部分（可选）：预构建 create_agent 冒烟")
    print("=" * 74)

    # 统一从 Settings 读配置 —— **不要**直接读 os.environ。
    # 早先这里写的是 os.environ.get("SHOPLENS_DEEPSEEK_API_KEY")，等于绕过 .env：
    # 同一个 Key，冒烟脚本读得到、这里读不到，于是被报成「未配置」。
    # 一个项目里两处读同一份配置、口径不一致，是最难查的一类问题。
    from app.config import get_settings

    settings = get_settings()

    if not settings.deepseek_api_key_configured:
        print(
            "\n  状态：未验证（未配置 SHOPLENS_DEEPSEEK_API_KEY）\n"
            "  说明：create_agent 需要一个会输出 tool_calls 的真实模型。\n"
            "        没有 Key 时这里不会假装跑过。开启方式：\n"
            "          在项目根目录 .env 里加一行 SHOPLENS_DEEPSEEK_API_KEY=sk-xxxx\n"
            "        然后重新运行本脚本。"
        )
        return {"create_agent_smoke": "unverified_no_api_key"}

    # Key 配了、但 provider 还是 offline：明确报「跳过」，而不是当成「未验证」。
    # 两者含义不同 —— 前者是「你不让我跑」，后者是「我没法跑」。
    if settings.model_provider != "deepseek":
        print(
            f"\n  状态：跳过（Key 已配置，但 SHOPLENS_MODEL_PROVIDER={settings.model_provider!r}）\n"
            "  说明：这一段会真的发网络请求、消耗 token，所以不随 .env 里的 Key 自动开启。\n"
            "        想跑就显式切一次（只对这一次生效）：\n"
            "          SHOPLENS_MODEL_PROVIDER=deepseek python scripts/learning_demo.py"
        )
        return {"create_agent_smoke": "skipped_provider_offline"}

    try:
        from langchain.agents import create_agent
        from langchain_deepseek import ChatDeepSeek

        model = ChatDeepSeek(
            model=settings.deepseek_model,
            api_key=settings.deepseek_api_key.get_secret_value(),
            api_base=settings.deepseek_base_url,
        )

        agent = create_agent(
            model=model,
            tools=LEARNING_TOOLS,
            system_prompt=("你是金额计算助手。需要计算净收款时必须调用工具，不要自己心算。"),
        )
        result = agent.invoke(
            {"messages": [{"role": "user", "content": "1234500 分减 234500 分是多少？"}]}
        )
        print(f"\n  调用成功，消息条数：{len(result['messages'])}")
        print(f"  最后一条：{result['messages'][-1].content[:200]}")
        return {"create_agent_smoke": "verified", "message_count": len(result["messages"])}
    except Exception as exc:
        # 冒烟脚本：把失败原样报出来比抛栈更有用
        print(f"\n  调用失败：{type(exc).__name__}: {str(exc)[:200]}")
        return {"create_agent_smoke": f"failed:{type(exc).__name__}"}


def main() -> int:
    print(f"Python {sys.version.split()[0]}")
    summary = {
        "tools": demo_tool_contract(),
        "graph": demo_graph(),
        "prebuilt": demo_prebuilt_agent_optional(),
    }

    print("\n" + "=" * 74)
    print("小结")
    print("=" * 74)
    print(
        "· @tool 的关键产物是「名字 + 描述 + 参数 JSON Schema」这份契约；\n"
        "  校验由 Pydantic 在代码侧完成，不依赖模型自觉。\n"
        "· LangGraph 的关键是「状态 schema + reducer + 条件边」；\n"
        "  把 repair_count 放进状态并由服务端递增，才能保证重试次数有界。\n"
        "· create_agent = 模型 + 工具 + 提示词，循环由框架执行；\n"
        "  适合探索型任务，不适合必须插入确定性校验与权限边界的流程。"
    )
    print(f"\n（本文件为阶段 0 学习样例，不参与业务逻辑。结构化摘要键：{sorted(summary)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
