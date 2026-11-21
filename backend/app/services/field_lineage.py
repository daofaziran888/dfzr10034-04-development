"""字段级加工血缘服务：沿加工血缘解析连接字段，输出字段传播路径视图。

宽表下游过去只能按资产定位，本服务把血缘细化到字段：

- 沿 field_lineage 的「上游字段 -> 下游字段」逐跳传播，连接字段随每跳记录；
- 传播遇到已停用字段（血缘本身停用，或所属加工路径停用）即止，不再向下游扩散；
- 环路遇到已经过字段即止，避免环形血缘导致无限遍历；
- 两条独立路径汇合到同一字段时分别保留，路径不做合并，复算清单的去重交给
  ``recompute_todo`` 服务处理。
"""
from __future__ import annotations

import time as _time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

# 停用状态集合：加工路径/字段血缘处于这些状态时，字段传播到此为止。
INACTIVE_STATUS = {"停用", "已停用"}


class FieldLineageService(BaseModuleService):
    MODULE = "field_lineage"
    ENTITY = "字段血缘"
    OPERATION_TERM = "解析"
    LIST_FIELDS = [
        "血缘编号", "加工路径", "加工作业", "上游资产", "上游字段",
        "下游资产", "下游字段", "转换逻辑", "连接字段", "最后更新",
    ]
    REQUIRED_FIELDS = ["加工路径", "加工作业", "上游资产", "上游字段", "下游资产", "下游字段"]
    STATUS_ORDER = ["待验证", "已验证", "已停用"]
    ACTION_RULES = {"验证血缘": "已验证", "停用血缘": "已停用"}
    NEGATIVE_ACTIONS = ["停用血缘"]

    # ── 状态判定 ──────────────────────────────────────────────

    def _inactive_paths(self) -> set[str]:
        """返回已停用的加工路径名称集合。"""
        return {
            str(row.get("加工路径", ""))
            for row in store.rows("processing_path")
            if str(row.get("启用状态", "启用")) in INACTIVE_STATUS
            or row.get("status") in INACTIVE_STATUS
        }

    def edge_inactive(self, edge: dict[str, Any]) -> bool:
        """一条字段传播边是否已停用：血缘停用或所属加工路径停用。"""
        if edge.get("status") in INACTIVE_STATUS:
            return True
        return str(edge.get("加工路径", "")) in self._inactive_paths()

    # ──  hop / path 组装 ─────────────────────────────────────

    @staticmethod
    def _hop(edge: dict[str, Any], *, active: bool, stop_reason: str = "") -> dict[str, Any]:
        return {
            "lineage_id": edge.get("血缘编号", ""),
            "path": edge.get("加工路径", ""),
            "job": edge.get("加工作业", ""),
            "from_asset": edge.get("上游资产", ""),
            "from_field": edge.get("上游字段", ""),
            "to_asset": edge.get("下游资产", ""),
            "to_field": edge.get("下游字段", ""),
            "join_field": edge.get("连接字段", ""),
            "transform": edge.get("转换逻辑", ""),
            "active": active,
            "stop_reason": stop_reason,
        }

    @staticmethod
    def _append_path(
        paths: list[dict[str, Any]],
        signatures: set[tuple[Any, ...]],
        hops: list[dict[str, Any]],
        route_paths: list[str],
        route_jobs: list[str],
        *,
        stopped: bool,
        stop_reason: str,
    ) -> None:
        signature = tuple(hop["lineage_id"] for hop in hops)
        if signature in signatures:
            return
        signatures.add(signature)
        terminal = hops[-1] if hops else {}
        paths.append({
            "route_paths": [p for p in route_paths if p],
            "jobs": [j for j in route_jobs if j],
            "join_fields": [hop["join_field"] for hop in hops if hop.get("join_field")],
            "hops": hops,
            "stopped": stopped,
            "stop_reason": stop_reason,
            "terminal_asset": terminal.get("to_asset", ""),
            "terminal_field": terminal.get("to_field", ""),
        })

    # ── 下游传播（影响分析方向） ─────────────────────────────

    def trace_field(self, asset: str, field: str) -> dict[str, Any]:
        """从指定字段出发向下游追踪全部字段传播路径。

        分支上已访问过的字段记录在 ``branch_seen`` 中，环路遇到已过字段即止；
        已停用边只记录截断点，不继续向下游扩散。两条路径汇合时各自成路，互不覆盖。
        """
        paths: list[dict[str, Any]] = []
        signatures: set[tuple[Any, ...]] = set()
        visited: set[tuple[str, str]] = {(asset, field)}
        stopped_hops: list[dict[str, Any]] = []

        def dfs(cur_asset: str, cur_field: str, hops: list[dict[str, Any]],
                route_paths: list[str], route_jobs: list[str],
                branch_seen: set[tuple[str, str]]) -> None:
            outgoing = [
                edge for edge in store.rows(self.MODULE)
                if edge.get("上游资产") == cur_asset and edge.get("上游字段") == cur_field
            ]
            if not outgoing and hops:
                self._append_path(paths, signatures, hops, route_paths, route_jobs,
                                  stopped=False, stop_reason="")
                return
            for edge in outgoing:
                nxt = (str(edge.get("下游资产", "")), str(edge.get("下游字段", "")))
                if nxt in branch_seen:
                    # 环路：下一跳字段已在当前路径上，记录截断点后即止
                    hop = self._hop(edge, active=False, stop_reason="环路截断：遇到已过字段")
                    self._append_path(
                        paths, signatures, hops + [hop],
                        route_paths + [str(edge.get("加工路径", ""))],
                        route_jobs + [str(edge.get("加工作业", ""))],
                        stopped=True, stop_reason="环路截断：遇到已过字段",
                    )
                    stopped_hops.append(hop)
                    continue
                inactive = self.edge_inactive(edge)
                hop = self._hop(
                    edge, active=not inactive,
                    stop_reason="停用截断：字段已停用" if inactive else "",
                )
                new_paths = route_paths + [str(edge.get("加工路径", ""))]
                new_jobs = route_jobs + [str(edge.get("加工作业", ""))]
                if inactive:
                    # 停用即止：保留截断证据，但不进入下游字段
                    self._append_path(paths, signatures, hops + [hop], new_paths, new_jobs,
                                      stopped=True, stop_reason="停用截断：字段已停用")
                    stopped_hops.append(hop)
                    continue
                visited.add(nxt)
                dfs(nxt[0], nxt[1], hops + [hop], new_paths, new_jobs,
                    branch_seen | {nxt})
        dfs(asset, field, [], [], [], {(asset, field)})
        return {
            "asset": asset,
            "field": field,
            "paths": paths,
            "visited_fields": sorted(visited),
            "stopped_hops": stopped_hops,
        }

    def propagation_view(self, asset: str, field: str) -> dict[str, Any]:
        """字段传播路径视图：值班人员据此界定字段级复算范围。"""
        trace = self.trace_field(asset, field)
        jobs: list[str] = []
        reachable: list[dict[str, str]] = []
        for path in trace["paths"]:
            for job in path["jobs"]:
                if job not in jobs:
                    jobs.append(job)
        for ast, fld in trace["visited_fields"]:
            if (ast, fld) != (asset, field):
                reachable.append({"asset": ast, "field": fld})
        return {
            "start": {"asset": asset, "field": field},
            "paths": trace["paths"],
            "path_count": len(trace["paths"]),
            "jobs": jobs,
            "reachable_fields": reachable,
            "stopped_hops": trace["stopped_hops"],
        }

    # ── 上游溯源（核对视图方向） ─────────────────────────────

    def trace_upstream(self, asset: str, field: str) -> dict[str, Any]:
        """向上游回溯汇合到目标字段的全部加工路径（含已停用路径的停用标记）。"""
        routes: list[list[dict[str, Any]]] = []
        signatures: set[tuple[Any, ...]] = set()

        def dfs(cur_asset: str, cur_field: str, acc: list[dict[str, Any]],
                branch_seen: set[tuple[str, str]]) -> None:
            incoming = [
                edge for edge in store.rows(self.MODULE)
                if edge.get("下游资产") == cur_asset and edge.get("下游字段") == cur_field
            ]
            if not incoming and acc:
                ordered = list(reversed(acc))
                signature = tuple(str(e.get("血缘编号", "")) for e in ordered)
                if signature not in signatures:
                    signatures.add(signature)
                    routes.append(ordered)
                return
            for edge in incoming:
                src = (str(edge.get("上游资产", "")), str(edge.get("上游字段", "")))
                inactive = self.edge_inactive(edge)
                if src in branch_seen:
                    ordered = list(reversed(acc + [edge]))
                    signature = tuple(str(e.get("血缘编号", "")) for e in ordered)
                    if signature not in signatures:
                        signatures.add(signature)
                        routes.append(ordered)
                    continue
                if inactive:
                    # 停用即止：不再越过停用字段继续上溯
                    ordered = list(reversed(acc + [edge]))
                    signature = tuple(str(e.get("血缘编号", "")) for e in ordered)
                    if signature not in signatures:
                        signatures.add(signature)
                        routes.append(ordered)
                    continue
                dfs(src[0], src[1], acc + [edge], branch_seen | {src})

        dfs(asset, field, [], {(asset, field)})
        source_routes = []
        for edges in routes:
            source_routes.append({
                "source_asset": edges[0].get("上游资产", ""),
                "source_field": edges[0].get("上游字段", ""),
                "route_paths": [str(e.get("加工路径", "")) for e in edges],
                "jobs": [str(e.get("加工作业", "")) for e in edges],
                "join_fields": [str(e.get("连接字段", "")) for e in edges],
                "active": all(not self.edge_inactive(e) for e in edges),
                "inactive_jobs": [
                    str(e.get("加工作业", "")) for e in edges if self.edge_inactive(e)
                ],
                "hops": [
                    self._hop(e, active=not self.edge_inactive(e),
                              stop_reason="停用截断：字段已停用" if self.edge_inactive(e) else "")
                    for e in edges
                ],
            })
        return {"asset": asset, "field": field, "routes": source_routes}

    def active_fields(self, asset: str) -> list[dict[str, Any]]:
        """核对视图：列出该资产下当前仍由有效作业计算的字段及其来源作业。"""
        result: dict[str, dict[str, Any]] = {}
        for edge in store.rows(self.MODULE):
            if edge.get("下游资产") != asset or self.edge_inactive(edge):
                continue
            field = str(edge.get("下游字段", ""))
            item = result.setdefault(field, {
                "asset": asset,
                "field": field,
                "jobs": [],
                "route_paths": [],
            })
            job = str(edge.get("加工作业", ""))
            if job and job not in item["jobs"]:
                item["jobs"].append(job)
            path = str(edge.get("加工路径", ""))
            if path and path not in item["route_paths"]:
                item["route_paths"].append(path)
        return list(result.values())

    # ── 动作联动 ──────────────────────────────────────────────

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """字段血缘停用/验证后，复算待办需要随加工血缘变化同步。"""
        entry, message = super().run_action(entry_id, action)
        if entry is not None and action in self.ACTION_RULES:
            entry["最后更新"] = _time.strftime("%Y-%m-%d %H:%M:%S")
            from app.services.recompute_todo import RecomputeTodoService
            RecomputeTodoService().on_lineage_changed(trigger=f"field_lineage:{action}")
        return entry, message
