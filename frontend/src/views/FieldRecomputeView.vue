<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h2>字段复算中心</h2>
        <p class="page-desc">
          沿加工血缘解析连接字段，查看字段传播路径；停用路径即截断传播，环路遇到已过字段即止。
          两条路径汇合到同一字段时只生成一条复算作业，来源路径完整保留。
        </p>
      </div>
    </header>

    <form class="analysis-form" @submit.prevent="runTrace">
      <label>
        资产
        <input v-model="query.asset" data-testid="trace-asset" placeholder="如 发票表" required />
      </label>
      <label>
        字段
        <input v-model="query.field" data-testid="trace-field" placeholder="如 invoice_net" required />
      </label>
      <button class="btn primary" data-testid="trace-submit" type="submit" :disabled="loading">
        {{ loading ? '解析中…' : '解析传播路径' }}
      </button>
      <button class="btn" data-testid="impact-submit" type="button" :disabled="loading" @click="runImpact">
        发起字段影响分析
      </button>
      <button class="btn ghost" type="button" @click="loadTodos">刷新复算清单</button>
    </form>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <!-- ── 字段传播路径视图 ── -->
    <h3 v-if="view" class="section-title">
      传播路径视图：{{ view.start.asset }}.{{ view.start.field }}
      <span class="tag">共 {{ view.path_count }} 条路径</span>
      <span class="tag">经过作业 {{ view.jobs.length }} 个</span>
    </h3>
    <table v-if="view" class="data-table">
      <thead>
        <tr>
          <th>传播路径</th><th>经过作业</th><th>连接字段</th>
          <th>字段链路（上游字段 → 下游字段）</th><th>状态</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(route, idx) in view.paths" :key="idx" :data-testid="`path-row-${idx}`">
          <td>{{ route.route_paths.join(' → ') }}</td>
          <td>{{ route.jobs.join('、') }}</td>
          <td>{{ route.join_fields.join('、') }}</td>
          <td>
            <div v-for="(hop, hopIdx) in route.hops" :key="hopIdx" class="hop-line">
              <span :class="{ 'hop-inactive': !hop.active }">
                {{ hop.from_asset }}.{{ hop.from_field }} → {{ hop.to_asset }}.{{ hop.to_field }}
              </span>
              <span class="muted">（{{ hop.job }} / {{ hop.transform }}）</span>
            </div>
          </td>
          <td>
            <span v-if="route.stopped" class="tag tag-stop">{{ route.stop_reason }}</span>
            <span v-else class="tag tag-active">有效传播</span>
          </td>
        </tr>
        <tr v-if="!view.paths.length">
          <td colspan="5" class="empty-state">没有从该字段出发的字段血缘</td>
        </tr>
      </tbody>
    </table>

    <!-- ── 核对视图：仍由有效作业计算的字段 ── -->
    <h3 v-if="activeFields.length" class="section-title">
      核对视图：{{ activeAsset }} 下仍由有效作业计算的字段
    </h3>
    <table v-if="activeFields.length" class="data-table">
      <thead>
        <tr><th>字段</th><th>有效作业</th><th>来源加工路径</th></tr>
      </thead>
      <tbody>
        <tr v-for="item in activeFields" :key="item.field" data-testid="active-field-row">
          <td>{{ item.field }}</td>
          <td>{{ item.jobs.join('、') }}</td>
          <td>{{ item.route_paths.join('、') }}</td>
        </tr>
      </tbody>
    </table>

    <!-- ── 关联复算清单 ── -->
    <h3 class="section-title">关联复算清单（按目标字段归并，保留全部来源路径）</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>清单编号</th><th>目标资产</th><th>目标字段</th><th>来源路径</th>
          <th>来源作业</th><th>受影响的具体字段</th><th>状态</th><th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="todo in todos" :key="String(todo.id)" :data-testid="`todo-row-${todo.id}`">
          <td>{{ todo.清单编号 }}</td>
          <td>{{ todo.目标资产 }}</td>
          <td>{{ todo.目标字段 }}</td>
          <td>{{ todo.来源路径 }}</td>
          <td>{{ todo.来源作业 }}</td>
          <td>{{ todo.受影响字段 }}</td>
          <td>
            <span class="tag" :class="todoStatusClass(todo.status)">{{ todo.status }}</span>
          </td>
          <td class="row-actions">
            <button
              v-if="todo.status === '待复算'"
              class="link"
              type="button"
              :data-testid="`submit-todo-${todo.id}`"
              @click="submitTodo(Number(todo.id))"
            >
              提交复算
            </button>
            <span v-else class="muted">已提交/已完成</span>
          </td>
        </tr>
        <tr v-if="!todos.length">
          <td colspan="8" class="empty-state">暂无复算待办，可先发起字段影响分析</td>
        </tr>
      </tbody>
    </table>
    <footer class="page-foot">
      <span>共 {{ todos.length }} 条复算清单，重复提交同一作业只执行一次</span>
      <span v-if="impactMessage" class="muted">{{ impactMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  analyzeFieldImpact,
  fetchActiveFields,
  fetchPropagation,
  fetchRecomputeTodos,
  submitRecompute,
} from '@/api/field-lineage'
import type {
  ActiveField,
  PropagationView,
  RecomputeTodoRow,
} from '@/data/types'

const query = ref({ asset: '发票表', field: 'invoice_net' })
const view = ref<PropagationView | null>(null)
const todos = ref<RecomputeTodoRow[]>([])
const activeFields = ref<ActiveField[]>([])
const activeAsset = ref('')
const loading = ref(false)
const errorMessage = ref('')
const impactMessage = ref('')

async function runTrace() {
  loading.value = true
  errorMessage.value = ''
  try {
    view.value = await fetchPropagation(query.value.asset, query.value.field)
    const activePayload = await fetchActiveFields(view.value.paths.length
      ? view.value.paths[0].terminal_asset
      : query.value.asset)
    activeFields.value = activePayload.fields
    activeAsset.value = view.value.paths.length
      ? view.value.paths[0].terminal_asset
      : query.value.asset
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '传播路径解析失败'
  } finally {
    loading.value = false
  }
}

async function runImpact() {
  loading.value = true
  errorMessage.value = ''
  try {
    const result = await analyzeFieldImpact(query.value.asset, query.value.field)
    impactMessage.value = `已生成 ${result.recompute_todos.length} 条字段级复算待办`
    await runTrace()
    await loadTodos()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '影响分析失败'
  } finally {
    loading.value = false
  }
}

async function loadTodos() {
  try {
    const payload = await fetchRecomputeTodos()
    todos.value = payload.items
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复算清单读取失败'
  }
}

async function submitTodo(id: number) {
  errorMessage.value = ''
  try {
    const result = await submitRecompute(id)
    impactMessage.value = result.message
    await loadTodos()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复算提交失败'
  }
}

function todoStatusClass(status: string): string {
  if (status === '已完成') return 'tag-done'
  if (status === '复算中') return 'tag-running'
  return 'tag-pending'
}

onMounted(() => {
  void runTrace()
  void loadTodos()
})
</script>

<style scoped>
.section-title {
  margin: 18px 0 8px;
  font-size: 15px;
  display: flex;
  gap: 8px;
  align-items: center;
}
.tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: normal;
  background: #eef2ff;
  color: #3730a3;
}
.tag-active { background: #dcfce7; color: #166534; }
.tag-stop { background: #fee2e2; color: #991b1b; }
.tag-pending { background: #fef3c7; color: #92400e; }
.tag-running { background: #dbeafe; color: #1e40af; }
.tag-done { background: #f1f5f9; color: #475569; }
.hop-line { padding: 2px 0; }
.hop-inactive { color: #b91c1c; text-decoration: line-through; }
.muted { color: #64748b; font-size: 12px; }
</style>
