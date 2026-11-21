"""字段级复算待办服务：把已验证的字段传播路径转成复算清单。

核心规则：

- 影响分析产出的传播路径按「目标资产 + 目标字段」归并成复算作业；两条独立
  路径汇合到同一字段时只生成一条复算待办，但来源路径/来源作业全部保留；
- 加工血缘（字段血缘或加工路径启停）发生变化时，待办按最新血缘刷新，并保留
  受影响的具体字段，不丢历史字段；
- 并发提交通过类级锁保证同一条复算作业只执行一次，重复提交直接幂等返回。
"""
from __future__ import annotations

import threading
import time as _time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

# 来源路径/作业在同一单元格内的分隔符。
MULTI_VALUE_SEP = " || "


class RecomputeTodoService(BaseModuleService):
    MODULE = "recompute_todo"
    ENTITY = "复算待办"
    OPERATION_TERM = "复算"
    LIST_FIELDS = [
        "清单编号", "目标资产", "目标字段", "来源路径", "来源作业",
        "关联报告", "受影响字段", "处理说明", "创建时间", "更新时间",
    ]
    REQUIRED_FIELDS = ["目标资产", "目标字段"]
    STATUS_ORDER = ["待复算", "复算中", "已完成"]
    ACTION_RULES = {"提交复算": "复算中", "完成复算": "已完成"}
    NEGATIVE_ACTIONS: list[str] = []

    # 类级锁：所有服务实例共享，保证并发提交只执行一次复算作业。
    _submit_lock = threading.Lock()

    # ── 清单构建 ──────────────────────────────────────────────

    def build_from_impact(
        self, asset: str, field: str, report_id: str = "",
    ) -> list[dict[str, Any]]:
        """把某个变更字段已验证的传播路径转成复算清单。

        只沿有效（未停用）传播边可达的下游字段生成待办；与金额无关的备注列
        因为不在传播可达范围内，自然不会进入本次回算集。
        """
        from app.services.field_lineage import FieldLineageService
        lineage_service = FieldLineageService()
        view = lineage_service.propagation_view(asset, field)

        todos: list[dict[str, Any]] = []
        for target in view["reachable_fields"]:
            todos.append(self._upsert_from_lineage(
                target["asset"], target["field"], report_id=report_id,
            ))
        return todos

    def on_lineage_changed(self, trigger: str = "") -> list[dict[str, Any]]:
        """加工血缘变化后按最新血缘刷新已有复算待办。

        只刷新清单中已存在的待办，不凭空为其他血缘（如与金额无关的备注列）
        新建待办——新待办只由字段级影响分析显式生成。停用的路径从来源作业中
        剔除，但受影响的具体字段保留；全部来源路径都停用的待办移出复算集；
        路径恢复时自动重新打开。
        """
        from app.services.field_lineage import FieldLineageService
        lineage_service = FieldLineageService()
        refreshed: list[dict[str, Any]] = []
        now = _time.strftime("%Y-%m-%d %H:%M:%S")

        for todo in list(store.rows(self.MODULE)):
            target_asset = str(todo.get("目标资产", ""))
            target_field = str(todo.get("目标字段", ""))
            upstream = lineage_service.trace_upstream(target_asset, target_field)
            active_routes = [route for route in upstream["routes"] if route["active"]]

            route_names = _dedupe(name for route in active_routes for name in route["route_paths"])
            job_names = _dedupe(job for route in active_routes for job in route["jobs"])
            affected = _dedupe(
                fld
                for route in active_routes
                for fld in [route["source_field"], target_field]
                if fld
            )

            previous_fields = _split_multi(todo.get("受影响字段", ""))
            todo["受影响字段"] = ", ".join(_dedupe(previous_fields + affected))
            todo["更新时间"] = now

            if not active_routes:
                if todo.get("status") in ("待复算", "复算中"):
                    todo["status"] = "已完成"
                    todo["pending"] = False
                    todo["处理说明"] = f"全部来源路径已停用，移出复算集（触发：{trigger or '血缘变更'}）"
                continue

            todo["来源路径"] = MULTI_VALUE_SEP.join(route_names)
            todo["来源作业"] = MULTI_VALUE_SEP.join(job_names)
            if todo.get("status") == "已完成":
                # 路径恢复：重新纳入复算集
                todo["status"] = "待复算"
                todo["pending"] = True
            todo["处理说明"] = f"血缘变更自动刷新（触发：{trigger or '血缘变更'}）"
            refreshed.append(todo)
        return refreshed

    def _upsert_from_lineage(
        self, target_asset: str, target_field: str, *,
        report_id: str, trigger: str = "",
    ) -> dict[str, Any]:
        """按目标字段归并传播路径，汇合字段只保留一条复算待办。"""
        from app.services.field_lineage import FieldLineageService
        upstream = FieldLineageService().trace_upstream(target_asset, target_field)
        active_routes = [route for route in upstream["routes"] if route["active"]]

        route_names = _dedupe(name for route in active_routes for name in route["route_paths"])
        job_names = _dedupe(job for route in active_routes for job in route["jobs"])
        affected = _dedupe(
            fld
            for route in active_routes
            for fld in [route["source_field"], target_field]
            if fld
        )

        existing = next(
            (row for row in store.rows(self.MODULE)
             if row.get("目标资产") == target_asset and row.get("目标字段") == target_field),
            None,
        )
        now = _time.strftime("%Y-%m-%d %H:%M:%S")
        if existing is not None:
            # 血缘变化后刷新来源路径/作业；受影响字段与历史值取并集，保留具体字段。
            previous_fields = _split_multi(existing.get("受影响字段", ""))
            existing["来源路径"] = MULTI_VALUE_SEP.join(route_names)
            existing["来源作业"] = MULTI_VALUE_SEP.join(job_names)
            existing["受影响字段"] = ", ".join(_dedupe(previous_fields + affected))
            existing["处理说明"] = f"血缘变更自动刷新（触发：{trigger or '影响分析'}）" if trigger else existing.get("处理说明", "")
            if report_id and not existing.get("关联报告"):
                existing["关联报告"] = report_id
            existing["更新时间"] = now
            return existing

        todo_id = store.next_id(self.MODULE)
        todo = {
            "id": todo_id,
            "清单编号": f"RCPT-{todo_id:04d}",
            "目标资产": target_asset,
            "目标字段": target_field,
            "来源路径": MULTI_VALUE_SEP.join(route_names),
            "来源作业": MULTI_VALUE_SEP.join(job_names),
            "关联报告": report_id,
            "受影响字段": ", ".join(affected),
            "处理说明": "",
            "创建时间": now,
            "更新时间": now,
            "status": "待复算",
            "pending": True,
            "abnormal": False,
        }
        store.rows(self.MODULE).append(todo)
        return todo

    # ── 并发提交去重 ─────────────────────────────────────────

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action == "提交复算":
            return self._submit_once(entry_id)
        return super().run_action(entry_id, action)

    def _submit_once(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """并发/重复提交时同一条复算作业只执行一次。"""
        with self._submit_lock:
            entry = store.find(self.MODULE, entry_id)
            if entry is None:
                return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
            if entry.get("status") != "待复算":
                # 幂等返回：不重复执行，但两条来源路径仍完整保留在清单上。
                return entry, f"复算作业已处于「{entry.get('status')}」，本次提交不重复执行"
            old_status = entry.get("status")
            entry["status"] = "复算中"
            entry["pending"] = True
            entry["更新时间"] = _time.strftime("%Y-%m-%d %H:%M:%S")
            store.log_change(
                self.MODULE, entry_id, "提交复算",
                before=old_status, after="复算中", source="action", scope=self.MODULE,
            )
            return entry, "复算作业已提交，仅执行一次"

    # ── 查询 ──────────────────────────────────────────────────

    def todos_for_asset(self, asset: str) -> list[dict[str, Any]]:
        return [row for row in store.rows(self.MODULE) if row.get("目标资产") == asset]

    def pending_todos(self) -> list[dict[str, Any]]:
        return [row for row in store.rows(self.MODULE) if row.get("status") != "已完成"]


def _dedupe(values: Any) -> list[str]:
    seen: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def _split_multi(value: Any) -> list[str]:
    return [part.strip() for part in str(value or "").replace(MULTI_VALUE_SEP, ",").split(",") if part.strip()]
