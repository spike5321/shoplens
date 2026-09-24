"""验证 LangGraph 小图的分支走向和「修复次数有界」这个硬约束。

风险点：
* 条件边写反 -> 信息不足的问题被当成正常查询执行。
* 忘记给重试设上限 -> 图中的循环可以无限回边。
* repair_count 由状态累加而不是服务端递增 -> 上限形同虚设。

这三件事全都是真实的、会让「最多修复一次」变成一句空话的 bug。
"""

from __future__ import annotations


def _initial(question: str, **overrides) -> dict:
    state = {
        "question": question,
        "needs_clarification": False,
        "clarification": None,
        "draft_sql": None,
        "validation_errors": [],
        "repair_count": 0,
        "status": "running",
        "log": [],
    }
    state.update(overrides)
    return state


def test_normal_path_succeeds_without_repair(learning_demo):
    out = learning_demo.run_demo_graph("2026 年 9 月各渠道实付金额是多少？")
    assert out["status"] == "succeeded"
    assert out["repair_count"] == 0
    assert "analytics.sales_lines" in out["draft_sql"]


def test_ambiguous_question_routes_to_clarification(learning_demo):
    """「销售额」是歧义词，必须走澄清分支而不是直接查数。"""
    out = learning_demo.run_demo_graph("上月销售额多少？")
    assert out["status"] == "awaiting_clarification"
    assert out["clarification"]
    assert out["draft_sql"] is None, "信息不足时不应该生成 SQL"


def test_bad_column_triggers_exactly_one_repair_then_succeeds(learning_demo):
    out = learning_demo.run_demo_graph("bad 问题：查上周各渠道实付金额")
    assert out["status"] == "succeeded"
    assert out["repair_count"] == 1
    repairs = [line for line in out["log"] if line.startswith("repair:")]
    assert len(repairs) == 1, "修复节点只能进一次"


def test_repair_limit_is_enforced_and_loop_terminates(learning_demo):
    """构造一个「修一次也没修好」的情况，验证图会停，而不是一直转。

    注意：必须让坏 SQL 由图内的 node_draft_sql 产生。
    如果在初始状态里塞 draft_sql，node_draft_sql 会把它覆盖掉，
    测试看起来"注入了坏输入"，实际上根本没测到想测的分支。
    """
    out = learning_demo.run_demo_graph("bad-bad 问题：查一下各渠道金额")

    assert out["status"] == "failed"
    assert out["repair_count"] == learning_demo.MAX_REPAIR_COUNT == 1

    repairs = [line for line in out["log"] if line.startswith("repair:")]
    validates = [line for line in out["log"] if line.startswith("validate:")]
    assert len(repairs) == 1, "修复只能发生一次"
    # 首次校验 + 修复后校验 = 2 次；出现第 3 次说明回边没有被上限拦住
    assert len(validates) == 2, f"validate 进入次数异常: {validates}"
    assert "another_bad_column" in out["draft_sql"], "第二次校验必须仍能发现坏列名"


def test_state_log_uses_add_reducer(learning_demo):
    """log 字段用 Annotated[list[str], add]，多次返回值应该累加而不是覆盖。"""
    out = learning_demo.run_demo_graph("2026 年 9 月各渠道实付金额是多少？")
    assert len(out["log"]) >= 3, "轨迹被覆盖了，说明 reducer 没生效"
    assert any(line.startswith("classify:") for line in out["log"])
    assert any(line.startswith("draft_sql:") for line in out["log"])
    assert any(line.startswith("validate:") for line in out["log"])


def test_graph_compiles_and_is_reusable(learning_demo):
    """同一个编译结果可以重复调用，节点函数不应把结果写进模块级变量。"""
    graph = learning_demo.build_demo_graph()
    first = graph.invoke(_initial("查 9 月实付"))
    second = graph.invoke(_initial("查 9 月实付"))
    assert first["status"] == second["status"] == "succeeded"
    assert first["log"] == second["log"]
