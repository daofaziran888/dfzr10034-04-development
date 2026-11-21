"""字段级加工血缘与复算清单专用路由。

通用资源路由负责三个新模块的列表/登记/动作；这里承载需要入参体的
字段传播、字段级影响分析和复算提交接口。固定路径必须在资源路由之前注册。
"""
from __future__ import annotations

from fastapi import APIRouter

from app.schemas import FieldLineagePayload
from app.services.field_lineage import FieldLineageService
from app.services.impact_analysis import ImpactAnalysisService
from app.services.recompute_todo import RecomputeTodoService

router = APIRouter(prefix="/api", tags=["字段血缘与复算"])


@router.post("/lineage/field/propagation")
def field_propagation(payload: FieldLineagePayload) -> dict:
    """字段传播路径视图：向下游列出每条路径经过的作业、连接字段与截断点。"""
    return FieldLineageService().propagation_view(payload.asset, payload.field)


@router.post("/lineage/field/upstream")
def field_upstream(payload: FieldLineagePayload) -> dict:
    """向上游回溯汇合到目标字段的全部加工路径（标注停用路径）。"""
    return FieldLineageService().trace_upstream(payload.asset, payload.field)


@router.get("/lineage/field/active")
def active_fields(asset: str) -> dict:
    """核对视图：列出资产下仍由有效作业计算的字段。"""
    service = FieldLineageService()
    fields = service.active_fields(asset)
    return {"asset": asset, "fields": fields, "total": len(fields)}


@router.post("/impact/field")
def field_impact(payload: FieldLineagePayload) -> dict:
    """字段级影响分析：已验证传播路径转成复算清单。"""
    return ImpactAnalysisService().analyze_field_impact(payload.asset, payload.field)


@router.get("/recompute/todos")
def recompute_todos(asset: str | None = None) -> dict:
    """复算清单查询，可按目标资产过滤。"""
    service = RecomputeTodoService()
    items = service.todos_for_asset(asset) if asset else service.pending_todos()
    return {"items": items, "total": len(items)}


@router.post("/recompute/{entry_id}/submit")
def submit_recompute(entry_id: int) -> dict:
    """提交复算作业：并发/重复提交只执行一次。"""
    entry, message = RecomputeTodoService().run_action(entry_id, "提交复算")
    return {"ok": entry is not None, "message": message, "entry": entry}
