"""字段级加工血缘与复算清单测试。

场景（与题面一致）：
- 日报宽表 net_amount 由「发票净额路径」和「订单折让路径」两条独立路径汇合；
- 订单号 order_id 由订单表直传；
- 仓库宽表 remark_note 是与金额无关的备注列；
- 停用发票金额路径后，日报金额仍由订单折让路径的有效作业计算。
"""
from __future__ import annotations

import threading

import app.store as store_mod
from app.services.field_lineage import FieldLineageService
from app.services.impact_analysis import ImpactAnalysisService
from app.services.processing_path import ProcessingPathService
from app.services.recompute_todo import RecomputeTodoService


def _fresh() -> dict[str, object]:
    store_mod.store.reset()
    return {
        "field_lineage": FieldLineageService(),
        "processing_path": ProcessingPathService(),
        "recompute_todo": RecomputeTodoService(),
        "impact_analysis": ImpactAnalysisService(),
    }


def _rows(module: str) -> list:
    return store_mod.store.rows(module)


def _todo(asset: str, field: str):
    items = [
        row for row in _rows("recompute_todo")
        if row.get("目标资产") == asset and row.get("目标字段") == field
    ]
    return items[0] if items else None


def _path_id(name: str) -> int:
    return next(row["id"] for row in _rows("processing_path") if row.get("加工路径") == name)


# ══════════════════════════════════════════════════════════════════
# 字段传播路径视图
# ══════════════════════════════════════════════════════════════════

class TestFieldPropagationView:

    def test_two_routes_converge_on_same_field_and_keep_both(self):
        s = _fresh()
        view = s["field_lineage"].propagation_view("发票表", "invoice_net")
        # 发票净额经过发票作业汇入 net_amount
        assert view["path_count"] == 1
        assert view["paths"][0]["jobs"] == ["JOB_INVOICE_NET"]
        assert view["paths"][0]["terminal_field"] == "net_amount"

        view = s["field_lineage"].propagation_view("订单表", "discount_amount")
        assert view["paths"][0]["jobs"] == ["JOB_ORDER_DISCOUNT"]
        assert view["reachable_fields"] == [{"asset": "日报宽表", "field": "net_amount"}]

        # 向上溯源：net_amount 同时挂着发票与折让两条独立路径
        upstream = s["field_lineage"].trace_upstream("日报宽表", "net_amount")
        sources = {(route["source_asset"], route["source_field"]) for route in upstream["routes"]}
        assert ("发票表", "invoice_net") in sources
        assert ("订单表", "discount_amount") in sources
        assert len(upstream["routes"]) == 2

    def test_propagation_lists_jobs_for_each_route(self):
        s = _fresh()
        invoice_view = s["field_lineage"].propagation_view("发票表", "invoice_net")
        discount_view = s["field_lineage"].propagation_view("订单表", "discount_amount")
        assert invoice_view["jobs"] == ["JOB_INVOICE_NET"]
        assert discount_view["jobs"] == ["JOB_ORDER_DISCOUNT"]
        # 连接字段随每跳记录
        assert invoice_view["paths"][0]["join_fields"] == ["order_id"]
        assert discount_view["paths"][0]["join_fields"] == ["order_id"]

    def test_remark_column_is_outside_amount_recompute_set(self):
        s = _fresh()
        # 备注列与金额列分属不同传播链，金额变更不可达备注列
        amount_view = s["field_lineage"].propagation_view("发票表", "invoice_net")
        reached = {(item["asset"], item["field"]) for item in amount_view["reachable_fields"]}
        assert ("仓库宽表", "remark_note") not in reached

        result = s["impact_analysis"].analyze_field_impact("发票表", "invoice_net")
        todo_fields = {
            (todo["目标资产"], todo["目标字段"]) for todo in result["recompute_todos"]
        }
        assert ("日报宽表", "net_amount") in todo_fields
        assert ("仓库宽表", "remark_note") not in todo_fields

    def test_loop_stops_when_hitting_visited_field(self):
        s = _fresh()
        rows = _rows("field_lineage")
        # 构造环：日报宽表.net_amount -> 回流表.net_back -> 日报宽表.net_amount
        rows.append({
            "id": 101, "status": "已验证", "pending": False, "abnormal": False,
            "血缘编号": "FLIN-0101", "加工路径": "回流路径", "加工作业": "JOB_LOOP_A",
            "上游资产": "日报宽表", "上游字段": "net_amount", "下游资产": "回流表",
            "下游字段": "net_back", "转换逻辑": "回流", "连接字段": "order_id",
            "最后更新": "2026-11-21 10:00:00",
        })
        rows.append({
            "id": 102, "status": "已验证", "pending": False, "abnormal": False,
            "血缘编号": "FLIN-0102", "加工路径": "回流路径", "加工作业": "JOB_LOOP_B",
            "上游资产": "回流表", "上游字段": "net_back", "下游资产": "日报宽表",
            "下游字段": "net_amount", "转换逻辑": "回写", "连接字段": "order_id",
            "最后更新": "2026-11-21 10:00:00",
        })
        view = s["field_lineage"].propagation_view("发票表", "invoice_net")
        loop_stops = [hop for hop in view["stopped_hops"] if "环路" in hop["stop_reason"]]
        assert loop_stops, "环路必须在遇到已过字段时截断"
        # 截断点保留证据，但不会无限递归
        assert loop_stops[0]["to_field"] == "net_amount"
        # 同一次追踪不会产生重复签名的路径爆炸
        signatures = [tuple(hop["lineage_id"] for hop in path["hops"]) for path in view["paths"]]
        assert len(signatures) == len(set(signatures))


