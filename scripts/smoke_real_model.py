"""真实模型可选冒烟（阶段 0 交付项之一：真实模型另做可选冒烟）。

这个脚本回答两个**不同**的问题，并且刻意不让答案混在一起：

    Q1「我们这一侧的接线对不对？」
       —— 请求怎么序列化、tools 有没有正确带上、tool_call 怎么解析、
          结构化输出怎么落到 Pydantic 模型上。
       用 ``--stub`` 模式验证：本地起一个 OpenAI 兼容协议的假服务端，
       把适配器指过去。这**不能**证明任何真实服务商的行为。

    Q2「真实服务商到底行不行？」
       —— 用真实 Key 打真实端点。
       没有 Key 时脚本明确报告「未验证」并以退出码 2 结束，
       绝不伪装成跑过、也绝不拿 Q1 的结果冒充 Q2。

用法::

    # Q1：协议层自证，不需要任何 Key
    python scripts/smoke_real_model.py --stub

    # Q2：真实模型冒烟，需要 Key（只从环境变量读，不要写进代码）
    set SHOPLENS_DEEPSEEK_API_KEY=sk-xxxx
    python scripts/smoke_real_model.py

退出码：0 = 全部通过 / 1 = 有检查项失败 / 2 = 未验证（缺 Key，不是失败）

检查项（四项都是阶段 3 依赖的**模型能力前提**，不是业务流程本身）：

    C1 工具调用     —— 会选对工具，且金额按整数分填对
    C2 结构化输出   —— 能按给定 Pydantic 模型返回，不多不少
    C3 多轮闭环     —— tool_call -> ToolMessage -> 最终回答 能走通
    C4 歧义澄清     —— 信息不足时返回"需要澄清"，而不是硬猜一个期间

业务流程（解析->澄清->生成 SQL->校验->执行->修复）本身是阶段 3 的范围，
本脚本不做。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))

# ============================================================================
# 冒烟用最小 schema
# ----------------------------------------------------------------------------
# 注意：正式的 QueryPlan 在阶段 3 定义（含指标 ID、时间范围、Top N、维度等）。
# 这里只保留「能不能按模型返回结构化对象」所必需的最小字段。
# ============================================================================


class SmokePlan(BaseModel):
    """阶段 3 查询计划的最小替身，只用于冒烟。"""

    model_config = ConfigDict(extra="forbid")

    metric_id: Literal["paid_amount", "refund_amount", "net_receipt", "paid_order_count", "aov"] = (
        Field(description="指标 ID，只能是给定的五个之一")
    )
    group_by: list[Literal["date", "channel", "category"]] = Field(
        default_factory=list, description="分组维度，可为空"
    )
    start_date: str | None = Field(default=None, description="开始日期，格式 YYYY-MM-DD")
    end_date: str | None = Field(default=None, description="结束日期，格式 YYYY-MM-DD")
    needs_clarification: bool = Field(
        default=False, description="信息不足时为 true，并在 clarification_question 里说明缺什么"
    )
    clarification_question: str | None = Field(default=None, description="需要向用户追问的原话")


# 与 scripts/learning_demo.py 相同的确定性工具，用于 C1 / C3。
class NetReceiptArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paid_cents: int = Field(description="期间内成功支付订单实付金额合计，单位：分")
    refund_cents: int = Field(default=0, description="同期成功退款金额合计，单位：分")


def net_receipt_cents(paid_cents: int, refund_cents: int = 0) -> int:
    """计算期间净收款，单位「分」= 同期实付 − 同期成功退款。"""
    return paid_cents - refund_cents


NET_RECEIPT_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "net_receipt_cents",
        "description": net_receipt_cents.__doc__,
        "parameters": NetReceiptArgs.model_json_schema(),
    },
}

# 只从环境变量读，绝不写死
EXPECTED_PAID_CENTS = 12_345_00
EXPECTED_REFUND_CENTS = 2_345_00
EXPECTED_NET_CENTS = EXPECTED_PAID_CENTS - EXPECTED_REFUND_CENTS


# ============================================================================
# 本地 OpenAI 兼容 stub
# ============================================================================


def _system_text(body: dict[str, Any]) -> str:
    for msg in body.get("messages", []):
        if msg.get("role") == "system":
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def _has_tool_result(body: dict[str, Any]) -> bool:
    return any(m.get("role") == "tool" for m in body.get("messages", []))


def _chat_completion(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "chatcmpl-stub",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "stub-model",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _plan_args(clarify: bool) -> dict[str, Any]:
    if clarify:
        return {
            "metric_id": "net_receipt",
            "group_by": [],
            "start_date": None,
            "end_date": None,
            "needs_clarification": True,
            "clarification_question": "你说的「销售额」是指实付金额，还是期间净收款？",
        }
    return {
        "metric_id": "paid_amount",
        "group_by": ["channel"],
        "start_date": "2026-09-01",
        "end_date": "2026-10-01",
        "needs_clarification": False,
        "clarification_question": None,
    }


def build_stub_response(body: dict[str, Any]) -> dict[str, Any]:
    """按 system 里的标记决定回什么。

    标记由冒烟脚本自己写进 system prompt，因此这里是确定性的，
    不会出现"假服务端猜错意图导致测试假通过"。
    """
    system = _system_text(body)

    # --- C1 / C3：工具调用 ---
    if "[[STUB:TOOL_CALL]]" in system:
        if not _has_tool_result(body):
            # 第一轮：提出 tool_call
            return _chat_completion(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_stub_1",
                            "type": "function",
                            "function": {
                                "name": "net_receipt_cents",
                                "arguments": json.dumps(
                                    {
                                        "paid_cents": EXPECTED_PAID_CENTS,
                                        "refund_cents": EXPECTED_REFUND_CENTS,
                                    }
                                ),
                            },
                        }
                    ],
                }
            )
        # 第二轮：已经有 tool 结果了，给出最终回答
        tool_content = next(
            m.get("content") for m in reversed(body.get("messages", [])) if m.get("role") == "tool"
        )
        return _chat_completion({"role": "assistant", "content": f"净收款是 {tool_content} 分。"})

    # --- C2 / C4：结构化输出 ---
    if "[[STUB:PLAN_OK]]" in system or "[[STUB:PLAN_CLARIFY]]" in system:
        clarify = "[[STUB:PLAN_CLARIFY]]" in system
        args = _plan_args(clarify)

        # with_structured_output 可能走 json_schema，也可能走 function_calling，
        # 两条路都要能覆盖，否则"我验证过结构化输出"就是一句空话。
        if body.get("response_format") is not None:
            return _chat_completion(
                {"role": "assistant", "content": json.dumps(args, ensure_ascii=False)}
            )
        if body.get("tools"):
            first = body["tools"][0]["function"]["name"]
            return _chat_completion(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_stub_plan",
                            "type": "function",
                            "function": {
                                "name": first,
                                "arguments": json.dumps(args, ensure_ascii=False),
                            },
                        }
                    ],
                }
            )
        return _chat_completion(
            {"role": "assistant", "content": "stub: 既没有 tools 也没有 response_format"}
        )

    return _chat_completion({"role": "assistant", "content": "stub: 未知标记"})


class _StubHandler(BaseHTTPRequestHandler):
    # do_POST 这个名字由 BaseHTTPRequestHandler 的调度机制决定（它按 "do_" + method
    # 反射调用），不能改成 PEP8 的小写风格，否则 stub 服务端直接 501。
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            body = {}
        # 记录下来，供断言"我们发出去的请求长什么样"
        self.server.requests.append(body)  # type: ignore[attr-defined]

        payload = json.dumps(build_stub_response(body)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: Any) -> None:
        """静音 stub 的访问日志。"""


def start_stub() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    server.requests = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}/v1"


# ============================================================================
# 模型构建
# ============================================================================


def build_model(*, stub_base_url: str | None):
    """返回 (模型实例, 模式说明)。

    两种模式都用 ``BaseChatOpenAI`` 家族的真实适配器 —— 这正是要验证的东西。
    stub 模式只是把 base_url 指到本地假服务端。
    """
    from app.config import get_settings

    settings = get_settings()

    if stub_base_url is not None:
        from langchain_deepseek import ChatDeepSeek

        model = ChatDeepSeek(
            model=settings.deepseek_model,
            api_key="stub-key-not-a-secret",
            api_base=stub_base_url,
            max_retries=0,
            timeout=15,
        )
        return model, f"stub ({stub_base_url}) 适配器=ChatDeepSeek"

    provider = settings.model_provider

    # 先判断"有没有 Key"，再判断"provider 选对没有"。
    # 顺序反了会把"没配 Key"误报成"配置错误"，而这两件事的处理方式完全不同：
    # 前者是「未验证、可以继续离线工作」，后者才是「你写错了」。
    if not settings.deepseek_api_key_configured:
        raise RuntimeError("missing-key")

    if provider != "deepseek":
        raise RuntimeError(
            f"已提供 SHOPLENS_DEEPSEEK_API_KEY，但 SHOPLENS_MODEL_PROVIDER={provider!r}。"
            "请改成 deepseek，否则真实模型不会被调用。\n"
            "  方式一（推荐，只对这一次生效，不动项目默认值）：\n"
            "    SHOPLENS_MODEL_PROVIDER=deepseek python scripts/smoke_real_model.py\n"
            "  方式二（把项目默认切到真实模型）：改 .env 里 SHOPLENS_MODEL_PROVIDER=deepseek\n"
            "  两种都需要你明确表态，脚本不会自己替你切 —— 免得跑个测试就把 token 花掉。"
        )

    from langchain_deepseek import ChatDeepSeek

    model = ChatDeepSeek(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key.get_secret_value(),
        api_base=settings.deepseek_base_url,
        max_retries=2,
        timeout=60,
    )
    return model, f"real:deepseek model={settings.deepseek_model}"


# ============================================================================
# 四个检查项
# ============================================================================


class Result:
    def __init__(self, name: str) -> None:
        self.name = name
        self.passed = False
        self.detail = ""
        self.notes: list[str] = []


def _to_messages(system: str, user: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def check_tool_call(model: Any, requests: list[dict[str, Any]] | None) -> Result:
    """C1：模型是否能选对工具、按整数分填对参数。

    断言分两组，因为两组能拿到的证据不同：

    * **语义组**（两种模式都能做）：是否返回 tool_call、工具名对不对、参数名与数值对不对。
    * **出站组**（只有 stub 模式能做）：请求体里 `tools` 是否真的带上、schema 是否收紧。

    真实模式下没有本地抓包点，出站组如实标为「本模式不覆盖」，**不判失败**。
    判失败等于用一条永远不可能成立的断言把自己卡死 —— 那是假阴性，不是严格。
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    res = Result("C1 工具调用")
    bound = model.bind_tools([NET_RECEIPT_TOOL_SCHEMA])
    before = len(requests) if requests is not None else 0
    ai = bound.invoke(
        [
            SystemMessage(content="[[STUB:TOOL_CALL]] 需要算净收款时必须调用工具，不要心算。"),
            HumanMessage(
                content=f"{EXPECTED_PAID_CENTS} 分的实付，退了 {EXPECTED_REFUND_CENTS} 分，净收款多少？"
            ),
        ]
    )

    calls = ai.tool_calls
    if not calls:
        res.detail = "模型没有返回任何 tool_call"
        return res
    call = calls[0]
    if call["name"] != "net_receipt_cents":
        res.detail = f"工具名不对：{call['name']}"
        return res
    args = call["args"]
    if args.get("paid_cents") != EXPECTED_PAID_CENTS:
        res.detail = f"paid_cents 不对：{args.get('paid_cents')!r}"
        return res
    if args.get("refund_cents") != EXPECTED_REFUND_CENTS:
        res.detail = f"refund_cents 不对：{args.get('refund_cents')!r}"
        return res

    # ---- 语义组已通过，下面做出站组 ----
    if requests is None:
        res.notes.append("出站 tools / schema 断言：真实模式无抓包点，由 --stub 模式覆盖")
    else:
        sent = requests[before:]
        if not sent:
            res.detail = "本地 stub 没捕获到出站请求（stub 自己坏了，不是模型问题）"
            return res
        tools = sent[0].get("tools")
        if not tools:
            res.detail = "出站请求里没有 tools 字段"
            return res
        params = tools[0]["function"]["parameters"]
        res.notes.append(f"出站 tools[0].function.name = {tools[0]['function']['name']}")
        if params.get("additionalProperties") is not False:
            res.detail = "出站 schema 缺少 additionalProperties=false（契约被放宽了）"
            return res
        res.notes.append("出站 schema 含 additionalProperties=false")

    res.passed = True
    res.detail = f"tool_call={call['name']}({args})"
    return res


