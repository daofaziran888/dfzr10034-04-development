"""加工路径服务：管理同一张宽表上多条加工路径及其作业的启停。

一条加工路径对应一个加工作业；停用金额路径后，该路径上的字段传播在字段血缘
追踪中被截断，同时触发复算待办按最新加工血缘重新界定。
"""
from __future__ import annotations

import time as _time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ProcessingPathService(BaseModuleService):
    MODULE = "processing_path"
    ENTITY = "加工路径"
    OPERATION_TERM = "编排"
    LIST_FIELDS = [
        "路径编号", "加工路径", "加工作业", "源资产", "目标资产",
        "目标字段", "启用状态", "最后更新",
    ]
    REQUIRED_FIELDS = ["加工路径", "加工作业", "源资产", "目标资产", "目标字段"]
    STATUS_ORDER = ["运行中", "已停用"]
    ACTION_RULES = {"启用路径": "运行中", "停用路径": "已停用"}
    NEGATIVE_ACTIONS = ["停用路径"]

    def set_enabled(self, entry_id: int, enabled: bool) -> dict[str, Any] | None:
        """切换加工路径启停状态，并同步复算待办。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None
        entry["启用状态"] = "启用" if enabled else "停用"
        entry["status"] = "运行中" if enabled else "已停用"
        entry["pending"] = not enabled
        entry["最后更新"] = _time.strftime("%Y-%m-%d %H:%M:%S")
        store.log_change(
            self.MODULE, entry_id, "启用路径" if enabled else "停用路径",
            before="停用" if enabled else "启用", after=entry["启用状态"],
            source="action", scope=self.MODULE,
        )
        from app.services.recompute_todo import RecomputeTodoService
        RecomputeTodoService().on_lineage_changed(
            trigger=f"processing_path:{entry.get('加工路径', '')}:{entry['启用状态']}",
        )
        return entry

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action not in self.ACTION_RULES:
            return None, f"动作「{action}」不属于{self.MODULE}可执行范围"
        entry = self.set_enabled(entry_id, enabled=(action == "启用路径"))
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        return entry, f"{self.ENTITY}已{action}"

    def paths_for_field(self, asset: str, field: str) -> list[dict[str, Any]]:
        """列出汇合到指定资产字段的全部加工路径及其启用状态。"""
        return [
            dict(row) for row in store.rows(self.MODULE)
            if row.get("目标资产") == asset and row.get("目标字段") == field
        ]
