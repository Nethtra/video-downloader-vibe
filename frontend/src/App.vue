<script setup>
import { ref, computed, watch, nextTick, onBeforeUnmount } from 'vue'
import MindMap from './components/MindMap.vue'
import { t, tl, locale, setLocale, LANGS } from './i18n'

/* ---------------- 分析与下载 ---------------- */
const url = ref('')
const analyzing = ref(false)
const errorMsg = ref('')
const info = ref(null)
const selectedFormat = ref('')
const task = ref(null)
let pollTimer = null

const canSubmit = computed(() => url.value.trim().length > 4 && !analyzing.value)

const videoFormats = computed(() =>
  (info.value?.formats || []).filter(f => f.height > 0)
)

async function analyze() {
  if (!canSubmit.value) return
  analyzing.value = true
  errorMsg.value = ''
  info.value = null
  task.value = null
  try {
    const res = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: url.value.trim() }),
    })
    const data = await readJson(res)
    if (!res.ok) throw new Error(data.detail || data.message || t('err.analyze'))
    // 轮询解析结果（YouTube 等平台解析较慢，后端异步执行）
    const analyzeId = data.analyze_id
    for (let i = 0; i < 100; i++) {
      await new Promise(r => setTimeout(r, 1500))
      const sres = await fetch(`/api/analyze/${analyzeId}`)
      const sdata = await readJson(sres)
      if (!sres.ok) throw new Error(sdata.detail || t('err.analyzeStatus'))
      if (sdata.status === 'running') continue
      if (sdata.status === 'error') throw new Error(sdata.detail || t('err.analyzeFail'))
      info.value = sdata.info
      const best = sdata.info.formats.find(f => f.height > 0) || sdata.info.formats[0]
      selectedFormat.value = best ? best.format_id : ''
      return
    }
    throw new Error(t('err.analyzeTimeout'))
  } catch (e) {
    errorMsg.value = e.message
  } finally {
    analyzing.value = false
  }
}

async function readJson(res) {
  const text = await res.text()
  try {
    return JSON.parse(text)
  } catch {
    throw new Error(res.ok ? t('err.service')
      : t('err.serviceCode', { c: res.status }))
  }
}

async function startDownload() {
  if (!info.value) return
  errorMsg.value = ''
  try {
    const mode = selectedFormat.value === '__audio__' ? 'audio' : 'video'
    const fid = selectedFormat.value === '__audio__' ? 'best' : selectedFormat.value
    const res = await fetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: info.value.webpage_url, format_id: fid, mode }),
    })
    const data = await readJson(res)
    if (!res.ok) throw new Error(data.detail || t('err.downloadTask'))
    task.value = { task_id: data.task_id, status: 'downloading', progress: 0 }
    pollTimer = setInterval(pollTask, 1200)
  } catch (e) {
    errorMsg.value = e.message
  }
}

async function pollTask() {
  if (!task.value) return
  try {
    const res = await fetch(`/api/task/${task.value.task_id}`)
    const data = await res.json()
    if (res.ok) {
      task.value = data
      if (data.status === 'done' || data.status === 'error') {
        clearInterval(pollTimer)
        if (data.status === 'error') errorMsg.value = data.error || t('err.downloadFail')
      }
    }
  } catch { /* 网络抖动时下一轮重试 */ }
}

function saveFile() {
  window.open(`/api/file/${task.value.task_id}`, '_blank')
}

function reset() {
  info.value = null
  task.value = null
  errorMsg.value = ''
  url.value = ''
}

/* ---------------- 会员购买（模拟支付） ---------------- */
const plans = ref([])
const payModal = ref(false)
const payOrder = ref(null)
const payState = ref('qr') // qr | paying | paid
let payPoll = null

async function loadPlans() {
  try {
    const res = await fetch('/api/plans')
    plans.value = await res.json()
  } catch { /* 忽略 */ }
}
loadPlans()

/* 套餐文案本地化：后端 name/features 为中文，前端按 plan.id 用字典覆盖（缺失回退后端原文） */
const plansView = computed(() => plans.value.map(p => {
  const nameKey = `plans.${p.id}.name`, pKey = `plans.${p.id}.p`, fKey = `plans.${p.id}.f`
  return {
    ...p,
    name: t(nameKey) === nameKey ? p.name : t(nameKey),
    period: t(pKey) === pKey ? p.period : t(pKey),
    features: tl(fKey).length ? tl(fKey) : p.features,
  }
}))

const selectedPlan = ref(null)

function openPay(plan) {
  selectedPlan.value = plan
  payModal.value = true
  payState.value = 'qr'
}

async function createOrder(plan) {
  const res = await fetch('/api/pay/order', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plan_id: plan.id }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || t('err.order'))
  return data
}

async function startPay(plan) {
  try {
    selectedPlan.value = plan
    payOrder.value = null
    payState.value = 'paying'
    payModal.value = true
    const order = await createOrder(plan)
    payOrder.value = order
    // 轮询订单状态（真实渠道下由服务端异步回调改变状态）
    payPoll = setInterval(async () => {
      const r = await fetch(`/api/pay/order/${order.order_id}`)
      const d = await r.json()
      if (d.status === 'paid') {
        payState.value = 'paid'
        clearInterval(payPoll)
      }
    }, 1500)
  } catch (e) {
    payState.value = 'qr'
    errorMsg.value = e.message
  }
}

async function confirmMockPay() {
  if (!payOrder.value) return
  // 模拟渠道的"支付成功通知"
  await fetch('/api/pay/mock/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ order_id: payOrder.value.order_id }),
  })
}

function closePay() {
  payModal.value = false
  clearInterval(payPoll)
}

/* ---------------- AI 视频总结 v2（SSE 流式 + 页内选项卡） ---------------- */
const summaryTask = ref(null)   // {status, basis, title, webpage_url, ...}
const summaryPanel = ref(false) // 结果卡下方面板
const activeTab = ref('ai')     // ai | timeline | mindmap
const summaryMsg = ref('')
const streamText = ref('')      // 流式原始输出（AI 总结 tab 实时显示）
const summaryData = ref(null)   // done 后的结构化结果
const transcript = ref([])      // 字幕时间线 [{time, text}]
const quota = ref(null)         // {used, limit, remaining}
// 旧版「字幕设置」（B站 SESSDATA）已下线：dm/view 字幕通道免登录免 Cookie，
// 顺手清理老用户浏览器里的遗留数据
localStorage.removeItem('bili_sessdata')
let es = null                   // EventSource
let pollFallbackTimer = null
let pollElapsed = 0