def check_structured_output(model: Any) -> Result:
    """C2：能否按给定 Pydantic 模型返回，且不多不少。"""
    res = Result("C2 结构化输出")
    structured = model.with_structured_output(SmokePlan)
    plan = structured.invoke(
        _to_messages(
            "[[STUB:PLAN_OK]] 你是电商经营分析助手，把问题解析成查询计划。",
            "2026 年 9 月各渠道的实付金额是多少？",
        )
    )
    if not isinstance(plan, SmokePlan):
        res.detail = f"返回类型不是 SmokePlan，而是 {type(plan).__name__}"
        return res
    if plan.metric_id != "paid_amount":
        res.detail = f"metric_id 解析错：{plan.metric_id}"
        return res
    if plan.group_by != ["channel"]:
        res.detail = f"group_by 解析错：{plan.group_by}"
        return res
    if plan.needs_clarification:
        res.detail = "不该需要澄清却标了 needs_clarification"
        return res
    res.passed = True
    res.detail = f"metric_id={plan.metric_id} group_by={plan.group_by} 区间={plan.start_date}~{plan.end_date}"
    return res


def check_tool_roundtrip(model: Any, requests: list[dict[str, Any]] | None) -> Result:
    """C3：tool_call -> ToolMessage -> 最终回答 的完整闭环。

    **提示词里必须给出具体数字。** 早期写的是「帮我算一下净收款。」，
    真实模型拒绝凭空编数字、于是不调工具，被判成失败；
    而 stub 无脑回 tool_call，反而"通过"了。
    一个测试用例里藏着歧义，会让 stub 的顺从掩盖真实模型的正直 ——
    这是只有跑过真机才会暴露的问题。
    """
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    res = Result("C3 多轮 tool_call 闭环")
    bound = model.bind_tools([NET_RECEIPT_TOOL_SCHEMA])

    sys_prompt = "[[STUB:TOOL_CALL]] 需要算净收款时必须调用工具，不要自己心算。"
    user = f"实付 {EXPECTED_PAID_CENTS} 分，退款 {EXPECTED_REFUND_CENTS} 分，帮我算净收款。"

    first = bound.invoke(
        [
            SystemMessage(content=sys_prompt),
            HumanMessage(content=user),
        ]
    )
    if not first.tool_calls:
        res.detail = "第一轮没有 tool_call（已给出两个具体数字，调工具本应是明确的下一步）"
        return res
    call = first.tool_calls[0]

    # 真正执行工具的是我们自己的代码，不是模型
    tool_result = net_receipt_cents(**call["args"])
    if tool_result != EXPECTED_NET_CENTS:
        res.detail = f"工具计算结果不对：{tool_result}"
        return res

    before = len(requests) if requests is not None else 0
    final = bound.invoke(
        [
            SystemMessage(content=sys_prompt),
            HumanMessage(content=user),
            AIMessage(content="", tool_calls=first.tool_calls),
            ToolMessage(content=str(tool_result), tool_call_id=call["id"]),
        ]
    )

    # ---- 语义组：闭环是否终止。两种模式都要查 ----
    if final.tool_calls:
        res.detail = f"拿到工具结果后仍在请求工具调用（{len(final.tool_calls)} 个），闭环没有终止"
        return res
    if not final.content:
        res.detail = "第二轮没有产出文字回答"
        return res

    # ---- 出站组：role=tool 消息有没有配对。仅 stub 模式可查 ----
    if requests is None:
        res.notes.append("出站 role=tool 消息配对断言：真实模式无抓包点，由 --stub 模式覆盖")
    else:
        sent = requests[before:]
        if not sent:
            res.detail = "本地 stub 没捕获到第二轮出站请求（stub 自己坏了，不是模型问题）"
            return res
        tool_msgs = [m for m in sent[0].get("messages", []) if m.get("role") == "tool"]
        if not tool_msgs:
            res.detail = "第二轮出站请求里没有 role=tool 的消息，配对失败"
            return res
        if tool_msgs[0].get("tool_call_id") != call["id"]:
            res.detail = (
                f"tool_call_id 不匹配：{tool_msgs[0].get('tool_call_id')!r} != {call['id']!r}"
            )
            return res
        res.notes.append(f"出站 role=tool 消息配对成功：tool_call_id={call['id']}")

    res.passed = True
    res.detail = f"工具结果 {tool_result} 分已回喂，最终回答长度 {len(str(final.content))}"
    res.notes.append(f"最终回答：{str(final.content)[:80]}")
    return res


