<script setup>
import { onMounted, ref } from 'vue'
import { getJSON } from '../api'
import ShadowDiffPanel from '../components/ShadowDiffPanel.vue'
const rows = ref([])
const detail = ref(null)
const err = ref('')
async function load() {
  rows.value = await getJSON('/api/batches')
}
async function show(id) {
  detail.value = await getJSON(`/api/batches/${id}`)
}
onMounted(async () => {
  try { await load() }
  catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <header>
      <h1>批次</h1>
      <p class="muted">历史入检批次与报告；每套清单在批次开始时固化，事后改规则不回写已结束批次</p>
    </header>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel">
      <table>
        <thead><tr><th>ID</th><th>数据集</th><th>状态</th><th>扫描</th><th>通过</th><th>隔离</th><th>签发</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id" style="cursor:pointer" @click="show(r.id)">
            <td>{{ r.id }}</td>
            <td>{{ r.dataset_id }}</td>
            <td>{{ r.status }}</td>
            <td>{{ r.scanned_count }}</td>
            <td>{{ r.passed_count }}</td>
            <td>{{ r.quarantined_count }}</td>
            <td>{{ r.released_count }}</td>
            <td>{{ r.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="panel" v-if="detail">
      <h3>批次 #{{ detail.id }} 明细</h3>
      <div class="row" style="align-items:flex-start">
        <div style="flex:1;min-width:260px">
          <h4>本批主集规则清单（{{ detail.main_rules?.length ?? 0 }}）</h4>
          <table>
            <thead><tr><th>ID</th><th>名称</th><th>字段</th><th>类型</th><th>严重度</th></tr></thead>
            <tbody>
              <tr v-for="r in (detail.main_rules || [])" :key="'m'+r.id">
                <td>{{ r.id }}</td><td>{{ r.name }}</td><td><code>{{ r.field }}</code></td>
                <td>{{ r.rule_type }}</td>
                <td><span class="tag" :class="r.severity === 'error' ? 'danger' : ''">{{ r.severity }}</span></td>
              </tr>
              <tr v-if="!(detail.main_rules || []).length"><td colspan="5" class="muted">无</td></tr>
            </tbody>
          </table>
        </div>
        <div style="flex:1;min-width:260px">
          <h4>本批影子集规则清单（{{ detail.shadow_rules?.length ?? 0 }}）</h4>
          <table>
            <thead><tr><th>ID</th><th>名称</th><th>字段</th><th>类型</th><th>严重度</th></tr></thead>
            <tbody>
              <tr v-for="r in (detail.shadow_rules || [])" :key="'s'+r.id">
                <td>{{ r.id }}</td><td>{{ r.name }}</td><td><code>{{ r.field }}</code></td>
                <td>{{ r.rule_type }}</td>
                <td><span class="tag shadow">{{ r.severity }}</span></td>
              </tr>
              <tr v-if="!(detail.shadow_rules || []).length"><td colspan="5" class="muted">影子集关闭，等价单主集入检</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
    <ShadowDiffPanel v-if="detail" :report="detail.report" :rows="detail.rows" />
    <div class="panel" v-if="detail">
      <h4>行级影子命中（持久化，共 {{ detail.shadow_hits?.length ?? 0 }} 条）</h4>
      <table>
        <thead><tr><th>行号</th><th>键</th><th>影子规则</th><th>字段</th><th>详情</th></tr></thead>
        <tbody>
          <tr v-for="h in (detail.shadow_hits || [])" :key="h.id">
            <td>{{ h.row_no }}</td><td>{{ h.entity_key }}</td>
            <td class="muted">{{ h.rule_name }}</td><td><code>{{ h.field }}</code></td>
            <td class="muted">{{ h.detail }}</td>
          </tr>
          <tr v-if="!(detail.shadow_hits || []).length"><td colspan="5" class="muted">无影子命中</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
