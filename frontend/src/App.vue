<script setup>
import { ref, computed, watch, nextTick, onBeforeUnmount } from 'vue'
import MindMap from './components/MindMap.vue'

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
    if (!res.ok) throw new Error(data.detail || data.message || '解析失败，请稍后再试')
    // 轮询解析结果（YouTube 等平台解析较慢，后端异步执行）
    const analyzeId = data.analyze_id
    for (let i = 0; i < 100; i++) {
      await new Promise(r => setTimeout(r, 1500))
      const sres = await fetch(`/api/analyze/${analyzeId}`)
      const sdata = await readJson(sres)
      if (!sres.ok) throw new Error(sdata.detail || '解析状态查询失败')
      if (sdata.status === 'running') continue
      if (sdata.status === 'error') throw new Error(sdata.detail || '解析失败')
      info.value = sdata.info
      const best = sdata.info.formats.find(f => f.height > 0) || sdata.info.formats[0]
      selectedFormat.value = best ? best.format_id : ''
      return
    }
    throw new Error('解析超时，请稍后重试')
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
    throw new Error(res.ok ? '服务响应异常，请稍后重试'
      : `服务暂时不可用（${res.status}），请稍后重试`)
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
    if (!res.ok) throw new Error(data.detail || '创建下载任务失败')
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
        if (data.status === 'error') errorMsg.value = data.error || '下载失败'
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
  if (!res.ok) throw new Error(data.detail || '下单失败')
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
const showCookieCfg = ref(false)
const biliCookie = ref(localStorage.getItem('bili_sessdata') || '')
let es = null                   // EventSource
let pollFallbackTimer = null
let pollElapsed = 0

function saveBiliCookie() {
  if (biliCookie.value.trim()) {
    localStorage.setItem('bili_sessdata', biliCookie.value.trim())
  } else {
    localStorage.removeItem('bili_sessdata')
  }
}

const summaryBusy = computed(() =>
  !!summaryTask.value && ['fetching_subtitles', 'summarizing'].includes(summaryTask.value.status)
)

const summaryPhaseText = computed(() => {
  if (!summaryTask.value || !summaryBusy.value) return '✨ AI 总结'
  return {
    fetching_subtitles: '正在提取字幕…',
    summarizing: 'AI 总结中…',
  }[summaryTask.value.status] || 'AI 总结中…'
})

