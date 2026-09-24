"""验证 LangChain 工具的「契约」是真实存在、且真的会拦错误参数的。

这里测的是风险，不是行数：
* 如果有人把工具的参数类型改坏（比如 int 改成 str），模型填的数会被静默接受 —— 这是真风险。
* 如果有人给工具加了 `**kwargs`，多出来的字段会静默通过 —— 这也是真风险。
* 如果工具的 description 被清空，模型就失去选择依据 —— 同样需要被固定住。
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import ValidationError


def test_tool_contract_exposes_name_description_and_schema(learning_demo):
    info = learning_demo.inspect_tool_schema(learning_demo.net_receipt_cents)

    assert info["name"] == "net_receipt_cents"
    assert info["description_first_line"], "描述不能为空，模型靠它决定是否调用"

    # 参数类型必须真的是 integer（单位是「分」，不能是浮点或字符串）
    assert info["parameters"]["paid_cents"]["type"] == "integer"
    assert info["parameters"]["refund_cents"]["type"] == "integer"

    # paid_cents 必填，refund_cents 有默认值所以不在 required 里
    assert info["required"] == ["paid_cents"]

    # 多余字段必须被禁止，避免模型乱塞参数被静默吞掉
    assert info["additionalProperties"] is False


def test_tool_computes_net_receipt_in_cents(learning_demo):
    # 12,345.00 元 - 2,345.00 元 = 10,000.00 元 = 1_000_000 分
    result = learning_demo.net_receipt_cents.invoke(
        {"paid_cents": 1_234_500, "refund_cents": 234_500}
    )
    assert result == 1_000_000


def test_tool_allows_refund_larger_than_paid(learning_demo):
    """跨期退款时，本期退款可以大于本期实付，净收款允许为负。

    这是业务口径，不是漏洞：退款按完成时间入账，本期可能退上期的款。
    如果有人「顺手」加了 refund <= paid 的校验，这条测试会失败。
    """
    result = learning_demo.net_receipt_cents.invoke({"paid_cents": 100, "refund_cents": 500})
    assert result == -400


def test_tool_rejects_negative_amount(learning_demo):
    """负数金额是业务规则拒绝的，异常来自函数体本身。"""
    with pytest.raises(ValueError) as excinfo:
        learning_demo.net_receipt_cents.invoke({"paid_cents": -1})
    assert "不能为负" in str(excinfo.value)


def test_tool_rejects_wrong_parameter_type(learning_demo):
    """模型把数字写成中文时，必须在执行前被 Pydantic 拦下。

    这也证明了「校验发生在代码侧」：即使模型给了合法 JSON，
    参数类型不对也进不了函数体。
    """
    with pytest.raises(ValidationError) as excinfo:
        learning_demo.net_receipt_cents.invoke({"paid_cents": "一万二千"})
    assert "paid_cents" in str(excinfo.value)


def test_tool_rejects_unknown_parameter(learning_demo):
    """多传的参数名必须报错，而不是被静默丢弃。

    这条是有来历的：Pydantic v2 生成的 JSON Schema **默认不含**
    ``additionalProperties: false``，多余字段会被直接忽略，
    工具照常返回一个看起来正常的数字 —— 查数系统里最危险的一类 bug。
    所以 args_schema 上显式加了 ``extra="forbid"``。
    """
    info = learning_demo.inspect_tool_schema(learning_demo.net_receipt_cents)
    assert info["additionalProperties"] is False, (
        "schema 没有禁止多余字段，模型填错参数名会被静默忽略"
    )

    with pytest.raises(ValidationError) as excinfo:
        learning_demo.net_receipt_cents.invoke({"paid_cents": 100, "currency": "CNY"})
    assert "currency" in str(excinfo.value)


def test_cents_formatting_never_uses_float(learning_demo):
    """金额一律整数分；格式化只做整除取余，不引入浮点误差。"""
    assert learning_demo.format_cents_as_cny.invoke({"amount_cents": 123456}) == "¥1234.56"
    assert learning_demo.format_cents_as_cny.invoke({"amount_cents": 5}) == "¥0.05"
    assert learning_demo.format_cents_as_cny.invoke({"amount_cents": -400}) == "-¥4.00"


def test_ai_message_tool_call_pairs_with_tool_message():
    """tool_call 与 ToolMessage 靠 id 配对，这是消息结构的硬约束。"""
    call_id = "call_abc"
    ai = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "net_receipt_cents",
                "args": {"paid_cents": 1000, "refund_cents": 300},
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )
    assert ai.tool_calls[0]["name"] == "net_receipt_cents"
    assert ai.tool_calls[0]["args"] == {"paid_cents": 1000, "refund_cents": 300}

    tool_msg = ToolMessage(content="700", tool_call_id=call_id)
    assert tool_msg.tool_call_id == ai.tool_calls[0]["id"]

    human = HumanMessage(content="9 月净收款是多少？")
    assert human.type == "human"
