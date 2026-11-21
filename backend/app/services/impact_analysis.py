"""影响分析领域服务：血缘驱动的影响分析报告、变更风险评估和补救建议生成。

接收血缘追踪的输出，对变更进行风险评估。
"""
from __future__ import annotations

import time as _time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ImpactAnalysisService(BaseModuleService):
    MODULE = "impact_analysis"
    ENTITY = "影响报告"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["报告编号", "变更资产", "影响下游", "影响类型", "严重级别", "影响行数", "补救建议", "生成时间"]
    REQUIRED_FIELDS = ["报告编号", "变更资产", "影响下游"]
    STATUS_ORDER = ["待生成", "已生成", "已审核", "已处理"]
    ACTION_RULES = {"触发生成": "已生成", "标记审核": "已审核", "确认处理": "已处理"}
    NEGATIVE_ACTIONS = []

    def analyze_impact(self, asset_code: str, change_type: str = "schema_change") -> dict[str, Any]:
        """分析资产变更对下游的影响（Pipeline 7 核心方法）。"""
        # 获取血缘关系中的下游资产
        downstream = []
        for row in store.rows("lineage_track"):
            if row.get("上游资产") == asset_code or row.get("下游资产") == asset_code:
                ds = row.get("下游资产") if row.get("上游资产") == asset_code else row.get("上游资产")
                if ds and ds != asset_code:
                    downstream.append(ds)

        severity = "严重" if len(downstream) >= 5 else ("需关注" if len(downstream) >= 2 else "一般")

        report_id = store.next_id(self.MODULE)
        report = {
            "id": report_id,
            "报告编号": f"IMPA-{report_id:04d}",
            "变更资产": asset_code,
            "影响下游": ", ".join(downstream) if downstream else "无",
            "影响类型": change_type,
            "严重级别": severity,
            "影响行数": str(len(downstream) * 100),
            "补救建议": self._generate_remedy(change_type, downstream),
            "生成时间": _time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "已生成",
            "pending": True,
            "abnormal": severity == "严重",
            "created_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        store.rows(self.MODULE).append(report)

        # 如果严重级别高，创建治理问题
        if severity == "严重":
            self._create_impact_issue(report, asset_code, downstream)

        store.log_change(
            self.MODULE, report_id, "影响分析",
            before=None, after=f"变更{asset_code}影响{len(downstream)}个下游, 严重级别:{severity}",
            source="pipeline_7", scope="impact_analysis"
        )

        self.emit_event("impact_analyzed", {
            "report_id": report_id,
            "asset": asset_code,
            "downstream_count": len(downstream),
            "severity": severity,
        })

        return {
            "report": report,
            "downstream": downstream,
            "severity": severity,
            "pipeline": "lineage_track->impact_analysis",
        }

    def _generate_remedy(self, change_type: str, downstream: list[str]) -> str:
        """生成补救建议。"""
        if not downstream:
            return "无下游影响，无需特殊处理"
        count = len(downstream)
        if change_type == "schema_change":
            return f"上游Schema变更影响{count}个下游资产，建议先通知各下游负责人确认兼容性，再同步更新下游Schema"
        elif change_type in ("data_deletion", "field_removal"):
            return f"上游删除操作影响{count}个下游资产，建议先备份现有数据再执行，并通知各下游调整数据消费逻辑"
        return f"上游变更影响{count}个下游资产，建议逐项评估影响后有序推进"

    def _create_impact_issue(self, report: dict[str, Any], asset_code: str, downstream: list[str]) -> None:
        """创建治理问题追踪。"""
        issue_id = store.next_id("issue_track")
        issue = {
            "id": issue_id,
            "问题编号": f"ISSU-IMP-{issue_id:04d}",
            "关联资产": asset_code,
            "问题类型": "影响范围过大",
            "严重级别": "严重",
            "指派处理": "数据架构师",
            "当前状态": "待评估",
            "修复方案": f"上游{asset_code}变更影响{len(downstream)}个下游，需逐项评估",
            "截止日期": _time.strftime("%Y-%m-%d", _time.localtime(_time.time() + 86400 * 5)),
            "status": "待确认",
            "pending": True,
            "abnormal": True,
            "impact_report_id": report["id"],
            "created_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        store.rows("issue_track").append(issue)

    def analyze_field_impact(self, asset_code: str, field_name: str) -> dict[str, Any]:
        """字段级影响分析：沿加工血缘输出传播路径，并转成字段级复算清单。

        - 只覆盖已验证且未停用的传播路径，停用路径以截断点呈现；
        - 与变更字段无传播关系的列（如同表的备注列）不会进入复算集；
        - 两条路径汇合到同一字段时复算清单只有一条作业，来源路径全部保留。
        """
        from app.services.field_lineage import FieldLineageService
        from app.services.recompute_todo import RecomputeTodoService

        lineage_service = FieldLineageService()
        view = lineage_service.propagation_view(asset_code, field_name)

        report_id = store.next_id(self.MODULE)
        report = {
            "id": report_id,
            "报告编号": f"IMPA-FIELD-{report_id:04d}",
            "变更资产": f"{asset_code}.{field_name}",
            "影响下游": "、".join(
                f"{item['asset']}.{item['field']}" for item in view["reachable_fields"]
            ) or "无有效下游",
            "影响类型": "字段级复算",
            "严重级别": "需关注" if view["path_count"] >= 2 else "一般",
            "影响行数": str(len(view["reachable_fields"])),
            "补救建议": (
                f"字段{field_name}的变更沿{view['path_count']}条传播路径影响"
                f"{len(view['reachable_fields'])}个下游字段，已按字段生成复算清单"
            ),
            "生成时间": _time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "已生成",
            "pending": True,
            "abnormal": False,
            "field_paths": view["paths"],
            "reachable_fields": view["reachable_fields"],
            "stopped_hops": view["stopped_hops"],
            "created_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        store.rows(self.MODULE).append(report)

        todos = RecomputeTodoService().build_from_impact(
            asset_code, field_name, report_id=report["报告编号"],
        )
        store.log_change(
            self.MODULE, report_id, "字段级影响分析",
            before=None,
            after=f"{asset_code}.{field_name} -> {len(todos)}条复算待办",
            source="field_lineage", scope="recompute_todo",
        )
        self.emit_event("field_impact_analyzed", {
            "report_id": report_id,
            "asset": asset_code,
            "field": field_name,
            "recompute_todo_ids": [todo["id"] for todo in todos],
        })
        return {
            "report": report,
            "propagation_view": view,
            "recompute_todos": todos,
        }

    def get_reports_by_asset(self, asset_code: str) -> list[dict[str, Any]]:
        """获取资产的所有影响报告。"""
        return store.find_by_field(self.MODULE, "变更资产", asset_code)

    def get_pending_reports(self) -> list[dict[str, Any]]:
        """获取待处理的影响报告。"""
        return [
            r for r in store.rows(self.MODULE)
            if r.get("status") in ("待生成", "已生成")
        ]