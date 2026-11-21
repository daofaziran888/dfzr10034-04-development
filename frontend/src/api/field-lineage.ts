import { fetchJson, request } from './client'
import type {
  ActiveField,
  PropagationView,
  RecomputeTodoRow,
  UpstreamRoute,
} from '@/data/types'

/** 字段传播路径视图：沿加工血缘向下游追踪字段。 */
export function fetchPropagation(asset: string, field: string): Promise<PropagationView> {
  return postJson('/api/lineage/field/propagation', { asset, field })
}

/** 向上游回溯汇合到目标字段的全部加工路径。 */
export function fetchUpstreamRoutes(asset: string, field: string): Promise<{ routes: UpstreamRoute[] }> {
  return postJson('/api/lineage/field/upstream', { asset, field })
}

/** 核对视图：资产下仍由有效作业计算的字段。 */
export async function fetchActiveFields(asset: string): Promise<{ fields: ActiveField[]; total: number }> {
  const query = encodeURIComponent(asset)
  return fetchJson(`/api/lineage/field/active?asset=${query}`)
}

/** 字段级影响分析：已验证传播路径转成复算清单。 */
export function analyzeFieldImpact(
  asset: string,
  field: string,
): Promise<{ recompute_todos: RecomputeTodoRow[] }> {
  return postJson('/api/impact/field', { asset, field })
}

/** 复算清单查询，可按目标资产过滤。 */
export async function fetchRecomputeTodos(asset?: string): Promise<{ items: RecomputeTodoRow[]; total: number }> {
  const suffix = asset ? `?asset=${encodeURIComponent(asset)}` : ''
  return fetchJson(`/api/recompute/todos${suffix}`)
}

/** 提交复算作业：并发/重复提交只执行一次。 */
export async function submitRecompute(id: number): Promise<{ ok: boolean; message: string }> {
  const response = await request(`/api/recompute/${id}/submit`, { method: 'POST' })
  return await response.json()
}

async function postJson<T>(path: string, payload: unknown): Promise<T> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw new Error(`接口返回 ${response.status}，数据未更新`)
  return (await response.json()) as T
}