# ══════════════════════════════════════════════════════════════════
# 停用即止
# ══════════════════════════════════════════════════════════════════

class TestInactivePathCutoff:

    def test_disabled_amount_path_stops_propagation(self):
        s = _fresh()
        # 停用发票净额金额路径
        entry = s["processing_path"].set_enabled(_path_id("发票净额路径"), enabled=False)
        assert entry["启用状态"] == "停用"

        view = s["field_lineage"].propagation_view("发票表", "invoice_net")
        assert view["paths"][0]["stopped"] is True
        assert "停用" in view["paths"][0]["stop_reason"]
        assert view["paths"][0]["hops"][-1]["active"] is False
        # 停用后不可达日报金额字段
        assert view["reachable_fields"] == []

    def test_other_amount_route_remains_active_after_one_disabled(self):
        s = _fresh()
        s["processing_path"].set_enabled(_path_id("发票净额路径"), enabled=False)

        # 订单折让路径仍供日报计算
        discount_view = s["field_lineage"].propagation_view("订单表", "discount_amount")
        assert discount_view["reachable_fields"] == [{"asset": "日报宽表", "field": "net_amount"}]
        assert discount_view["paths"][0]["stopped"] is False

        # 核对视图：net_amount 仍由有效作业计算，但只剩折让作业
        active = s["field_lineage"].active_fields("日报宽表")
        net_amount = next(item for item in active if item["field"] == "net_amount")
        assert net_amount["jobs"] == ["JOB_ORDER_DISCOUNT"]
        assert "JOB_INVOICE_NET" not in net_amount["jobs"]

        # 停用的发票路径在上溯视图中仍可见，只是带停用标记
        upstream = s["field_lineage"].trace_upstream("日报宽表", "net_amount")
        inactive_jobs = {
            job for route in upstream["routes"] for job in route["inactive_jobs"]
        }
        assert "JOB_INVOICE_NET" in inactive_jobs


# ══════════════════════════════════════════════════════════════════
# 影响分析 -> 复算清单
# ══════════════════════════════════════════════════════════════════

