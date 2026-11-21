<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h2>运营概览</h2>
        <p class="page-desc">汇总各业务模块的关键指标，先看总量再看异常。</p>
      </div>
    </header>
    <div class="stat-row">
      <article v-for="card in cards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.value }}</strong>
      </article>
    </div>
    <section v-if="recompute" class="recompute-panel" data-testid="recompute-panel">
      <h3>关联复算清单</h3>
      <p class="page-desc">
        待复算作业 {{ recompute.pending }} 条 / 累计清单 {{ recompute.total }} 条；
        同一字段的多条传播路径只生成一条复算作业。
      </p>
      <table class="data-table">
        <thead>
          <tr><th>目标资产</th><th>待复算清单数</th></tr>
        </thead>
        <tbody>
          <tr v-for="(count, asset) in recompute.by_asset" :key="asset">
            <td>{{ asset }}</td>
            <td>{{ count }}</td>
          </tr>
          <tr v-if="!Object.keys(recompute.by_asset).length">
            <td colspan="2" class="empty-state">暂无待复算清单</td>
          </tr>
        </tbody>
      </table>
    </section>
    <table class="data-table">
      <thead>
        <tr><th>业务模块</th><th>今日新增</th><th>待处理</th><th>异常量</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in moduleRows" :key="row.name">
          <td>{{ row.name }}</td>
          <td>{{ row.created }}</td>
          <td>{{ row.pending }}</td>
          <td>{{ row.abnormal }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchJson } from '@/api/client'

type Overview = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
  recompute?: {
    pending: number
    total: number
    by_asset: Record<string, number>
  }
}

const cards = ref<Overview['cards']>([])
const moduleRows = ref<Overview['modules']>([])
const recompute = ref<Overview['recompute']>()

onMounted(async () => {
  try {
    const payload = await fetchJson<Overview>('/api/overview')
    cards.value = payload.cards
    moduleRows.value = payload.modules
    recompute.value = payload.recompute
  } catch {
    cards.value = [{"label": "业务模块", "value": 0}, {"label": "今日新增", "value": 0}]
    moduleRows.value = [{"name": "资产注册", "created": 0, "pending": 0, "abnormal": 0}, {"name": "元数据索引", "created": 0, "pending": 0, "abnormal": 0}, {"name": "质量规则", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据画像", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据分级", "created": 0, "pending": 0, "abnormal": 0}, {"name": "生命周期", "created": 0, "pending": 0, "abnormal": 0}, {"name": "冷热归档", "created": 0, "pending": 0, "abnormal": 0}, {"name": "保留清理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "脱敏引擎", "created": 0, "pending": 0, "abnormal": 0}, {"name": "操作日志", "created": 0, "pending": 0, "abnormal": 0}, {"name": "变更捕获", "created": 0, "pending": 0, "abnormal": 0}, {"name": "血缘追踪", "created": 0, "pending": 0, "abnormal": 0}, {"name": "影响分析", "created": 0, "pending": 0, "abnormal": 0}, {"name": "标签体系", "created": 0, "pending": 0, "abnormal": 0}, {"name": "治理评分", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据契约", "created": 0, "pending": 0, "abnormal": 0}, {"name": "合规扫描", "created": 0, "pending": 0, "abnormal": 0}, {"name": "问题追踪", "created": 0, "pending": 0, "abnormal": 0}, {"name": "版本履历", "created": 0, "pending": 0, "abnormal": 0}, {"name": "调度治理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "差异报告", "created": 0, "pending": 0, "abnormal": 0}, {"name": "负责人管理", "created": 0, "pending": 0, "abnormal": 0}]
  }
})
</script>

<style scoped>
.recompute-panel {
  margin-top: 18px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 16px 16px;
}
.recompute-panel h3 {
  margin: 4px 0;
  font-size: 15px;
}
</style>