const basisLabel = computed(() => {
  const b = summaryTask.value?.basis
  if (!b) return ''
  return b === 'subtitles' ? '基于字幕总结' : '基于视频信息总结'
})

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
      body: JSON.stringify({
        url: info.value.webpage_url,
        bili_cookie: localStorage.getItem('bili_sessdata') || '',
      }),
    })
    const data = await readJson(res)
    if (!res.ok) throw new Error(data.detail || '创建总结任务失败')
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
    let msg = '总结失败，请稍后重试'
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
      summaryMsg.value = '总结超时，请稍后重试'
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
        summaryMsg.value = d.error || '总结失败，请稍后重试'
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
    lines.push('章节大纲：')
    r.chapters.forEach(c => lines.push(`  [${c.label}] ${c.title}${c.detail ? ' — ' + c.detail : ''}`))
    lines.push('')
  }
  lines.push(`来自「极易下」AI 视频总结：${summaryTask.value?.webpage_url || ''}`)
  try {
    await navigator.clipboard.writeText(lines.join('\n'))
    alert('总结已复制到剪贴板')
  } catch {
    alert('复制失败，请手动选择文本复制')
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
        <a href="#features">功能</a>
        <a href="#pricing">定价</a>
        <a href="#faq">常见问题</a>
      </nav>
      <a class="btn btn-sm" href="#hero">免费开始</a>
    </div>
  </header>

  <!-- ===== Hero ===== -->
  <section id="hero" class="hero">
    <div class="container">
      <h1 class="hero-title">全网视频下载，<span class="grad">一键保存</span></h1>
      <p class="hero-sub">支持 B站 / 抖音 / YouTube / 小红书 等 1000+ 平台，无需登录、不限设备，手机电脑都能用</p>
      <div class="input-row">
        <input
          v-model="url"
          class="url-input"
          type="text"
          placeholder="粘贴视频链接，例如 https://www.bilibili.com/video/..."
          @keyup.enter="analyze"
        />
        <button class="btn btn-lg" :disabled="!canSubmit" @click="analyze">
          {{ analyzing ? '解析中…' : '开始解析' }}
        </button>
      </div>
      <p v-if="errorMsg" class="error-tip">{{ errorMsg }}</p>
      <p class="hero-hint">支持平台举例：B站 · 抖音 · YouTube · 小红书 · 微博 · 快手 · X · Instagram …</p>
    </div>
  </section>

  <!-- ===== 解析结果 / 下载进度 ===== -->
  <section v-if="info" class="result">
    <div class="container">
      <div class="result-card">
        <div class="result-media">
          <img v-if="info.thumbnail" :src="info.thumbnail" alt="视频封面" referrerpolicy="no-referrer" />
          <div v-else class="thumb-fallback">无封面</div>
        </div>
        <div class="result-body">
          <div class="result-title-row">
            <h3 class="result-title">{{ info.title }}</h3>
            <button class="icon-btn" title="重新解析" @click="reset">✕</button>
          </div>
          <p class="result-meta">
            <span class="platform-tag">{{ platformName(info.platform) }}</span>
            <span v-if="info.uploader">{{ info.uploader }}</span>
            <span v-if="info.duration">时长 {{ fmtDuration(info.duration) }}</span>
          </p>

          <!-- AI 视频总结（新增） -->
          <div class="ai-row">
            <button class="btn ai-btn" :disabled="summaryBusy" @click="startSummary">
              <span v-if="summaryBusy" class="spinner"></span>{{ summaryPhaseText }}
            </button>
            <span v-if="quota" class="quota-tip">今日免费 {{ quota.remaining }}/{{ quota.limit }} 次</span>
            <button class="cookie-toggle" title="配置B站Cookie，解锁AI字幕" @click="showCookieCfg = !showCookieCfg">
              ⚙ 字幕设置
            </button>
          </div>
          <div v-if="showCookieCfg" class="cookie-cfg">
            <p class="cookie-tip">
              可选：粘贴你自己的 <b>B站 SESSDATA</b> Cookie，即可对有AI字幕的视频生成高质量总结（B站要求登录才能读取AI字幕）。
              Cookie 仅保存在<b>你的浏览器</b>，总结时随请求传给服务器用完即弃，服务端不存储。获取方式：登录B站 → F12 → 应用 → Cookies → 复制 SESSDATA 的值。
            </p>
            <div class="cookie-row">
              <input v-model="biliCookie" class="cookie-input" type="password" placeholder="粘贴 SESSDATA 值（留空则不使用AI字幕）" />
              <button class="btn btn-sm" @click="saveBiliCookie">{{ biliCookie ? '已保存 ✓' : '保存' }}</button>
            </div>
          </div>
          <p v-if="summaryMsg" class="error-tip">{{ summaryMsg }}</p>

          <!-- 总结面板（页内选项卡，SSE 流式） -->
          <div v-if="summaryPanel" class="summary-panel">
            <div class="summary-tabs">
              <button class="stab" :class="{ active: activeTab === 'ai' }" @click="activeTab = 'ai'">📝 AI 总结</button>
              <button class="stab" :class="{ active: activeTab === 'timeline' }" @click="activeTab = 'timeline'">⏱ 字幕时间线</button>
              <button class="stab" :class="{ active: activeTab === 'mindmap' }" @click="activeTab = 'mindmap'">🧠 思维导图</button>
              <span class="stab-right">
                <span v-if="basisLabel" class="basis-badge" :class="summaryTask?.basis" :title="summaryTask?.basis === 'subtitles' ? '基于视频字幕生成，内容较完整' : '该平台无可用字幕，基于标题简介生成，内容较简略'">{{ basisLabel }}</span>
                <button v-if="summaryData" class="link-btn copy-link" @click="copySummary">复制全文</button>
              </span>
            </div>

            <div class="summary-content">
              <!-- AI 总结 -->
              <template v-if="activeTab === 'ai'">
                <pre v-if="!summaryData" class="stream-box">{{ streamText || '正在准备素材…' }}</pre>
                <template v-else>
                  <p class="summary-line">{{ summaryData.overview }}</p>
                  <ul class="summary-points">
                    <li v-for="p in summaryData.points" :key="p">{{ p }}</li>
                  </ul>
                  <template v-if="summaryData.chapters?.length">
                    <h4 class="summary-sec">章节大纲 <small>点击时间戳直达对应片段</small></h4>
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
                  <div v-for="(t, i) in transcript" :key="i" class="chapter-row tl-row">
                    <a class="ts-chip" :href="chapterHref(t.time)" target="_blank" rel="noopener">{{ fmtTime(t.time) }}</a>
                    <p class="tl-text">{{ t.text }}</p>
                  </div>
                </div>
                <p v-else class="empty-tip">{{ summaryBusy ? '字幕加载中…' : '该视频没有可用的字幕时间线' }}</p>
              </template>

              <!-- 思维导图 -->
              <template v-if="activeTab === 'mindmap'">
                <MindMap v-if="summaryData?.mindmap" :data="summaryData.mindmap" />
                <p v-else class="empty-tip">{{ summaryBusy ? '思维导图生成中…' : '未能生成思维导图' }}</p>
              </template>
            </div>
          </div>

          <p class="fmt-label">选择清晰度</p>
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
            >仅音频</button>
          </div>

          <!-- 进度区 -->
          <div v-if="task" class="progress-area">
            <template v-if="task.status === 'downloading' || task.status === 'processing'">
              <div class="progress-head">
                <span>{{ task.status === 'processing' ? '正在合并音视频…' : '正在下载…' }}</span>
                <span>{{ task.progress }}%</span>
              </div>
              <div class="progress-bar"><div class="progress-fill" :style="{ width: task.progress + '%' }"></div></div>
              <p class="progress-meta" v-if="task.speed">速度 {{ fmtSpeed(task.speed) }}<template v-if="task.eta"> · 剩余 {{ task.eta }}s</template></p>
            </template>
            <template v-else-if="task.status === 'done'">
              <div class="done-row">
                <span class="done-badge">✓ 下载完成 {{ fmtSize(task.filesize) }}</span>
                <button class="btn" @click="saveFile">保存到本地</button>
              </div>
              <p class="progress-meta">文件保留 30 分钟，请尽快保存</p>
            </template>
            <template v-else-if="task.status === 'error'">
              <p class="error-tip">下载失败：{{ task.error }}</p>
            </template>
          </div>

          <button v-else class="btn btn-lg btn-block" @click="startDownload">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m0 0l-4-4m4 4l4-4M4 19h16"/></svg>
            下载视频
          </button>
        </div>
      </div>
    </div>
  </section>

  <!-- ===== 三步流程 ===== -->
  <section class="steps">
    <div class="container">
      <div class="steps-grid">
        <div class="step">
          <span class="step-num">1</span>
          <h4>粘贴链接</h4>
          <p>在任意平台复制视频分享链接</p>
        </div>
        <div class="step">
          <span class="step-num">2</span>
          <h4>选择清晰度</h4>
          <p>从 360p 到 4K，按需选择</p>
        </div>
        <div class="step">
          <span class="step-num">3</span>
          <h4>保存本地</h4>
          <p>秒级解析，高速下载到手机或电脑</p>
        </div>
      </div>
    </div>
  </section>

  <!-- ===== 功能卖点 ===== -->
  <section id="features" class="features">
    <div class="container">
      <h2 class="sec-title">为什么选择<span class="grad">极易下</span></h2>
      <p class="sec-sub">不用装软件、不用登录账号，打开网页就能下</p>
      <div class="feature-grid">
        <div class="feature-card">
          <div class="feature-icon" style="background:var(--primary-bg)">🌐</div>
          <h4>1000+ 平台支持</h4>
          <p>基于开源 yt-dlp 引擎，覆盖主流国内外视频平台，持续更新</p>
          <div class="tags"><span># 全平台</span><span># 持续更新</span></div>
        </div>
        <div class="feature-card">
          <div class="feature-icon" style="background:#eaf3de">⚡</div>
          <h4>高清原画下载</h4>
          <p>最高支持 4K 原画下载，不二压、不带水印，画质如一</p>
          <div class="tags"><span># 4K</span><span># 无水印</span></div>
        </div>
        <div class="feature-card">
          <div class="feature-icon" style="background:#faeeda">📱</div>
          <h4>手机直接用</h4>
          <p>无需安装 App，手机浏览器打开网页即可下载保存到相册</p>
          <div class="tags"><span># 免安装</span><span># 随时随地</span></div>
        </div>
        <div class="feature-card">
          <div class="feature-icon" style="background:#fbeaf0">🎵</div>
          <h4>一键提取音频</h4>
          <p>演讲、播客、课程想只留声音？一键提取为 m4a 音频文件</p>
          <div class="tags"><span># 音频提取</span><span># 播客收藏</span></div>
        </div>
        <div class="feature-card">
          <div class="feature-icon" style="background:#e1f5ee">🚀</div>
          <h4>会员极速通道</h4>
          <p>会员独享多线程加速与优先下载队列，大文件也飞速完成</p>
          <div class="tags"><span># 极速</span><span># 优先队列</span></div>
        </div>
        <div class="feature-card">
          <div class="feature-icon" style="background:#f1efe8">🔒</div>
          <h4>隐私安全</h4>
          <p>不注册、不登录、不收集个人信息，下载记录自动清理</p>
          <div class="tags"><span># 免登录</span><span># 自动清理</span></div>
        </div>
      </div>
    </div>
  </section>

  <!-- ===== 定价 ===== -->
  <section id="pricing" class="pricing">
    <div class="container">
      <h2 class="sec-title">选择适合你的<span class="grad">方案</span></h2>
      <p class="sec-sub">先免费用，觉得好用再升级会员</p>
      <div class="plan-grid">
        <div
          v-for="p in plans"
          :key="p.id"
          class="plan-card"
          :class="{ highlight: p.highlight }"
        >
          <span v-if="p.highlight" class="plan-badge">最受欢迎</span>
          <h4>{{ p.name }}</h4>
          <div class="plan-price">
            <template v-if="p.price > 0"><em>¥</em>{{ p.price }}<small>{{ p.period }}</small></template>
            <template v-else><em>¥</em>0<small>永久</small></template>
          </div>
          <ul class="plan-feats">
            <li v-for="f in p.features" :key="f">✓ {{ f }}</li>
          </ul>
          <button
            class="btn btn-block"
            :class="{ 'btn-outline': !p.highlight, 'btn-lg': p.highlight }"
            @click="p.price > 0 ? startPay(p) : null"
          >
            {{ p.price > 0 ? '立即开通' : '免费开始' }}
          </button>
        </div>
      </div>
    </div>
  </section>

  <!-- ===== FAQ ===== -->
  <section id="faq" class="faq">
    <div class="container faq-inner">
      <h2 class="sec-title">常见问题</h2>
      <details class="faq-item">
        <summary>支持哪些平台的视频下载？</summary>
        <p>基于 yt-dlp 引擎，支持 B站、抖音、YouTube、小红书、微博、快手、X、Instagram 等 1000+ 平台。粘贴链接解析一下就知道能不能下。</p>
      </details>
      <details class="faq-item">
        <summary>手机上怎么保存到相册？</summary>
        <p>用手机浏览器打开本站，下载完成后点击"保存到本地"，再到浏览器的下载管理中选择"保存到相册/文件"即可。</p>
      </details>
      <details class="faq-item">
        <summary>下载的视频有水印吗？</summary>
        <p>我们下载的是平台原始视频流，不带任何额外水印（平台本身水印以原视频为准）。</p>
      </details>
      <details class="faq-item">
        <summary>会员和免费用户的区别？</summary>
        <p>免费用户可下载标准清晰度；会员解锁全部清晰度（最高 4K）、仅音频提取与极速下载通道。</p>
      </details>
      <details class="faq-item">
        <summary>AI 视频总结是什么？</summary>
        <p>粘贴链接解析后，点击结果卡上的「AI 总结」按钮：B站、YouTube 等支持字幕的平台会基于字幕生成一句话概述、核心要点、章节大纲（可点击时间戳跳转）与思维导图；抖音等没有字幕的平台会基于标题和简介做简略总结，并在结果中如实标注总结依据。每天有免费次数，会员不限次。</p>
      </details>
      <details class="faq-item">
        <summary>下载的视频可以随便传播吗？</summary>
        <p>不可以。下载内容仅供个人学习与备份，请尊重版权，遵守各平台用户协议及相关法律法规。</p>
      </details>
    </div>
  </section>

  <!-- ===== 页脚 ===== -->
  <footer class="footer">
    <div class="container">
      <p class="footer-logo">极易下</p>
      <p class="footer-disclaimer">
        本工具基于开源项目 yt-dlp 构建，仅提供技术演示。所有视频内容版权归原平台及作者所有，
        请勿下载受版权保护的内容用于商业用途或传播。下载内容仅供个人学习与研究，使用本站产生的一切行为由用户自行承担。
      </p>
      <p class="footer-copy">© 2026 极易下 ·Powered by yt-dlp</p>
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
          <p class="qr-tip">请使用微信扫码支付<br />（演示环境为模拟收银台）</p>
        </div>
        <button class="btn btn-block btn-lg" @click="confirmMockPay">我已完成支付</button>
        <button class="link-btn" @click="closePay">取消支付</button>
      </template>
      <template v-else-if="payState === 'paid'">
        <div class="pay-success">
          <div class="success-icon">✓</div>
          <h3>支付成功</h3>
          <p>已开通 {{ selectedPlan?.name }}，感谢支持！<br />（演示环境，未真实扣款）</p>
        </div>
        <button class="btn btn-block btn-lg" @click="closePay">好的</button>
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
.btn:disabled { background: #a9c4f7; cursor: not-allowed; }
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
  background: linear-gradient(90deg, var(--primary), #6a9bff);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.sec-title { font-size: 30px; font-weight: 600; text-align: center; }
.sec-sub { text-align: center; color: var(--text-2); margin-top: 12px; font-size: 15px; }

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
  height: 60px;
  gap: 32px;
}
.logo {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 19px;
  font-weight: 600;
}
.logo-mark { display: flex; }
.nav-links { display: flex; gap: 28px; margin-left: auto; }
.nav-links a { color: var(--text-2); font-size: 15px; }
.nav-links a:hover { color: var(--primary); }

/* ===== Hero ===== */
.hero { padding: 84px 0 48px; text-align: center; }
.hero-title { font-size: 48px; font-weight: 700; letter-spacing: 1px; }
.hero-sub { color: var(--text-2); font-size: 17px; margin-top: 18px; }
.input-row {
  display: flex;
  gap: 12px;
  max-width: 720px;
  margin: 36px auto 0;
}
.url-input {
  flex: 1;
  height: 54px;
  border: 1.5px solid var(--border);
  border-radius: 12px;
  padding: 0 18px;
  font-size: 15px;
  outline: none;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.url-input:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px rgba(51, 112, 255, 0.12);
}
.error-tip { color: #d93126; margin-top: 14px; font-size: 14px; }
.hero-hint { color: var(--text-3); font-size: 13px; margin-top: 18px; }

/* ===== 解析结果 ===== */
.result { padding: 8px 0 40px; }
.result-card {
  display: flex;
  gap: 24px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 20px;
  box-shadow: var(--shadow);
}
.result-media {
  width: 300px;
  flex-shrink: 0;
  border-radius: 10px;
  overflow: hidden;
  background: var(--bg-page);
  align-self: flex-start;
}
.result-media img { width: 100%; display: block; }
.thumb-fallback {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 180px;
  color: var(--text-3);
  font-size: 14px;
}
.result-body { flex: 1; min-width: 0; }
.result-title-row { display: flex; align-items: flex-start; gap: 12px; }
.result-title { font-size: 19px; font-weight: 600; line-height: 1.4; flex: 1; }
.icon-btn {
  border: none;
  background: var(--bg-page);
  color: var(--text-3);
  width: 28px;
  height: 28px;
  border-radius: 8px;
  flex-shrink: 0;
}
.icon-btn:hover { color: var(--text-1); }
.result-meta {
  display: flex;
  align-items: center;
  gap: 14px;
  color: var(--text-2);
  font-size: 14px;
  margin-top: 10px;
  flex-wrap: wrap;
}
.platform-tag {
  background: var(--primary-bg);
  color: var(--primary);
  font-size: 12px;
  padding: 3px 10px;
  border-radius: 6px;
}
.fmt-label { color: var(--text-2); font-size: 14px; margin: 20px 0 10px; }
.fmt-list { display: flex; flex-wrap: wrap; gap: 10px; }
.fmt-chip {
  border: 1.5px solid var(--border);
  background: #fff;
  color: var(--text-1);
  border-radius: 10px;
  padding: 9px 16px;
  font-size: 14px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  transition: all 0.12s;
}
.fmt-chip small { color: var(--text-3); font-size: 11px; }
.fmt-chip:hover { border-color: var(--primary); }
.fmt-chip.active {
  border-color: var(--primary);
  background: var(--primary-bg);
  color: var(--primary);
}
.fmt-chip.active small { color: var(--primary); }
.progress-area { margin-top: 20px; }
.progress-head {
  display: flex;
  justify-content: space-between;
  font-size: 14px;
  color: var(--text-1);
  margin-bottom: 8px;
}
.progress-bar {
  height: 10px;
  background: var(--bg-page);
  border-radius: 6px;
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--primary), #6a9bff);
  border-radius: 6px;
  transition: width 0.4s;
}
.progress-meta { color: var(--text-3); font-size: 12.5px; margin-top: 8px; }
.done-row { display: flex; align-items: center; gap: 16px; }
.done-badge {
  color: #1d9e75;
  background: #e1f5ee;
  padding: 8px 14px;
  border-radius: 10px;
  font-size: 14px;
  font-weight: 500;
}

/* ===== 三步流程 ===== */
.steps { padding: 48px 0; background: var(--bg-page); }
.steps-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 24px; }
.step {
  background: #fff;
  border-radius: var(--radius);
  padding: 28px 24px;
  text-align: center;
  box-shadow: var(--shadow);
}
.step-num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: var(--primary);
  color: #fff;
  font-weight: 600;
  margin-bottom: 14px;
}
.step h4 { font-size: 17px; margin-bottom: 8px; }
.step p { color: var(--text-2); font-size: 14px; }