const summaryBusy = computed(() =>
  !!summaryTask.value && ['fetching_subtitles', 'summarizing'].includes(summaryTask.value.status)
)

const summaryPhaseText = computed(() => {
  if (!summaryTask.value || !summaryBusy.value) return t('ai.btn')
  return {
    fetching_subtitles: t('ai.fetching'),
    summarizing: t('ai.summarizing'),
  }[summaryTask.value.status] || t('ai.summarizing')
})

const basisLabel = computed(() => {
  const b = summaryTask.value?.basis
  if (!b) return ''
  return b === 'subtitles' ? t('ai.basis.sub') : t('ai.basis.meta')
})

const basisTip = computed(() =>
  summaryTask.value?.basis === 'subtitles' ? t('ai.basisSubTip') : t('ai.basisMetaTip'))

async function loadQuota() {
  try {
    const r = await fetch('/api/summary/quota')
    if (r.ok) quota.value = await r.json()
  } catch { /* 忽略 */ }
}

function resetSummaryState() {
  summaryTask.value = null
  summaryPanel.value = false
  summaryMsg.value = ''
  streamText.value = ''
  summaryData.value = null
  transcript.value = []
  closeSummaryStream()
  clearInterval(pollFallbackTimer)
}

function closeSummaryStream() {
  if (es) { es.close(); es = null }
}

async function startSummary() {
  if (!info.value || summaryBusy.value) return
  summaryMsg.value = ''
  streamText.value = ''
  summaryData.value = null
  transcript.value = []
  try {
    const res = await fetch('/api/summary', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: info.value.webpage_url }),
    })
    const data = await readJson(res)
    if (!res.ok) throw new Error(data.detail || t('err.summaryTask'))
    summaryTask.value = { status: 'fetching_subtitles', webpage_url: info.value.webpage_url }
    summaryPanel.value = true
    activeTab.value = 'ai'
    openSummaryStream(data.task_id)
  } catch (e) {
    summaryMsg.value = e.message
  }
}

function openSummaryStream(taskId) {
  closeSummaryStream()
  es = new EventSource(`/api/summary/${taskId}/stream`)

  es.addEventListener('meta', (e) => {
    try { Object.assign(summaryTask.value, JSON.parse(e.data)) } catch { /* 忽略 */ }
  })

  es.addEventListener('delta', (e) => {
    try { streamText.value += JSON.parse(e.data).t } catch { /* 忽略 */ }
  })

  es.addEventListener('done', (e) => {
    try {
      const d = JSON.parse(e.data)
      Object.assign(summaryTask.value, { status: 'done', ...d })
      summaryData.value = d.result || {}
      transcript.value = d.transcript || []
    } catch { /* 忽略 */ }
    closeSummaryStream()
    loadQuota()
  })

  // 服务端业务错误（带 data 的 error 事件）
  es.addEventListener('error', (e) => {
    if (!e.data) return // 无 data = 连接层错误，交给 onerror 处理
    let msg = t('err.summaryFail')
    try { msg = JSON.parse(e.data).error || msg } catch { /* 忽略 */ }
    summaryMsg.value = msg
    summaryTask.value = { ...(summaryTask.value || {}), status: 'error' }
    closeSummaryStream()
    loadQuota()
  })

  // 连接层错误：若还没拿到结果，回退到轮询兜底
  es.onerror = () => {
    if (summaryTask.value?.status === 'done') { closeSummaryStream(); return }
    closeSummaryStream()
    startPollFallback(taskId)
  }
}

function startPollFallback(taskId) {
  pollElapsed = 0
  clearInterval(pollFallbackTimer)
  pollFallbackTimer = setInterval(async () => {
    pollElapsed += 2
    if (pollElapsed > 120) {
      clearInterval(pollFallbackTimer)
      summaryMsg.value = t('err.summaryTimeout')
      summaryTask.value = { ...(summaryTask.value || {}), status: 'error' }
      return
    }
    try {
      const res = await fetch(`/api/summary/${taskId}`)
      if (!res.ok) return
      const d = await res.json()
      summaryTask.value = d
      if (d.status === 'done') {
        clearInterval(pollFallbackTimer)
        summaryData.value = d.result || {}
        transcript.value = d.transcript || []
        loadQuota()
      } else if (d.status === 'error') {
        clearInterval(pollFallbackTimer)
        summaryMsg.value = d.error || t('err.summaryFail')
      }
    } catch { /* 下一轮重试 */ }
  }, 2000)
}

