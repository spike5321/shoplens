"""冒烟脚本自身的测试。

为什么值得测：`scripts/smoke_real_model.py` 是用来给阶段 3 兜底的工具。
如果它自己坏了（比如 stub 逻辑写错、或者没 Key 时静默返回 0），
它会给出**假的通过信号** —— 那比没有冒烟更糟。

所以这里固定三件事：
1. stub 模式下四个检查项必须真的通过。
2. stub 服务端的两轮 tool_call 行为是确定性的（第一轮回 tool_call，第二轮回文字）。
3. 没有 Key 时必须返回退出码 2（未验证），而不是 0（通过）。
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from conftest import load_module_from_path


@pytest.fixture(scope="module")
def smoke():
    return load_module_from_path("smoke_real_model", "scripts/smoke_real_model.py")


class _FakeBound:
    """假的「已绑定工具的模型」：按顺序吐出预设响应，并记住调用过几次。"""

    def __init__(
        self,
        responses: list[AIMessage],
        capture: list[dict] | None = None,
        outbound: dict | None = None,
    ) -> None:
        self._responses = list(responses)
        self._capture = capture
        self._outbound = outbound
        self.seen: list[list[object]] = []

    def invoke(self, messages):
        self.seen.append(list(messages))
        # 模仿真实 stub：invoke 期间把出站请求体记进抓包列表。
        # 顺序很重要 —— 检查项是在 invoke 前量 before 长度、invoke 后取切片的。
        if self._capture is not None:
            self._capture.append(self._outbound if self._outbound is not None else {})
        if not self._responses:
            raise AssertionError("假模型被调用的次数超过了预设响应数量")
        return self._responses.pop(0)


class _FakeModel:
    """只有 bind_tools 的假模型，用来单独驱动检查项，不联网、不花钱。"""

    def __init__(
        self,
        responses: list[AIMessage],
        capture: list[dict] | None = None,
        outbound: dict | None = None,
    ) -> None:
        self.bound = _FakeBound(responses, capture, outbound)

    # 签名要与真实模型一致；tools 参数这里用不到
    def bind_tools(self, tools):
        return self.bound


def _tool_call_payload(smoke):
    return [
        {
            "name": "net_receipt_cents",
            "args": {
                "paid_cents": smoke.EXPECTED_PAID_CENTS,
                "refund_cents": smoke.EXPECTED_REFUND_CENTS,
            },
            "id": "call_fake_1",
        }
    ]


def test_stub_mode_passes_all_four_checks(smoke, capsys):
    """--stub：协议层自证必须 4/4 通过。"""
    import sys

    original = sys.argv
    sys.argv = ["smoke_real_model.py", "--stub"]
    try:
        exit_code = smoke.main()
    finally:
        sys.argv = original

    out = capsys.readouterr().out
    assert exit_code == 0, out
    assert "结果：4/4 通过" in out
    assert "PASS" in out
    assert "FAIL" not in out


def test_stub_response_is_deterministic_two_turn(smoke):
    """第一轮必须回 tool_call，第二轮（带 role=tool）必须回文字。"""
    system_marker = "[[STUB:TOOL_CALL]] 需要算净收款时必须调用工具。"

    first = smoke.build_stub_response(
        {
            "messages": [
                {"role": "system", "content": system_marker},
                {"role": "user", "content": "算净收款"},
            ]
        }
    )
    calls = first["choices"][0]["message"]["tool_calls"]
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "net_receipt_cents"

    import json as _json

    args = _json.loads(calls[0]["function"]["arguments"])
    assert args == {
        "paid_cents": smoke.EXPECTED_PAID_CENTS,
        "refund_cents": smoke.EXPECTED_REFUND_CENTS,
    }

    second = smoke.build_stub_response(
        {
            "messages": [
                {"role": "system", "content": system_marker},
                {"role": "user", "content": "算净收款"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": calls,
                },
                {"role": "tool", "tool_call_id": "call_stub_1", "content": "1000000"},
            ]
        }
    )
    message = second["choices"][0]["message"]
    assert not message.get("tool_calls"), "第二轮不该再提 tool_call"
    assert "1000000" in message["content"]


def test_stub_structured_plan_respects_clarify_marker(smoke):
    """结构化输出有两条可能路径：json_schema（response_format）和 function_calling（tools）。

    两条都要能走通，否则「我验证过结构化输出」就是空话。
    """
    response_format = {"type": "json_schema", "json_schema": {"name": "SmokePlan"}}

    ok = smoke.build_stub_response(
        {
            "messages": [{"role": "system", "content": "[[STUB:PLAN_OK]]"}],
            "response_format": response_format,
        }
    )
    message = ok["choices"][0]["message"]
    assert not message.get("tool_calls"), "走 response_format 时不该再提 tool_call"
    assert '"needs_clarification": false' in message["content"]

    clarify = smoke.build_stub_response(
        {
            "messages": [{"role": "system", "content": "[[STUB:PLAN_CLARIFY]]"}],
            "response_format": response_format,
        }
    )
    content = clarify["choices"][0]["message"]["content"]
    assert '"needs_clarification": true' in content
    assert "实付金额" in content


def test_stub_structured_plan_via_function_calling_path(smoke):
    """同一份计划也必须能通过 tool_call 的方式返回。"""
    import json as _json

    payload = smoke.build_stub_response(
        {
            "messages": [{"role": "system", "content": "[[STUB:PLAN_OK]]"}],
            "tools": [{"type": "function", "function": {"name": "SmokePlan", "parameters": {}}}],
        }
    )
    message = payload["choices"][0]["message"]
    calls = message["tool_calls"]
    assert calls[0]["function"]["name"] == "SmokePlan"
    assert _json.loads(calls[0]["function"]["arguments"])["metric_id"] == "paid_amount"


def test_outbound_tools_schema_carries_extra_forbid(smoke):
    """出站 schema 必须收紧到 additionalProperties=false。

    如果哪天有人把工具改回默认（Pydantic v2 默认不加这个字段），
    真实模型填错参数名就会被静默忽略 —— 这条测试负责拦住这种回退。
    """
    params = smoke.NET_RECEIPT_TOOL_SCHEMA["function"]["parameters"]
    assert params["additionalProperties"] is False
    assert params["required"] == ["paid_cents"]


def test_missing_key_reports_unverified_with_exit_code_2(smoke, monkeypatch, capsys):
    """没 Key 时必须是「未验证 / 退出码 2」，不能返回 0 让人以为通过了。"""
    import app.config as app_config

    monkeypatch.setattr(
        app_config,
        "_settings",
        app_config.Settings(_env_file=None, deepseek_api_key=""),  # type: ignore[call-arg]
        raising=False,
    )

    import sys

    original = sys.argv
    sys.argv = ["smoke_real_model.py"]
    try:
        exit_code = smoke.main()
    finally:
        sys.argv = original

    out = capsys.readouterr().out
    assert exit_code == 2, out
    assert "未验证" in out
    # 必须明确否认"用 stub 冒充真实模型"这件事
    assert "不会用 stub 的结果冒充" in out


# ---------------------------------------------------------------------------
# 回归测试：真实模式下「出站断言」必须让路，而不是把自己卡死
#
# 背景（真犯过的错）：早先版本在真实模式用一个空的 _RecordingList 冒充抓包结果，
# 于是 C1/C3 的「出站请求」断言在真实模式下**必然**失败。
# 表现是：真实模型明明正确返回了 tool_call，却被判 FAIL，
# 而我可能把这条 FAIL 当成「模型不行」写进文档 —— 假阴性比假阳性更危险，
# 因为它会让你去修一个根本不存在的问题。
# ---------------------------------------------------------------------------


def test_c1_passes_in_real_mode_without_request_capture(smoke):
    """真实模式（无抓包点）：语义组通过就应 PASS，出站组标为「本模式不覆盖」。"""
    model = _FakeModel([AIMessage(content="", tool_calls=_tool_call_payload(smoke))])

    res = smoke.check_tool_call(model, None)

    assert res.passed, res.detail
    assert any("真实模式无抓包点" in n for n in res.notes), res.notes


def test_c1_still_asserts_outbound_when_capture_available(smoke):
    """stub 模式（有抓包点）：出站组必须还在生效 —— 别把断言一起改没了。"""
    capture: list[dict] = []
    model = _FakeModel(
        [AIMessage(content="", tool_calls=_tool_call_payload(smoke))],
        capture=capture,
        outbound={"messages": [{"role": "user", "content": "x"}]},  # 没有 tools 字段
    )

    res = smoke.check_tool_call(model, capture)

    assert not res.passed
    assert "没有 tools 字段" in res.detail


def test_c1_still_rejects_loosened_schema_in_stub_mode(smoke):
    """契约被放宽（缺 additionalProperties=false）必须被拦住，否则这条断言是摆设。"""
    capture: list[dict] = []
    model = _FakeModel(
        [AIMessage(content="", tool_calls=_tool_call_payload(smoke))],
        capture=capture,
        outbound={
            "tools": [
                {
                    "function": {
                        "name": "net_receipt_cents",
                        "parameters": {"type": "object", "properties": {}},
                    }
                }
            ]
        },
    )

    res = smoke.check_tool_call(model, capture)

    assert not res.passed
    assert "additionalProperties" in res.detail


def test_c3_still_asserts_tool_message_pairing_in_stub_mode(smoke):
    """stub 模式：role=tool 消息没有配对必须判失败。"""
    capture: list[dict] = []
    model = _FakeModel(
        [
            AIMessage(content="", tool_calls=_tool_call_payload(smoke)),
            AIMessage(content="净收款是 1000000 分。"),
        ],
        capture=capture,
        outbound={"messages": [{"role": "assistant", "content": ""}]},  # 缺 role=tool
    )

    res = smoke.check_tool_roundtrip(model, capture)

    assert not res.passed
    assert "role=tool" in res.detail


def test_c3_passes_in_real_mode_when_loop_terminates(smoke):
    """真实模式：第一轮 tool_call、第二轮给文字 —— 闭环成立即 PASS。"""
    model = _FakeModel(
        [
            AIMessage(content="", tool_calls=_tool_call_payload(smoke)),
            AIMessage(content="净收款是 1000000 分。"),
        ]
    )

    res = smoke.check_tool_roundtrip(model, None)

    assert res.passed, res.detail
    assert any("真实模式无抓包点" in n for n in res.notes), res.notes


def test_c3_fails_when_loop_does_not_terminate(smoke):
    """第二轮还在要工具调用 = 闭环没终止，必须判失败。"""
    model = _FakeModel(
        [
            AIMessage(content="", tool_calls=_tool_call_payload(smoke)),
            AIMessage(content="", tool_calls=_tool_call_payload(smoke)),
        ]
    )

    res = smoke.check_tool_roundtrip(model, None)

    assert not res.passed
    assert "闭环没有终止" in res.detail


def test_c3_prompt_gives_concrete_numbers(smoke):
    """C3 的提示词里必须出现两个具体数字。

    早先写的是「帮我算一下净收款。」—— 真实模型拒绝凭空编数字，于是不调工具，
    被判成失败；stub 无脑回 tool_call 反而"通过"。测试用例自身有歧义时，
    必须先修用例，不能去修模型。
    """
    model = _FakeModel(
        [
            AIMessage(content="", tool_calls=_tool_call_payload(smoke)),
            AIMessage(content="净收款是 1000000 分。"),
        ]
    )

    smoke.check_tool_roundtrip(model, None)

    first_round = model.bound.seen[0]
    user_text = " ".join(str(m.content) for m in first_round if getattr(m, "type", "") == "human")
    assert str(smoke.EXPECTED_PAID_CENTS) in user_text
    assert str(smoke.EXPECTED_REFUND_CENTS) in user_text