/* ===== 功能 ===== */
.features { padding: 72px 0; }
.feature-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 20px;
  margin-top: 44px;
}
.feature-card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 26px 24px;
  transition: transform 0.15s, box-shadow 0.15s;
}
.feature-card:hover { transform: translateY(-4px); box-shadow: var(--shadow-lift); }
.feature-icon {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  margin-bottom: 16px;
}
.feature-card h4 { font-size: 16.5px; margin-bottom: 8px; }
.feature-card p { color: var(--text-2); font-size: 14px; line-height: 1.65; }
.tags { display: flex; gap: 8px; margin-top: 14px; flex-wrap: wrap; }
.tags span { color: var(--text-3); font-size: 12px; }

/* ===== 定价 ===== */
.pricing { padding: 72px 0; background: var(--bg-page); }
.plan-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 20px;
  max-width: 960px;
  margin: 44px auto 0;
  align-items: stretch;
}
.plan-card {
  position: relative;
  background: #fff;
  border: 1.5px solid var(--border);
  border-radius: 16px;
  padding: 32px 26px;
  display: flex;
  flex-direction: column;
}
.plan-card.highlight {
  border-color: var(--primary);
  box-shadow: 0 10px 32px rgba(51, 112, 255, 0.14);
}
.plan-badge {
  position: absolute;
  top: -12px;
  left: 50%;
  transform: translateX(-50%);
  background: var(--primary);
  color: #fff;
  font-size: 12px;
  padding: 4px 14px;
  border-radius: 20px;
  white-space: nowrap;
}
.plan-card h4 { font-size: 17px; text-align: center; }
.plan-price { text-align: center; margin: 18px 0 22px; font-weight: 700; font-size: 40px; }
.plan-price em { font-style: normal; font-size: 20px; margin-right: 2px; }
.plan-price small { font-size: 14px; color: var(--text-3); font-weight: 400; margin-left: 4px; }
.plan-feats { list-style: none; flex: 1; margin-bottom: 24px; }
.plan-feats li {
  color: var(--text-2);
  font-size: 14px;
  padding: 7px 0;
  border-bottom: 1px dashed var(--border);
}
.plan-feats li:last-child { border-bottom: none; }