function fmtTime(sec) {
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

function chapterHref(time) {
  const u = summaryTask.value?.webpage_url || info.value?.webpage_url || ''
  if (/bilibili\.com|youtube\.com/.test(u)) {
    return u + (u.includes('?') ? '&' : '?') + 't=' + (time | 0)
  }
  return u
}

async function copySummary() {
  const r = summaryData.value
  if (!r) return
  const lines = [r.overview, '', ...r.points.map(p => `· ${p}`), '']
  if (r.chapters?.length) {
    lines.push(t('ai.summaryOf'))
    r.chapters.forEach(c => lines.push(`  [${c.label}] ${c.title}${c.detail ? ' — ' + c.detail : ''}`))
    lines.push('')
  }
  lines.push(t('ai.from') + (summaryTask.value?.webpage_url || ''))
  try {
    await navigator.clipboard.writeText(lines.join('\n'))
    alert(t('ai.copied'))
  } catch {
    alert(t('ai.copyFail'))
  }
}

/* ---------------- 字幕文件下载（SRT / TXT，浏览器本地生成） ---------------- */
function _fileBaseName() {
  return (summaryTask.value?.title || info.value?.title || t('ai.subtitleFile'))
    .replace(/[\\/:*?"<>|]+/g, '_').slice(0, 60) || t('ai.subtitleFile')
}

function _triggerDownload(content, filename, mime) {
  const blob = new Blob([content], { type: mime })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename
  a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 1000)
}

function _srtTime(sec) {
  const t = Math.max(0, Math.floor(sec))
  const h = String(Math.floor(t / 3600)).padStart(2, '0')
  const m = String(Math.floor((t % 3600) / 60)).padStart(2, '0')
  const s = String(t % 60).padStart(2, '0')
  return `${h}:${m}:${s},000`
}

function downloadSubtitle(kind) {
  if (!transcript.value.length) return
  if (kind === 'srt') {
    // 起始时间为该句时间戳，结束时间取下一句开始（最后一句 +2s 兜底）
    const body = transcript.value.map((t, i) => {
      const end = transcript.value[i + 1] ? transcript.value[i + 1].time : t.time + 2
      return `${i + 1}\n${_srtTime(t.time)} --> ${_srtTime(end)}\n${t.text}`
    }).join('\n\n')
    _triggerDownload(body + '\n', `${_fileBaseName()}.srt`, 'text/plain;charset=utf-8')
  } else {
    _triggerDownload(transcript.value.map(t => t.text).join('\n') + '\n',
      `${_fileBaseName()}.txt`, 'text/plain;charset=utf-8')
  }
}

// 追加式钩子：info 变化时联动总结状态（不动 analyze/reset 原有逻辑）
watch(info, (v) => {
  resetSummaryState()
  if (v) loadQuota()
})

/* ---------------- 工具函数 ---------------- */
function fmtDuration(sec) {
  if (!sec) return ''
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

function fmtSize(bytes) {
  if (!bytes) return ''
  if (bytes > 1024 * 1024 * 1024) return (bytes / 1024 / 1024 / 1024).toFixed(2) + ' GB'
  return (bytes / 1024 / 1024).toFixed(1) + ' MB'
}

function fmtSpeed(bps) {
  if (!bps) return ''
  return (bps / 1024 / 1024).toFixed(1) + ' MB/s'
}

const platformNames = {
  BiliBili: '哔哩哔哩', Douyin: '抖音', Youtube: 'YouTube', Xiaohongshu: '小红书',
  TikTok: 'TikTok', Weibo: '微博', Kuaishou: '快手', Twitter: 'X (Twitter)',
  Instagram: 'Instagram', 'EveryonesAmazing': '人人视频',
}
function platformName(key) {
  return platformNames[key] || key
}

const qrCanvas = ref(null)

/* 模拟二维码：根据订单号生成确定性伪随机方块（仅演示用） */
function drawQr() {
  const canvas = qrCanvas.value
  if (!canvas || !payOrder.value) return
  const ctx = canvas.getContext('2d')
  const seed = payOrder.value.order_id
  let s = 0
  for (const ch of seed) s = (s * 31 + ch.charCodeAt(0)) >>> 0
  const rand = () => { s = (s * 1103515245 + 12345) >>> 0; return (s >>> 16) / 65536 }
  const n = 21, cell = canvas.width / n
  ctx.fillStyle = '#fff'
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  ctx.fillStyle = '#1f2329'
  for (let y = 0; y < n; y++)
    for (let x = 0; x < n; x++)
      if (rand() > 0.52) ctx.fillRect(x * cell, y * cell, cell - 1, cell - 1)
  const eye = (ex, ey) => {
    ctx.fillStyle = '#1f2329'
    ctx.fillRect(ex, ey, cell * 5, cell * 5)
    ctx.fillStyle = '#fff'
    ctx.fillRect(ex + cell, ey + cell, cell * 3, cell * 3)
    ctx.fillStyle = '#1f2329'
    ctx.fillRect(ex + cell * 2, ey + cell * 2, cell, cell)
  }
  eye(cell * 1, cell * 1); eye(cell * 15, cell * 1); eye(cell * 1, cell * 15)
}

watch([payModal, payOrder, payState], () => nextTick(drawQr))

onBeforeUnmount(() => {
  clearInterval(pollTimer)
  clearInterval(payPoll)
  clearInterval(pollFallbackTimer)
  closeSummaryStream()
})
</script>

<template>
  <!-- ===== 导航栏 ===== -->
  <header class="nav">
    <div class="container nav-inner">
      <a class="logo" href="#">
        <span class="logo-mark">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none">
            <rect x="2" y="2" width="20" height="20" rx="6" fill="var(--primary)" />
            <path d="M10 8.5v7l6-3.5-6-3.5z" fill="#fff" />
          </svg>
        </span>
        极易下
      </a>
      <nav class="nav-links">
        <a href="#features">{{ t('nav.features') }}</a>
        <a href="#pricing">{{ t('nav.pricing') }}</a>
        <a href="#faq">{{ t('nav.faq') }}</a>
      </nav>
      <select class="lang-select" v-model="locale" :aria-label="t('nav.lang')" @change="setLocale(locale)">
        <option v-for="l in LANGS" :key="l.value" :value="l.value">{{ l.label }}</option>
      </select>
      <a class="btn btn-sm" href="#hero">{{ t('nav.start') }}</a>
    </div>
  </header>

  <!-- ===== Hero ===== -->
  <section id="hero" class="hero">
    <div class="container">
      <h1 class="hero-title">{{ t('hero.t1') }}<span class="grad">{{ t('hero.t2') }}</span></h1>
      <p class="hero-sub">{{ t('hero.sub') }}</p>
      <div class="input-row">
        <input
          v-model="url"
          class="url-input"
          type="text"
          :placeholder="t('hero.ph')"
          @keyup.enter="analyze"
        />
        <button class="btn btn-lg" :disabled="!canSubmit" @click="analyze">
          {{ analyzing ? t('hero.analyzing') : t('hero.analyze') }}
        </button>
      </div>
      <p v-if="errorMsg" class="error-tip">{{ errorMsg }}</p>
      <p class="hero-hint">{{ t('hero.hint') }}</p>
    </div>
  </section>

  <!-- ===== 解析结果 / 下载进度（双栏工作台布局） ===== -->
  <section v-if="info" class="result">
    <div class="container">
      <div class="result-card">
        <!-- 左栏：视频信息 + 下载操作 -->
        <aside class="result-side">
          <div class="result-media">
            <img v-if="info.thumbnail" :src="info.thumbnail" alt="" :aria-label="t('alt.cover')" referrerpolicy="no-referrer" />
            <div v-else class="thumb-fallback">{{ t('r.nocover') }}</div>
          </div>
          <div class="result-title-row">
            <h3 class="result-title">{{ info.title }}</h3>
            <button class="icon-btn" :title="t('r.reparse')" @click="reset">
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
            </button>
          </div>
          <p class="result-meta">
            <span class="platform-tag">{{ platformName(info.platform) }}</span>
            <span v-if="info.uploader">{{ info.uploader }}</span>
            <span v-if="info.duration">{{ t('r.duration', { d: fmtDuration(info.duration) }) }}</span>
          </p>

          <p class="fmt-label">{{ t('r.clarity') }}</p>
          <div class="fmt-list">
            <button
              v-for="f in videoFormats"
              :key="f.format_id"
              class="fmt-chip"
              :class="{ active: selectedFormat === f.format_id, vip: f.height >= 2160 }"
              @click="selectedFormat = f.format_id"
            >
              {{ f.label }}
              <small v-if="f.filesize">{{ fmtSize(f.filesize) }}</small>
            </button>
            <button
              class="fmt-chip"
              :class="{ active: selectedFormat === '__audio__' }"
              @click="selectedFormat = '__audio__'"
            >{{ t('r.audio') }}</button>
          </div>

          <!-- 进度区 -->
          <div v-if="task" class="progress-area">
            <template v-if="task.status === 'downloading' || task.status === 'processing'">
              <div class="progress-head">
                <span>{{ task.status === 'processing' ? t('r.merging') : t('r.downloading') }}</span>
                <span>{{ task.progress }}%</span>
              </div>
              <div class="progress-bar"><div class="progress-fill" :style="{ width: task.progress + '%' }"></div></div>
              <p class="progress-meta" v-if="task.speed">{{ t('r.speed', { s: fmtSpeed(task.speed) }) }}<template v-if="task.eta"> · {{ t('r.eta', { e: task.eta }) }}</template></p>
            </template>
            <template v-else-if="task.status === 'done'">
              <div class="done-row">
                <span class="done-badge">{{ t('r.done', { s: fmtSize(task.filesize) }) }}</span>
                <button class="btn" @click="saveFile">{{ t('r.saving') }}</button>
              </div>
              <p class="progress-meta">{{ t('r.keep') }}</p>
            </template>
            <template v-else-if="task.status === 'error'">
              <p class="error-tip">{{ t('r.fail', { e: task.error }) }}</p>
            </template>
          </div>

          <button v-else class="btn btn-lg btn-block" @click="startDownload">
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m0 0l-4-4m4 4l4-4M4 19h16"/></svg>
            {{ t('r.download') }}
          </button>
        </aside>

        <!-- 右栏：AI 总结工作台 -->
        <section class="result-main">
          <div class="ai-row">
            <button class="btn ai-btn" :disabled="summaryBusy" @click="startSummary">
              <span v-if="summaryBusy" class="spinner"></span>{{ summaryPhaseText }}
            </button>
            <span v-if="quota" class="quota-tip">{{ t('ai.quota', { r: quota.remaining, l: quota.limit }) }}</span>
          </div>
          <p v-if="summaryMsg" class="error-tip">{{ summaryMsg }}</p>

          <!-- 总结面板（页内选项卡，SSE 流式） -->
          <div v-if="summaryPanel" class="summary-panel">
            <div class="summary-tabs">
              <button class="stab" :class="{ active: activeTab === 'ai' }" @click="activeTab = 'ai'">
                <svg class="stab-ico" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M16 13H8M16 17H8"/></svg>
                {{ t('ai.tab.ai') }}
              </button>
              <button class="stab" :class="{ active: activeTab === 'timeline' }" @click="activeTab = 'timeline'">
                <svg class="stab-ico" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
                {{ t('ai.tab.timeline') }}
              </button>
              <button class="stab" :class="{ active: activeTab === 'mindmap' }" @click="activeTab = 'mindmap'">
                <svg class="stab-ico" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.59 13.51l6.83 3.98M15.41 6.51l-6.82 3.98"/></svg>
                {{ t('ai.tab.mindmap') }}
              </button>
              <span class="stab-right">
                <span v-if="basisLabel" class="basis-badge" :class="summaryTask?.basis" :title="basisTip">{{ basisLabel }}</span>
                <template v-if="activeTab === 'timeline' && transcript.length">
                  <button class="link-btn copy-link" :title="t('ai.srtTip')" @click="downloadSubtitle('srt')">{{ t('ai.srt') }}</button>
                  <button class="link-btn copy-link" :title="t('ai.txtTip')" @click="downloadSubtitle('txt')">{{ t('ai.txt') }}</button>
                </template>
                <button v-if="summaryData" class="link-btn copy-link" @click="copySummary">{{ t('ai.copy') }}</button>
              </span>
            </div>

            <div class="summary-content">
              <!-- AI 总结 -->
              <template v-if="activeTab === 'ai'">
                <pre v-if="!summaryData" class="stream-box">{{ streamText || t('ai.preparing') }}</pre>
                <template v-else>
                  <p class="summary-line">{{ summaryData.overview }}</p>
                  <ul class="summary-points">
                    <li v-for="p in summaryData.points" :key="p">{{ p }}</li>
                  </ul>
                  <template v-if="summaryData.chapters?.length">
                    <h4 class="summary-sec">{{ t('ai.chapters') }} <small>{{ t('ai.chaptersHint') }}</small></h4>
                    <div v-for="c in summaryData.chapters" :key="c.time + c.title" class="chapter-row">
                      <a class="ts-chip" :href="chapterHref(c.time)" target="_blank" rel="noopener">{{ c.label }}</a>
                      <div class="chapter-text">
                        <b>{{ c.title }}</b>
                        <p v-if="c.detail">{{ c.detail }}</p>
                      </div>
                    </div>
                  </template>
                </template>
              </template>

              <!-- 字幕时间线 -->
              <template v-if="activeTab === 'timeline'">
                <div v-if="transcript.length" class="transcript-list">
                  <div v-for="(t2, i) in transcript" :key="i" class="chapter-row tl-row">
                    <a class="ts-chip" :href="chapterHref(t2.time)" target="_blank" rel="noopener">{{ fmtTime(t2.time) }}</a>
                    <p class="tl-text">{{ t2.text }}</p>
                  </div>
                </div>
                <p v-else class="empty-tip">{{ summaryBusy ? t('ai.loadingSubs') : t('ai.noSubs') }}</p>
              </template>

              <!-- 思维导图 -->
              <template v-if="activeTab === 'mindmap'">
                <MindMap v-if="summaryData?.mindmap" :data="summaryData.mindmap" />
                <p v-else class="empty-tip">{{ summaryBusy ? t('ai.mmGenerating') : t('ai.mmFailed') }}</p>
              </template>
            </div>
          </div>
          <div v-else class="summary-empty">
            <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M16 13H8M16 17H8"/></svg>
            <p>{{ t('ai.placeholder') }}</p>
          </div>
        </section>
      </div>
    </div>
  </section>

  <!-- ===== 三步流程 ===== -->
  <section class="steps">
    <div class="container">
      <div class="steps-grid">
        <div class="step">
          <span class="step-num">1</span>
          <div>
            <h4>{{ t('steps.t1') }}</h4>
            <p>{{ t('steps.d1') }}</p>
          </div>
        </div>
        <div class="step">
          <span class="step-num">2</span>
          <div>
            <h4>{{ t('steps.t2') }}</h4>
            <p>{{ t('steps.d2') }}</p>
          </div>
        </div>
        <div class="step">
          <span class="step-num">3</span>
          <div>
            <h4>{{ t('steps.t3') }}</h4>
            <p>{{ t('steps.d3') }}</p>
          </div>
        </div>
      </div>
    </div>
  </section>

  <!-- ===== 功能卖点 ===== -->
  <section id="features" class="features">
    <div class="container">
      <h2 class="sec-title">{{ t('features.t1') }}<span class="grad">{{ t('features.t2') }}</span></h2>
      <p class="sec-sub">{{ t('features.sub') }}</p>
      <div class="feature-grid">
        <div class="feature-card">
          <div class="feature-icon"><svg viewBox="0 0 24 24" width="21" height="21" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg></div>
          <h4>{{ t('features.f1t') }}</h4>
          <p>{{ t('features.f1d') }}</p>
          <div class="tags"><span>{{ t('features.f1a') }}</span><span>{{ t('features.f1b') }}</span></div>
        </div>
        <div class="feature-card">
          <div class="feature-icon"><svg viewBox="0 0 24 24" width="21" height="21" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M8 12l3 3 5-6"/></svg></div>
          <h4>{{ t('features.f2t') }}</h4>
          <p>{{ t('features.f2d') }}</p>
          <div class="tags"><span>{{ t('features.f2a') }}</span><span>{{ t('features.f2b') }}</span></div>
        </div>
        <div class="feature-card">
          <div class="feature-icon"><svg viewBox="0 0 24 24" width="21" height="21" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="2" width="14" height="20" rx="2"/><path d="M12 18h.01"/></svg></div>
          <h4>{{ t('features.f3t') }}</h4>
          <p>{{ t('features.f3d') }}</p>
          <div class="tags"><span>{{ t('features.f3a') }}</span><span>{{ t('features.f3b') }}</span></div>
        </div>
        <div class="feature-card">
          <div class="feature-icon"><svg viewBox="0 0 24 24" width="21" height="21" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg></div>
          <h4>{{ t('features.f4t') }}</h4>
          <p>{{ t('features.f4d') }}</p>
          <div class="tags"><span>{{ t('features.f4a') }}</span><span>{{ t('features.f4b') }}</span></div>
        </div>
        <div class="feature-card">
          <div class="feature-icon"><svg viewBox="0 0 24 24" width="21" height="21" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m0 0l-4-4m4 4l4-4M4 19h16"/></svg></div>
          <h4>{{ t('features.f5t') }}</h4>
          <p>{{ t('features.f5d') }}</p>
          <div class="tags"><span>{{ t('features.f5a') }}</span><span>{{ t('features.f5b') }}</span></div>
        </div>
        <div class="feature-card">
          <div class="feature-icon"><svg viewBox="0 0 24 24" width="21" height="21" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg></div>
          <h4>{{ t('features.f6t') }}</h4>
          <p>{{ t('features.f6d') }}</p>
          <div class="tags"><span>{{ t('features.f6a') }}</span><span>{{ t('features.f6b') }}</span></div>
        </div>
      </div>
    </div>
  </section>

  <!-- ===== 定价 ===== -->
  <section id="pricing" class="pricing">
    <div class="container">
      <h2 class="sec-title">{{ t('pricing.t1') }}<span class="grad">{{ t('pricing.t2') }}</span></h2>
      <p class="sec-sub">{{ t('pricing.sub') }}</p>
      <div class="plan-grid">
        <div
          v-for="p in plansView"
          :key="p.id"
          class="plan-card"
          :class="{ highlight: p.highlight }"
        >
          <span v-if="p.highlight" class="plan-badge">{{ t('pricing.popular') }}</span>
          <h4>{{ p.name }}</h4>
          <div class="plan-price">
            <template v-if="p.price > 0"><em>¥</em>{{ p.price }}<small>{{ p.period }}</small></template>
            <template v-else><em>¥</em>0<small>{{ t('plans.free.p') }}</small></template>
          </div>
          <ul class="plan-feats">
            <li v-for="f in p.features" :key="f">✓ {{ f }}</li>
          </ul>
          <button
            class="btn btn-block"
            :class="{ 'btn-outline': !p.highlight, 'btn-lg': p.highlight }"
            @click="p.price > 0 ? startPay(p) : null"
          >
            {{ p.price > 0 ? t('pricing.buy') : t('pricing.freeBtn') }}
          </button>
        </div>
      </div>
    </div>
  </section>

  <!-- ===== FAQ ===== -->
  <section id="faq" class="faq">
    <div class="container faq-inner">
      <h2 class="sec-title">{{ t('faq.title') }}</h2>
      <details class="faq-item">
        <summary>{{ t('faq.q1') }}</summary>
        <p>{{ t('faq.a1') }}</p>
      </details>
      <details class="faq-item">
        <summary>{{ t('faq.q2') }}</summary>
        <p>{{ t('faq.a2') }}</p>
      </details>
      <details class="faq-item">
        <summary>{{ t('faq.q3') }}</summary>
        <p>{{ t('faq.a3') }}</p>
      </details>
      <details class="faq-item">
        <summary>{{ t('faq.q4') }}</summary>
        <p>{{ t('faq.a4') }}</p>
      </details>
      <details class="faq-item">
        <summary>{{ t('faq.q5') }}</summary>
        <p>{{ t('faq.a5') }}</p>
      </details>
      <details class="faq-item">
        <summary>{{ t('faq.q6') }}</summary>
        <p>{{ t('faq.a6') }}</p>
      </details>
    </div>
  </section>

  <!-- ===== 页脚 ===== -->
  <footer class="footer">
    <div class="container">
      <p class="footer-logo">极易下</p>
      <p class="footer-disclaimer">{{ t('footer.disclaimer') }}</p>
      <p class="footer-copy">{{ t('footer.copy') }}</p>
    </div>
  </footer>

  <!-- ===== 模拟收银台弹窗 ===== -->
  <div v-if="payModal" class="modal-mask" @click.self="closePay">
    <div class="modal">
      <template v-if="payState === 'paying'">
        <h3 class="modal-title">{{ selectedPlan?.name }}</h3>
        <p class="modal-amount">¥ {{ selectedPlan?.price }}</p>
        <div class="fake-qr" aria-hidden="true">
          <canvas ref="qrCanvas" width="180" height="180"></canvas>
          <p class="qr-tip">{{ t('pay.scan') }}<br />{{ t('pay.mockTip') }}</p>
        </div>
        <button class="btn btn-block btn-lg" @click="confirmMockPay">{{ t('pay.confirm') }}</button>
        <button class="link-btn" @click="closePay">{{ t('pay.cancel') }}</button>
      </template>
      <template v-else-if="payState === 'paid'">
        <div class="pay-success">
          <div class="success-icon">✓</div>
          <h3>{{ t('pay.success') }}</h3>
          <p>{{ t('pay.successDesc', { n: selectedPlan?.name }) }}<br />{{ t('pay.mockNote') }}</p>
        </div>
        <button class="btn btn-block btn-lg" @click="closePay">{{ t('pay.ok') }}</button>
      </template>
    </div>
  </div>
</template>

<script>
// 模拟二维码：根据订单号生成确定性伪随机方块（仅演示用）
export default {
  watch: {
    payModal(v) {
      if (v) this.$nextTick(this.drawQr)
    },
  },
  methods: {
    drawQr() {
      const canvas = this.$refs.qrCanvas
      if (!canvas || !this.payOrder) return
      const ctx = canvas.getContext('2d')
      const seed = this.payOrder.order_id
      let s = 0
      for (const ch of seed) s = (s * 31 + ch.charCodeAt(0)) >>> 0
      const rand = () => { s = (s * 1103515245 + 12345) >>> 0; return (s >>> 16) / 65536 }
      const n = 21, cell = canvas.width / n
      ctx.fillStyle = '#fff'
      ctx.fillRect(0, 0, canvas.width, canvas.height)
      ctx.fillStyle = '#1f2329'
      for (let y = 0; y < n; y++)
        for (let x = 0; x < n; x++)
          if (rand() > 0.52) ctx.fillRect(x * cell, y * cell, cell - 1, cell - 1)
      // 三个定位角
      const eye = (ex, ey) => {
        ctx.fillStyle = '#1f2329'
        ctx.fillRect(ex, ey, cell * 5, cell * 5)
        ctx.fillStyle = '#fff'
        ctx.fillRect(ex + cell, ey + cell, cell * 3, cell * 3)
        ctx.fillStyle = '#1f2329'
        ctx.fillRect(ex + cell * 2, ey + cell * 2, cell, cell)
      }
      eye(cell * 1, cell * 1); eye(cell * 15, cell * 1); eye(cell * 1, cell * 15)
    },
  },
}
</script>

<style scoped>
/* ===== 通用 ===== */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: var(--primary);
  color: #fff;
  border: none;
  border-radius: 10px;
  padding: 10px 22px;
  font-size: 15px;
  font-weight: 500;
  transition: background 0.15s, transform 0.1s;
}
.btn:hover { background: var(--primary-dark); }
.btn:active { transform: scale(0.98); }
.btn:disabled { background: #8ec8c2; cursor: not-allowed; }
.btn-lg { padding: 13px 30px; font-size: 16px; }
.btn-sm { padding: 7px 16px; font-size: 14px; }
.btn-block { width: 100%; }
.btn-outline {
  background: #fff;
  color: var(--primary);
  border: 1px solid var(--primary);
}
.btn-outline:hover { background: var(--primary-bg); }
.grad {
  background: linear-gradient(90deg, #0d9488, #2dd4bf);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.sec-title { font-size: 24px; font-weight: 600; text-align: center; }
.sec-sub { text-align: center; color: var(--text-3); margin-top: 10px; font-size: 14px; }

/* ===== 导航 ===== */
.nav {
  position: sticky;
  top: 0;
  z-index: 50;
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--border);
}
.nav-inner {
  display: flex;
  align-items: center;
  height: 52px;
  gap: 28px;
}
.logo {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 17px;
  font-weight: 600;
}
.logo-mark { display: flex; }
.nav-links { display: flex; gap: 24px; margin-left: auto; }
.nav-links a { color: var(--text-2); font-size: 14px; }
.nav-links a:hover { color: var(--primary); }
.lang-select {
  height: 30px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: #fff;
  color: var(--text-2);
  font-size: 12.5px;
  padding: 0 6px;
  cursor: pointer;
  outline: none;
}
.lang-select:hover { border-color: var(--primary); color: var(--primary); }
.lang-select:focus { border-color: var(--primary); }

/* ===== Hero ===== */
.hero { padding: 52px 0 36px; text-align: center; }
.hero-title { font-size: 36px; font-weight: 700; letter-spacing: 0.5px; }
.hero-sub { color: var(--text-2); font-size: 15.5px; margin-top: 14px; }
.input-row {
  display: flex;
  gap: 10px;
  max-width: 680px;
  margin: 26px auto 0;
}
.url-input {
  flex: 1;
  height: 48px;
  border: 1.5px solid var(--border);
  border-radius: 10px;
  padding: 0 16px;
  font-size: 14px;
  outline: none;
  background: #fff;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.url-input:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px rgba(13, 148, 136, 0.14);
}
.error-tip { color: #c2453a; margin-top: 12px; font-size: 13.5px; }
.hero-hint { color: var(--text-3); font-size: 12.5px; margin-top: 14px; }

/* ===== 解析结果（双栏工作台） ===== */
.result { padding: 6px 0 28px; }
.result-card {
  display: grid;
  grid-template-columns: 268px minmax(0, 1fr);
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: var(--shadow);
  overflow: hidden;
}
/* 左栏：视频信息 + 下载操作 */
.result-side {
  padding: 16px;
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
}
.result-media {
  border-radius: 8px;
  overflow: hidden;
  background: var(--bg-page);
  margin-bottom: 12px;
}
.result-media img { width: 100%; display: block; }
.thumb-fallback {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 150px;
  color: var(--text-3);
  font-size: 13px;
}
.result-title-row { display: flex; align-items: flex-start; gap: 8px; }
.result-title { font-size: 15.5px; font-weight: 600; line-height: 1.45; flex: 1; }
.icon-btn {
  border: none;
  background: var(--bg-page);
  color: var(--text-3);
  width: 26px;
  height: 26px;
  border-radius: 6px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}
.icon-btn:hover { color: var(--text-1); }
.result-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--text-2);
  font-size: 12.5px;
  margin-top: 8px;
  flex-wrap: wrap;
}
.platform-tag {
  background: var(--primary-bg);
  color: var(--primary);
  font-size: 11.5px;
  padding: 2px 8px;
  border-radius: 5px;
}
.fmt-label { color: var(--text-2); font-size: 13px; margin: 16px 0 8px; }
.fmt-list { display: flex; flex-wrap: wrap; gap: 7px; }
.fmt-chip {
  border: 1.5px solid var(--border);
  background: #fff;
  color: var(--text-1);
  border-radius: 8px;
  padding: 7px 12px;
  font-size: 13px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
  transition: all 0.12s;
}
.fmt-chip small { color: var(--text-3); font-size: 10.5px; }
.fmt-chip:hover { border-color: var(--primary); }
.fmt-chip.active {
  border-color: var(--primary);
  background: var(--primary-bg);
  color: var(--primary);
}
.fmt-chip.active small { color: var(--primary); }
.progress-area { margin-top: 16px; }
.progress-head {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  color: var(--text-1);
  margin-bottom: 7px;
}
.progress-bar {
  height: 8px;
  background: var(--bg-page);
  border-radius: 5px;
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #0d9488, #2dd4bf);
  border-radius: 5px;
  transition: width 0.4s;
}
.progress-meta { color: var(--text-3); font-size: 12px; margin-top: 7px; }
.done-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.done-badge {
  color: #0f766e;
  background: #e2f4f1;
  padding: 7px 12px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
}

/* 右栏：AI 总结工作台 */
.result-main {
  padding: 16px 18px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-page);
}
.summary-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: var(--text-3);
  font-size: 13px;
  padding: 40px 0;
  border: 1px dashed var(--border);
  border-radius: 10px;
  background: #fff;
}

/* ===== 三步流程 ===== */
.steps { padding: 34px 0; background: var(--bg-card); border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); }
.steps-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
.step {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 18px 20px;
  display: flex;
  align-items: flex-start;
  gap: 14px;
  text-align: left;
}
.step-num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--primary-bg);
  color: var(--primary);
  font-weight: 600;
  font-size: 13px;
  flex-shrink: 0;
}
.step h4 { font-size: 14.5px; margin-bottom: 4px; }
.step p { color: var(--text-2); font-size: 13px; line-height: 1.55; }