class TestImpactToRecomputeTodo:

    def test_impact_turns_verified_paths_into_single_converged_todo(self):
        s = _fresh()
        # 清掉种子待办，验证影响分析自身的清单生成逻辑
        _rows("recompute_todo").clear()

        result = s["impact_analysis"].analyze_field_impact("订单表", "discount_amount")
        todos = result["recompute_todos"]
        assert len(todos) == 1
        todo = todos[0]
        assert todo["目标资产"] == "日报宽表"
        assert todo["目标字段"] == "net_amount"
        # 复算清单保留受影响的具体字段
        assert "discount_amount" in todo["受影响字段"]
        assert "net_amount" in todo["受影响字段"]
        assert result["report"]["报告编号"].startswith("IMPA-FIELD-")

    def test_two_routes_merge_to_one_todo_but_keep_both_routes(self):
        s = _fresh()
        _rows("recompute_todo").clear()
        # 分别从两个源头发起影响分析
        s["impact_analysis"].analyze_field_impact("发票表", "invoice_net")
        s["impact_analysis"].analyze_field_impact("订单表", "discount_amount")

        todos = [row for row in _rows("recompute_todo")
                 if row["目标资产"] == "日报宽表" and row["目标字段"] == "net_amount"]
        assert len(todos) == 1, "汇合字段只允许一条复算作业"
        todo = todos[0]
        assert "发票净额路径" in todo["来源路径"]
        assert "订单折让路径" in todo["来源路径"]
        assert "JOB_INVOICE_NET" in todo["来源作业"]
        assert "JOB_ORDER_DISCOUNT" in todo["来源作业"]

    def test_todo_refreshes_on_lineage_change_and_keeps_affected_fields(self):
        s = _fresh()
        todo = _todo("日报宽表", "net_amount")
        assert todo is not None
        original_fields = todo["受影响字段"]
        assert "invoice_net" in original_fields

        # 停用发票金额路径后，血缘变化驱动待办刷新
        s["processing_path"].set_enabled(_path_id("发票净额路径"), enabled=False)
        refreshed = _todo("日报宽表", "net_amount")
        assert "JOB_INVOICE_NET" not in refreshed["来源作业"]
        assert "JOB_ORDER_DISCOUNT" in refreshed["来源作业"]
        # 受影响的具体字段保留，不因为路径停用而丢失
        assert "invoice_net" in refreshed["受影响字段"]
        assert "discount_amount" in refreshed["受影响字段"]

    def test_disabling_all_routes_removes_todo_from_recompute_set(self):
        s = _fresh()
        s["processing_path"].set_enabled(_path_id("发票净额路径"), enabled=False)
        s["processing_path"].set_enabled(_path_id("订单折让路径"), enabled=False)

        todo = _todo("日报宽表", "net_amount")
        assert todo["status"] == "已完成"
        pending = s["recompute_todo"].pending_todos()
        assert all(
            not (row["目标资产"] == "日报宽表" and row["目标字段"] == "net_amount")
            for row in pending
        )


# ══════════════════════════════════════════════════════════════════
# 并发提交只执行一次
# ══════════════════════════════════════════════════════════════════

class TestConcurrentSubmitOnce:

    def test_duplicate_submit_executes_recompute_only_once(self):
        s = _fresh()
        todo = _todo("日报宽表", "net_amount")
        entry, message = s["recompute_todo"].run_action(todo["id"], "提交复算")
        assert entry["status"] == "复算中"
        assert "仅执行一次" in message

        # 重复提交幂等返回，状态不再改变
        again, second_message = s["recompute_todo"].run_action(todo["id"], "提交复算")
        assert again["status"] == "复算中"
        assert "不重复执行" in second_message

        # 两条来源路径仍然保留
        assert "发票净额路径" in again["来源路径"]
        assert "订单折让路径" in again["来源路径"]

    def test_concurrent_submits_run_only_one_recompute_job(self):
        s = _fresh()
        todo = _todo("日报宽表", "net_amount")
        outcomes: list[str] = []

        def submit() -> None:
            _, message = RecomputeTodoService().run_action(todo["id"], "提交复算")
            outcomes.append(message)

        threads = [threading.Thread(target=submit) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(outcomes) == 8
        assert sum(1 for msg in outcomes if "仅执行一次" in msg) == 1
        assert sum(1 for msg in outcomes if "不重复执行" in msg) == 7
        assert _todo("日报宽表", "net_amount")["status"] == "复算中"


# ══════════════════════════════════════════════════════════════════
# 概览汇总
# ══════════════════════════════════════════════════════════════════

class TestOverviewRecomputeSummary:

    def test_overview_contains_recompute_summary(self):
        _fresh()
        overview = store_mod.store.overview()
        assert "recompute" in overview
        assert overview["recompute"]["pending"] >= 1
        assert overview["recompute"]["by_asset"].get("日报宽表") == 1
