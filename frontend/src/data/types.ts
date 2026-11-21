/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
  recompute?: {
    pending: number
    total: number
    by_asset: Record<string, number>
  }
}

/** 字段传播路径上的一跳（一次字段加工）。 */
export type FieldHop = {
  lineage_id: string
  path: string
  job: string
  from_asset: string
  from_field: string
  to_asset: string
  to_field: string
  join_field: string
  transform: string
  active: boolean
  stop_reason: string
}

export type FieldRoute = {
  route_paths: string[]
  jobs: string[]
  join_fields: string[]
  hops: FieldHop[]
  stopped: boolean
  stop_reason: string
  terminal_asset: string
  terminal_field: string
}

export type PropagationView = {
  start: { asset: string; field: string }
  paths: FieldRoute[]
  path_count: number
  jobs: string[]
  reachable_fields: { asset: string; field: string }[]
  stopped_hops: FieldHop[]
}

export type UpstreamRoute = {
  source_asset: string
  source_field: string
  route_paths: string[]
  jobs: string[]
  join_fields: string[]
  active: boolean
  inactive_jobs: string[]
  hops: FieldHop[]
}

export type ActiveField = {
  asset: string
  field: string
  jobs: string[]
  route_paths: string[]
}

export type RecomputeTodoRow = EntryRow & {
  清单编号: string
  目标资产: string
  目标字段: string
  来源路径: string
  来源作业: string
  关联报告: string
  受影响字段: string
  处理说明: string
  创建时间: string
  更新时间: string
}