/* ===== 功能 ===== */
.features { padding: 44px 0; }
.feature-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
  margin-top: 26px;
}
.feature-card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 18px 20px;
  transition: transform 0.15s, box-shadow 0.15s;
}
.feature-card:hover { transform: translateY(-3px); box-shadow: var(--shadow-lift); }
.feature-icon {
  width: 38px;
  height: 38px;
  border-radius: 9px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 12px;
  background: var(--primary-bg);
  color: var(--primary);
}
.feature-card h4 { font-size: 14.5px; margin-bottom: 6px; }
.feature-card p { color: var(--text-2); font-size: 13px; line-height: 1.6; }
.tags { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
.tags span { color: var(--text-3); font-size: 11.5px; }

/* ===== 定价 ===== */
.pricing { padding: 44px 0; background: var(--bg-card); border-top: 1px solid var(--border); }
.plan-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
  max-width: 900px;
  margin: 26px auto 0;
  align-items: stretch;
}
.plan-card {
  position: relative;
  background: #fff;
  border: 1.5px solid var(--border);
  border-radius: 12px;
  padding: 22px 20px;
  display: flex;
  flex-direction: column;
}
.plan-card.highlight {
  border-color: var(--primary);
  box-shadow: 0 6px 20px rgba(13, 148, 136, 0.16);
}
.plan-badge {
  position: absolute;
  top: -11px;
  left: 50%;
  transform: translateX(-50%);
  background: var(--primary);
  color: #fff;
  font-size: 11.5px;
  padding: 3px 12px;
  border-radius: 16px;
  white-space: nowrap;
}
.plan-card h4 { font-size: 15.5px; text-align: center; }
.plan-price { text-align: center; margin: 14px 0 16px; font-weight: 700; font-size: 32px; }
.plan-price em { font-style: normal; font-size: 17px; margin-right: 2px; }
.plan-price small { font-size: 13px; color: var(--text-3); font-weight: 400; margin-left: 4px; }
.plan-feats { list-style: none; flex: 1; margin-bottom: 18px; }
.plan-feats li {
  color: var(--text-2);
  font-size: 13px;
  padding: 6px 0;
  border-bottom: 1px dashed var(--border);
}
.plan-feats li:last-child { border-bottom: none; }

