<script setup>
defineProps({
  report: { type: Object, required: true },
  rows: { type: Array, default: () => [] },
})
const BUCKETS = [
  { key: 'shadow_only', badge: '主过影子中', note: '主集放行、影子多拦；仍按主集签发' },
  { key: 'main_only', badge: '主中影子过', note: '主集隔离成立、影子未中' },
  { key: 'both_hit', badge: '两侧都中', note: '两套规则均命中；按主集口径处置' },
]
function shadowNames(hits) {
  return (hits || []).map((h) => h.name).join('；') || '—'
}
function mainNames(hits) {
  return (hits || []).map((h) => h.name).join('；') || '—'
}
</script>
<template>
  <div class="panel">
    <h4>主/影子双轨差分</h4>
    <p class="muted">
      影子集{{ report.rule_pack?.shadow_enabled ? '已启用' : '未启用（等价单主集入检）' }} ·
      主集规则 {{ report.rule_pack?.main_rule_count ?? 0 }} 条 ·
      影子规则 {{ report.rule_pack?.shadow_rule_count ?? 0 }} 条
    </p>
    <div class="tiles">
      <div class="tile" v-for="b in BUCKETS" :key="b.key">
        <div class="hero-num">{{ report.diff?.counts?.[b.key] ?? 0 }}</div>
        <div class="muted">{{ b.badge }}</div>
      </div>
    </div>
    <table v-if="report.diff?.total_diff_rows">
      <thead>
        <tr><th>差分</th><th>行号</th><th>键</th><th>主集命中</th><th>影子命中</th><th>说明</th></tr>
      </thead>
      <tbody>
        <template v-for="b in BUCKETS" :key="b.key">
          <tr v-for="d in (report.diff?.details?.[b.key] || [])" :key="b.key + '-' + d.row_no">
            <td><span class="tag" :class="b.key === 'shadow_only' ? 'shadow' : 'danger'">{{ b.badge }}</span></td>
            <td>{{ d.row_no }}</td>
            <td>{{ d.entity_key }}</td>
            <td class="muted">{{ mainNames(d.main_hits) }}</td>
            <td class="muted">{{ shadowNames(d.shadow_hits) }}</td>
            <td class="muted">{{ b.note }}</td>
          </tr>
        </template>
      </tbody>
    </table>
    <h4 style="margin-top:14px">行清单（含两套命中）</h4>
    <table>
      <thead>
        <tr><th>#</th><th>键</th><th>处置（只认主集）</th><th>主集命中</th><th>影子命中（仅观察）</th></tr>
      </thead>
      <tbody>
        <tr v-for="r in rows" :key="r.id">
          <td>{{ r.row_no }}</td>
          <td>{{ r.entity_key }}</td>
          <td><span class="tag" :class="r.disposition === 'pass' ? 'ok' : 'danger'">{{ r.disposition }}</span></td>
          <td class="muted">{{ mainNames(r.rule_hits) }}</td>
          <td class="muted">
            <span :class="r.shadow_hits?.length ? 'shadow-text' : ''">{{ shadowNames(r.shadow_hits) }}</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