def check_clarification(model: Any) -> Result:
    """C4：信息不足时必须返回"需要澄清"，而不是硬猜一个期间。"""
    res = Result("C4 歧义澄清")
    structured = model.with_structured_output(SmokePlan)
    plan = structured.invoke(
        _to_messages(
            "[[STUB:PLAN_CLARIFY]] 你是电商经营分析助手。"
            "「销售额」是歧义词（实付金额 or 期间净收款），未指定期间也必须追问，不要自己假设。",
            "上月销售额多少？",
        )
    )
    if not isinstance(plan, SmokePlan):
        res.detail = f"返回类型不是 SmokePlan，而是 {type(plan).__name__}"
        return res
    if not plan.needs_clarification:
        res.detail = "面对歧义问题居然没有要求澄清（这正是最危险的失败模式）"
        return res
    if not plan.clarification_question:
        res.detail = "标了需要澄清但没给出追问内容"
        return res
    res.passed = True
    res.detail = f"追问：{plan.clarification_question[:60]}"
    return res


# ============================================================================
# 主流程
# ============================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description="真实模型可选冒烟 / 协议层自证")
    parser.add_argument(
        "--stub",
        action="store_true",
        help="用本地 OpenAI 兼容假服务端验证接线（不需要 Key，但**不能**证明真实服务商行为）",
    )
    args = parser.parse_args()

    server: ThreadingHTTPServer | None = None
    stub_url: str | None = None
    mode_label = ""

    try:
        if args.stub:
            server, stub_url = start_stub()
            mode_label = "协议层自证（本地 stub）"
        model, mode_label = build_model(stub_base_url=stub_url)
    except RuntimeError as exc:
        if "missing-key" in str(exc):
            print("=" * 74)
            print("真实模型冒烟：**未验证**")
            print("=" * 74)
            print(
                "\n  原因：未配置 SHOPLENS_DEEPSEEK_API_KEY。\n"
                "  这不是失败，是「没做」。本脚本不会用 stub 的结果冒充真实模型结果。\n\n"
                "  开启方式（Key 只放环境变量，不要发到聊天里、不要写进源码）：\n"
                "    1) 在项目根目录 .env 里加一行：\n"
                "         SHOPLENS_MODEL_PROVIDER=deepseek\n"
                "         SHOPLENS_DEEPSEEK_API_KEY=sk-你的key\n"
                "    2) 重新运行：python scripts/smoke_real_model.py\n\n"
                "  也可以先跑不依赖 Key 的那一半：\n"
                "    python scripts/smoke_real_model.py --stub\n"
            )
            if server is not None:
                server.shutdown()
            return 2
        print(f"配置错误：{exc}")
        if server is not None:
            server.shutdown()
        return 1

    # 真实模式下没有本地抓包点，就是 None —— 不用空列表占位。
    # 早先版本用了一个空的 _RecordingList 冒充"抓包结果"，
    # 结果是 C1/C3 的出站断言在真实模式下**必然**失败：那不是严格，是自造假阴性。
    requests: list[dict[str, Any]] | None = server.requests if server is not None else None

    print("=" * 74)
    print(f"模型冒烟：{mode_label}")
    print("=" * 74)
    if args.stub:
        print(
            "\n  注意：stub 模式只证明「我们这一侧的接线正确」——\n"
            "        请求序列化、tools 上协议、tool_call 解析、结构化输出解析。\n"
            "        它**不能**证明任何真实服务商的行为。真实模型仍需单独跑一次。"
        )
    else:
        print(
            "\n  注意：真实模式覆盖「模型行为 + 结构化输出解析」。\n"
            "        出站请求体（tools 是否上协议、schema 是否收紧、role=tool 配对）\n"
            "        没有本地抓包点，由 --stub 模式负责，两种模式结论互补、不可互相替代。"
        )

    checks = [
        ("C1 工具调用", lambda: check_tool_call(model, requests)),
        ("C2 结构化输出", lambda: check_structured_output(model)),
        ("C3 多轮闭环", lambda: check_tool_roundtrip(model, requests)),
        ("C4 歧义澄清", lambda: check_clarification(model)),
    ]

    results: list[Result] = []
    for label, fn in checks:
        started = time.perf_counter()
        try:
            res = fn()
        except Exception as exc:
            res = Result(label)
            res.detail = f"抛异常 {type(exc).__name__}: {str(exc)[:180]}"
        res.notes.append(f"耗时 {round((time.perf_counter() - started) * 1000)} ms")
        results.append(res)
        mark = "PASS" if res.passed else "FAIL"
        print(f"\n[{mark}] {res.name}")
        print(f"       {res.detail}")
        for note in res.notes:
            print(f"       · {note}")

    passed = sum(1 for r in results if r.passed)
    print("\n" + "=" * 74)
    print(f"结果：{passed}/{len(results)} 通过")
    print("=" * 74)

    if args.stub:
        print(
            "\n  本次只覆盖协议层。请在拿到 Key 后**再跑一次不带 --stub 的命令**，\n"
            "  那一次的结果才叫「真实模型已验证」。"
        )
    else:
        print(
            "\n  本次打的是真实端点。请在 docs/progress.md 里登记：\n"
            "  模型 ID、日期、通过项、失败项、耗时。\n"
            "  若与 --stub 的结果不一致，先查本文件里两组断言的分工说明，再下结论。"
        )

    if server is not None:
        server.shutdown()
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