/* ===== FAQ ===== */
.faq { padding: 44px 0; }
.faq-inner { max-width: 720px; }
.faq .sec-title { margin-bottom: 22px; }
.faq-item {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 13px 18px;
  margin-bottom: 9px;
}
.faq-item summary {
  font-weight: 500;
  cursor: pointer;
  font-size: 14px;
  list-style: none;
}
.faq-item summary::after { content: '+'; float: right; color: var(--text-3); }
.faq-item[open] summary::after { content: '−'; }
.faq-item p { color: var(--text-2); font-size: 13px; line-height: 1.65; margin-top: 10px; }

/* ===== 页脚 ===== */
.footer { background: var(--bg-page); border-top: 1px solid var(--border); padding: 28px 0 36px; text-align: center; }
.footer-logo { font-weight: 600; font-size: 15px; }
.footer-disclaimer {
  color: var(--text-3);
  font-size: 12px;
  line-height: 1.75;
  max-width: 620px;
  margin: 10px auto;
}
.footer-copy { color: var(--text-3); font-size: 12px; }

/* ===== 模拟收银台 ===== */
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(31, 35, 41, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
  padding: 20px;
}
.modal {
  background: #fff;
  border-radius: 16px;
  padding: 32px 28px;
  width: 360px;
  max-width: 100%;
  text-align: center;
}
.modal-title { font-size: 17px; }
.modal-amount { font-size: 34px; font-weight: 700; margin: 12px 0 20px; }
.fake-qr { margin-bottom: 22px; }
.fake-qr canvas {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px;
}
.qr-tip { color: var(--text-3); font-size: 12.5px; margin-top: 10px; line-height: 1.7; }
.link-btn {
  background: none;
  border: none;
  color: var(--text-3);
  font-size: 14px;
  margin-top: 14px;
}
.link-btn:hover { color: var(--text-1); }
.pay-success { padding: 12px 0 24px; }
.success-icon {
  width: 64px;
  height: 64px;
  margin: 0 auto 18px;
  border-radius: 50%;
  background: #e1f5ee;
  color: #0f6e56;
  font-size: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.pay-success h3 { font-size: 18px; margin-bottom: 10px; }
.pay-success p { color: var(--text-2); font-size: 14px; line-height: 1.7; }

/* ===== AI 视频总结（右栏工作台面板） ===== */
.ai-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.ai-btn { min-width: 150px; }
.spinner {
  width: 13px;
  height: 13px;
  border: 2px solid rgba(255, 255, 255, 0.4);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.quota-tip { color: var(--text-3); font-size: 12px; }

.summary-panel {
  margin-top: 12px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.summary-tabs {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 6px 8px;
  background: var(--bg-card);
  border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
}
.stab {
  border: none;
  background: transparent;
  color: var(--text-2);
  font-size: 13px;
  padding: 6px 10px;
  border-radius: 7px;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  transition: all 0.12s;
}
.stab-ico { flex-shrink: 0; }
.stab:hover { color: var(--text-1); background: var(--bg-page); }
.stab.active { color: var(--primary); background: var(--primary-bg); font-weight: 600; }
.stab-right { margin-left: auto; display: flex; align-items: center; gap: 10px; }
.copy-link { font-size: 12.5px; margin-top: 0; }
.summary-content { padding: 13px 15px 15px; max-height: 620px; overflow-y: auto; flex: 1; }

.stream-box {
  margin: 0;
  font-family: inherit;
  font-size: 13.5px;
  line-height: 1.75;
  color: var(--text-1);
  white-space: pre-wrap;
  word-break: break-word;
}
.stream-box::after { content: '▌'; color: var(--primary); animation: blink 1s step-start infinite; }
@keyframes blink { 50% { opacity: 0; } }

.basis-badge {
  font-size: 11px;
  padding: 2px 9px;
  border-radius: 16px;
  white-space: nowrap;
  cursor: help;
}
.basis-badge.subtitles { background: #e2f4f1; color: #0f766e; }
.basis-badge.meta { background: #f5ecdd; color: #a3762a; }

.summary-line {
  font-size: 14px;
  font-weight: 500;
  line-height: 1.65;
  padding: 10px 12px;
  background: var(--primary-bg);
  border-radius: 8px;
}
.summary-points { margin: 12px 0 0; padding-left: 0; list-style: none; }
.summary-points li {
  position: relative;
  padding: 4px 0 4px 18px;
  color: var(--text-1);
  font-size: 13.5px;
  line-height: 1.6;
}
.summary-points li::before {
  content: '';
  position: absolute;
  left: 4px;
  top: 12px;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--primary);
}
.summary-sec { font-size: 14px; margin: 18px 0 8px; }
.summary-sec small { color: var(--text-3); font-weight: 400; font-size: 11.5px; margin-left: 6px; }
.chapter-row { display: flex; gap: 10px; padding: 7px 0; align-items: flex-start; }
.ts-chip {
  flex-shrink: 0;
  background: var(--primary);
  color: #fff;
  font-size: 12px;
  padding: 3px 8px;
  border-radius: 7px;
  text-decoration: none;
  margin-top: 1px;
  min-width: 44px;
  text-align: center;
}
.ts-chip:hover { background: var(--primary-dark); }
.chapter-text b { font-size: 13.5px; }
.chapter-text p, .tl-text { color: var(--text-2); font-size: 12.5px; margin-top: 2px; line-height: 1.55; }
.transcript-list { display: block; }
.empty-tip { color: var(--text-3); font-size: 13px; text-align: center; padding: 24px 0; }

/* ===== 响应式 ===== */
@media (max-width: 900px) {
  .hero-title { font-size: 30px; }
  .feature-grid { grid-template-columns: repeat(2, 1fr); }
  .plan-grid { grid-template-columns: 1fr; max-width: 400px; }
  .plan-card.highlight { order: -1; }
  .steps-grid { grid-template-columns: 1fr; }
  .result-card { grid-template-columns: 1fr; }
  .result-side { border-right: none; border-bottom: 1px solid var(--border); }
}
@media (max-width: 600px) {
  .nav-links { display: none; }
  .hero { padding: 40px 0 30px; }
  .hero-title { font-size: 25px; }
  .hero-sub { font-size: 14px; }
  .input-row { flex-direction: column; }
  .btn-lg { width: 100%; }
  .feature-grid { grid-template-columns: 1fr; }
  .done-row { flex-direction: column; align-items: stretch; text-align: center; }
  .summary-content { max-height: 60vh; padding: 12px; }
  .stab { font-size: 12px; padding: 5px 8px; }
  .result-side, .result-main { padding: 13px; }
}
</style>