/* ===== FAQ ===== */
.faq { padding: 72px 0; }
.faq-inner { max-width: 760px; }
.faq .sec-title { margin-bottom: 36px; }
.faq-item {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 18px 22px;
  margin-bottom: 12px;
}
.faq-item summary {
  font-weight: 500;
  cursor: pointer;
  font-size: 15.5px;
  list-style: none;
}
.faq-item summary::after { content: '+'; float: right; color: var(--text-3); }
.faq-item[open] summary::after { content: '−'; }
.faq-item p { color: var(--text-2); font-size: 14px; line-height: 1.7; margin-top: 12px; }

/* ===== 页脚 ===== */
.footer { background: var(--bg-page); border-top: 1px solid var(--border); padding: 40px 0 48px; text-align: center; }
.footer-logo { font-weight: 600; font-size: 17px; }
.footer-disclaimer {
  color: var(--text-3);
  font-size: 12.5px;
  line-height: 1.8;
  max-width: 640px;
  margin: 14px auto;
}
.footer-copy { color: var(--text-3); font-size: 12.5px; }

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

/* ===== AI 视频总结（页内面板 + 选项卡） ===== */
.ai-row { display: flex; align-items: center; gap: 12px; margin-top: 20px; flex-wrap: wrap; }
.ai-btn { min-width: 150px; }
.spinner {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255, 255, 255, 0.4);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.quota-tip { color: var(--text-3); font-size: 12.5px; }
.cookie-toggle {
  border: 1px solid var(--border);
  background: #fff;
  color: var(--text-2);
  font-size: 12.5px;
  padding: 6px 12px;
  border-radius: 8px;
  margin-left: auto;
}
.cookie-toggle:hover { color: var(--primary); border-color: var(--primary); }
.cookie-cfg {
  margin-top: 12px;
  border: 1px dashed var(--border);
  border-radius: 10px;
  padding: 12px 14px;
}
.cookie-tip { color: var(--text-2); font-size: 12.5px; line-height: 1.7; }
.cookie-row { display: flex; gap: 8px; margin-top: 8px; }
.cookie-input {
  flex: 1;
  height: 36px;
  border: 1.5px solid var(--border);
  border-radius: 8px;
  padding: 0 12px;
  font-size: 13px;
  outline: none;
}
.cookie-input:focus { border-color: var(--primary); }

.summary-panel {
  margin-top: 16px;
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
}
.summary-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 8px 10px;
  background: var(--bg-page);
  border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
}
.stab {
  border: none;
  background: transparent;
  color: var(--text-2);
  font-size: 13.5px;
  padding: 7px 12px;
  border-radius: 8px;
  transition: all 0.12s;
}
.stab:hover { color: var(--text-1); background: #fff; }
.stab.active { color: var(--primary); background: #fff; font-weight: 600; box-shadow: 0 1px 4px rgba(31,35,41,0.08); }
.stab-right { margin-left: auto; display: flex; align-items: center; gap: 10px; }
.copy-link { font-size: 13px; margin-top: 0; }
.summary-content { padding: 16px 18px 18px; max-height: 480px; overflow-y: auto; }

.stream-box {
  margin: 0;
  font-family: inherit;
  font-size: 14px;
  line-height: 1.8;
  color: var(--text-1);
  white-space: pre-wrap;
  word-break: break-word;
}
.stream-box::after { content: '▌'; color: var(--primary); animation: blink 1s step-start infinite; }
@keyframes blink { 50% { opacity: 0; } }

.basis-badge {
  font-size: 11.5px;
  padding: 3px 10px;
  border-radius: 20px;
  white-space: nowrap;
  cursor: help;
}
.basis-badge.subtitles { background: #e1f5ee; color: #1d9e75; }
.basis-badge.meta { background: #faeeda; color: #c47b1a; }

.summary-line {
  font-size: 15px;
  font-weight: 500;
  line-height: 1.7;
  padding: 12px 14px;
  background: var(--primary-bg);
  border-radius: 10px;
}
.summary-points { margin: 14px 0 0; padding-left: 0; list-style: none; }
.summary-points li {
  position: relative;
  padding: 5px 0 5px 20px;
  color: var(--text-1);
  font-size: 14px;
  line-height: 1.6;
}
.summary-points li::before {
  content: '';
  position: absolute;
  left: 4px;
  top: 13px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--primary);
}
.summary-sec { font-size: 15px; margin: 22px 0 10px; }
.summary-sec small { color: var(--text-3); font-weight: 400; font-size: 12px; margin-left: 6px; }
.chapter-row { display: flex; gap: 12px; padding: 8px 0; align-items: flex-start; }
.ts-chip {
  flex-shrink: 0;
  background: var(--primary);
  color: #fff;
  font-size: 12.5px;
  padding: 4px 9px;
  border-radius: 8px;
  text-decoration: none;
  margin-top: 1px;
  min-width: 42px;
  text-align: center;
}
.ts-chip:hover { background: var(--primary-dark); }
.chapter-text b { font-size: 14px; }
.chapter-text p, .tl-text { color: var(--text-2); font-size: 13px; margin-top: 2px; line-height: 1.5; }
.transcript-list { display: block; }
.empty-tip { color: var(--text-3); font-size: 13.5px; text-align: center; padding: 24px 0; }

/* ===== 响应式 ===== */
@media (max-width: 900px) {
  .hero-title { font-size: 34px; }
  .feature-grid { grid-template-columns: repeat(2, 1fr); }
  .plan-grid { grid-template-columns: 1fr; max-width: 420px; }
  .plan-card.highlight { order: -1; }
  .steps-grid { grid-template-columns: 1fr; }
  .result-card { flex-direction: column; }
  .result-media { width: 100%; }
}
@media (max-width: 600px) {
  .nav-links { display: none; }
  .hero { padding: 56px 0 36px; }
  .hero-title { font-size: 28px; }
  .hero-sub { font-size: 15px; }
  .input-row { flex-direction: column; }
  .btn-lg { width: 100%; }
  .feature-grid { grid-template-columns: 1fr; }
  .done-row { flex-direction: column; align-items: stretch; text-align: center; }
  .summary-content { max-height: 60vh; padding: 14px; }
  .stab { font-size: 12.5px; padding: 6px 8px; }
  .ai-row .ai-btn { flex: 1; }
}
</style>
