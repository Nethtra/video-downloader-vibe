<script setup>
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import MindMap from './components/MindMap.vue'
import { t, tl, locale, setLocale, LANGS } from './i18n'
import { theme, setTheme } from './theme'
import { approx } from './currency'

const THEMES = ['auto', 'light', 'dark']

/* ---------------- 滚动进度（导航底部红条） ---------------- */
const scrollPct = ref(0)
let scrollRaf = false
function onScroll() {
  if (scrollRaf) return
  scrollRaf = true
  requestAnimationFrame(() => {
    const doc = document.documentElement
    const max = doc.scrollHeight - window.innerHeight
    scrollPct.value = max > 0 ? Math.min(100, (doc.scrollTop || 0) / max * 100) : 0
    scrollRaf = false
  })
}

/* 平台跑马灯：品牌名不翻译；渲染两遍实现无缝循环 */
const marqueePlatforms = [
  '哔哩哔哩', '抖音', 'YouTube', '小红书', '微博', '快手',
  'X (Twitter)', 'Instagram', 'TikTok', 'Vimeo',
]

/* ---------------- 登录 / 会员状态 ---------------- */
const user = ref(null)            // {email, vip_until, is_vip} | null
const authModal = ref(false)
const authMode = ref('login')     // login | register
const authForm = ref({ email: '', password: '' })
const authError = ref('')
const authBusy = ref(false)
// 记录"登录前想做的事"：登录成功后自动继续（如完成购买）
let pendingAction = null

async function refreshUser() {
  try {
    const r = await fetch('/api/auth/me')
    if (r.ok) user.value = (await r.json()).user
  } catch { /* 忽略 */ }
}

function openAuth(mode, intent) {
  authMode.value = mode
  authError.value = ''
  authForm.value = { email: user.value?.email || '', password: '' }
  pendingAction = intent || null
  authModal.value = true
}

async function submitAuth() {
  if (authBusy.value) return
  authBusy.value = true
  authError.value = ''
  try {
    const res = await fetch(`/api/auth/${authMode.value}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(authForm.value),
    })
    const data = await readJson(res)
    if (!res.ok) {
      const detail = data.detail || ''
      authError.value = detail.includes('已注册') ? t('auth.emailTaken')
        : detail.includes('密码') ? detail
        : detail.includes('错误') ? t('auth.badCredentials') : detail
      return
    }
    authModal.value = false
    await refreshUser()
    const act = pendingAction
    pendingAction = null
    if (act) act()   // 登录成功，继续之前被拦下的动作（如购买）
  } catch (e) {
    authError.value = e.message
  } finally {
    authBusy.value = false
  }
}

async function logout() {
  try { await fetch('/api/auth/logout', { method: 'POST' }) } catch { /* 忽略 */ }
  user.value = null
  await loadQuota()
}

const isVip = computed(() => !!user.value?.is_vip)
const vipUntilText = computed(() => {
  if (!user.value?.vip_until) return ''
  const d = new Date(user.value.vip_until)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
})
/* 会员剩余天数（向上取整，最少显示 1 天），导航栏直观展示 */
const vipDaysLeft = computed(() => {
  if (!user.value?.vip_until) return 0
  const diff = new Date(user.value.vip_until).getTime() - Date.now()
  return Math.max(1, Math.ceil(diff / 86400000))
})

refreshUser()


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

/* ---------------- 会员购买（Stripe Checkout / Mock 回退） ---------------- */
const plans = ref([])
const payModal = ref(false)
const payOrder = ref(null)
const payState = ref('qr') // redirecting | qr | paying | confirming | paid
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
    body: JSON.stringify({ plan_id: plan.id, locale: locale.value }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || t('err.order'))
  return data
}

async function startPay(plan) {
  if (!user.value) {
    // 未登录先登录，成功后自动继续本次购买
    openAuth('login', () => startPay(plan))
    return
  }
  try {
    selectedPlan.value = plan
    payOrder.value = null
    payState.value = 'redirecting'
    payModal.value = true
    const order = await createOrder(plan)
    if (order.pay?.pay_type === 'stripe_redirect') {
      // 跳 Stripe 托管支付页；支付完成后重定向回 ?pay=success&order=xxx
      window.location.href = order.pay.pay_url
      return
    }
    // mock 渠道（后端未配 STRIPE_API_KEY）：模拟收银台
    payOrder.value = order
    payState.value = 'paying'
    pollPaid(order.order_id)
  } catch (e) {
    payModal.value = false
    errorMsg.value = e.message
  }
}

function pollPaid(orderId) {
  // 轮询订单状态；后端在 paying 时会主动向 Stripe 同步（webhook 未达的兜底）
  payPoll = setInterval(async () => {
    try {
      const r = await fetch(`/api/pay/order/${orderId}`)
      if (!r.ok) return
      const d = await r.json()
      if (d.status === 'paid') {
        payState.value = 'paid'
        clearInterval(payPoll)
        await refreshUser()
        await loadQuota()
      }
    } catch { /* 网络抖动，下一轮重试 */ }
  }, 1500)
}

/* Stripe 支付完成回跳：/?pay=success&order=xxx -> 弹「确认中」并轮询到 paid */
function handlePayRedirect() {
  const q = new URLSearchParams(location.search)
  if (q.get('pay') === 'success' && q.get('order')) {
    const orderId = q.get('order')
    history.replaceState({}, '', location.pathname + location.hash) // 清参数，防刷新重复弹
    selectedPlan.value = null
    payOrder.value = { order_id: orderId }
    payState.value = 'confirming'
    payModal.value = true
    pollPaid(orderId)
  }
}
handlePayRedirect()

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

/* 清晰度选择：4K 为会员专享（服务端同样强制），非会员点击给出提示 */
function selectFormat(f) {
  if (f.height >= 2160 && !isVip.value) {
    errorMsg.value = t('fmt.vipLock')
    return
  }
  selectedFormat.value = f.format_id
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
    if (pollElapsed > 600) {  // 长视频分块摘要可达数分钟，兜底轮询同步放宽
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

onMounted(() => {
  window.addEventListener('scroll', onScroll, { passive: true })
  onScroll()
})

onBeforeUnmount(() => {
  window.removeEventListener('scroll', onScroll)
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
          <svg viewBox="0 0 24 24" width="22" height="22" fill="none">
            <rect x="0" y="0" width="24" height="24" fill="var(--ink)" />
            <path d="M9 6.5v11l9-5.5-9-5.5z" fill="var(--red)" />
          </svg>
        </span>
        极易下
      </a>
      <nav class="nav-links">
        <a href="#showcase">{{ t('nav.ai') }}</a>
        <a href="#features">{{ t('nav.features') }}</a>
        <a href="#pricing">{{ t('nav.pricing') }}</a>
        <a href="#faq">{{ t('nav.faq') }}</a>
      </nav>
      <select class="lang-select" v-model="locale" :aria-label="t('nav.lang')" @change="setLocale(locale)">
        <option v-for="l in LANGS" :key="l.value" :value="l.value">{{ l.label }}</option>
      </select>
      <select class="lang-select theme-select" :value="theme" :aria-label="t('nav.theme')" @change="setTheme($event.target.value)">
        <option v-for="th in THEMES" :key="th" :value="th">{{ t('theme.' + th) }}</option>
      </select>
      <template v-if="user">
        <span class="user-chip" :class="{ vip: isVip }"
              :title="isVip ? t('nav.vipUntil', { d: vipUntilText }) : user.email">
          <span class="user-name">{{ user.email.split('@')[0] }}</span>
          <b v-if="isVip" class="vip-badge">
            <svg viewBox="0 0 24 24" width="9" height="9" fill="currentColor" aria-hidden="true"><path d="M13 2L3 14h7l-1 8 10-12h-7l1-8z"/></svg>
            {{ t('nav.vip') }}
          </b>
          <span v-if="isVip" class="vip-days">{{ t('nav.vipDays', { d: vipDaysLeft }) }}</span>
        </span>
        <button class="link-btn" @click="logout">{{ t('nav.logout') }}</button>
      </template>
      <button v-else class="btn btn-sm btn-outline" @click="openAuth('login')">{{ t('nav.login') }}</button>
      <a class="btn btn-sm btn-orange" href="#hero">{{ t('nav.start') }}</a>
    </div>
    <span class="nav-progress" :style="{ width: scrollPct + '%' }" aria-hidden="true"></span>
  </header>

  <!-- ===== Hero：12 栏网格 / 左对齐 / 标题极大 / 右侧数据栏 ===== -->
  <section id="hero" class="hero">
    <div class="container hero-grid">
      <div class="hero-main">
        <p class="eyebrow"><b>01</b> YIJIXIA / VIDEO DOWNLOADER &amp; AI SUMMARY</p>
        <h1 class="hero-title">{{ t('hero.t1') }}<span class="hero-grad">{{ t('hero.t2') }}</span></h1>
        <p class="hero-sub">{{ t('hero.sub') }}</p>
        <div class="input-card">
          <div class="input-row">
            <input
              v-model="url"
              class="url-input"
              type="text"
              :placeholder="t('hero.ph')"
              @keyup.enter="analyze"
            />
            <button class="btn" :disabled="!canSubmit" @click="analyze">
              {{ analyzing ? t('hero.analyzing') : t('hero.analyze') }}
            </button>
          </div>
        </div>
        <p v-if="errorMsg" class="error-tip hero-error">{{ errorMsg }}</p>
        <p class="hero-hint">{{ t('hero.hint') }}</p>
      </div>

      <aside class="hero-aside">
        <p class="aside-label">DATA / 2026</p>
        <div class="hero-stats">
          <div class="stat" v-for="i in 4" :key="i">
            <b>{{ t('stats.v' + i) }}</b>
            <span>{{ t('stats.p' + i) }}</span>
          </div>
        </div>
      </aside>
    </div>
  </section>

  <!-- ===== 平台支持：无缝跑马灯 ===== -->
  <section class="brandbar" aria-label="Supported platforms">
    <p class="brandbar-label">PLATFORMS / 1000+</p>
    <div class="marquee">
      <div class="marquee-track">
        <span class="marquee-item" v-for="p in marqueePlatforms" :key="p">{{ p }}</span>
        <span class="marquee-item" v-for="p in marqueePlatforms" :key="'dup-' + p" aria-hidden="true">{{ p }}</span>
      </div>
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
              :class="{ active: selectedFormat === f.format_id, vip: f.height >= 2160 && !isVip }"
              @click="selectFormat(f)"
            >
              {{ f.label }}
              <svg v-if="f.height >= 2160 && !isVip" class="vip-lock" viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><rect x="4" y="11" width="16" height="9" rx="2"/><path d="M8 11V7a4 4 0 018 0v4"/></svg>
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
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m0 0l-4-4m4 4l4-4M4 19h16"/></svg>
            {{ t('r.download') }}
          </button>
        </aside>

        <!-- 右栏：AI 总结工作台 -->
        <section class="result-main">
          <div class="ai-row">
            <button class="btn btn-outline ai-btn" :disabled="summaryBusy" @click="startSummary">
              <span v-if="summaryBusy" class="spinner"></span>{{ summaryPhaseText }}
            </button>
            <span v-if="quota && quota.vip" class="quota-tip vip">{{ t('ai.quotaVip') }}</span>
            <span v-else-if="quota" class="quota-tip">{{ t('ai.quota', { r: quota.remaining, l: quota.limit }) }}</span>
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

  <!-- ===== 三步流程（02 / 编号 + 细线分栏） ===== -->
  <section class="steps">
    <div class="container">
      <div class="steps-grid">
        <div class="step" v-for="i in 3" :key="i" v-reveal="(i - 1) * 80">
          <span class="step-num">{{ String(i).padStart(2, '0') }}</span>
          <h4>{{ t('steps.t' + i) }}</h4>
          <p>{{ t('steps.d' + i) }}</p>
        </div>
      </div>
    </div>
  </section>

  <!-- ===== AI 总结效果展示 ===== -->
  <section id="showcase" class="showcase">
    <div class="container">
      <div class="sec-head" v-reveal>
        <span class="sec-num">03</span>
        <div>
          <h2 class="sec-title">{{ t('sc.t1') }}<span class="grad"> {{ t('sc.t2') }}</span></h2>
          <p class="sec-sub">{{ t('sc.sub') }}</p>
        </div>
        <span class="sec-meta">AI SUMMARY / OUTPUT</span>
      </div>
      <div class="showcase-grid">
        <div class="sc-features">
          <div class="sc-f" v-for="i in 4" :key="i" v-reveal="(i - 1) * 60">
            <span class="sc-f-icon">{{ String(i).padStart(2, '0') }}</span>
            <div>
              <h4>{{ t('sc.f' + i + 't') }}</h4>
              <p>{{ t('sc.f' + i + 'd') }}</p>
            </div>
          </div>
        </div>
        <div class="demo-card" v-reveal="140">
          <div class="demo-head">
            <span class="demo-thumb">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M8 5.5v13l11-6.5-11-6.5z"/></svg>
            </span>
            <div class="demo-head-txt">
              <span class="demo-tag">{{ t('sc.demo.tag') }}</span>
              <b>{{ t('sc.demo.title') }}</b>
            </div>
          </div>
          <p class="demo-ov">{{ t('sc.demo.ov') }}</p>
          <ul class="demo-points">
            <li>{{ t('sc.demo.p1') }}</li>
            <li>{{ t('sc.demo.p2') }}</li>
            <li>{{ t('sc.demo.p3') }}</li>
          </ul>
          <div class="demo-chapters">
            <span class="demo-ch"><i>00:00</i>{{ t('sc.demo.c1') }}</span>
            <span class="demo-ch"><i>08:30</i>{{ t('sc.demo.c2') }}</span>
            <span class="demo-ch"><i>21:45</i>{{ t('sc.demo.c3') }}</span>
          </div>
          <div class="demo-chips">
            <span class="demo-chip">
              <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="5" cy="12" r="2"/><circle cx="19" cy="5.5" r="2"/><circle cx="19" cy="18.5" r="2"/><path d="M7 12h10M17 7.2L9.5 10.7M17 16.8L9.5 13.3"/></svg>
              {{ t('sc.demo.map') }}
            </span>
            <span class="demo-chip">
              <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 15h5M14 15h3"/></svg>
              {{ t('sc.demo.srt') }}
            </span>
          </div>
        </div>
      </div>
    </div>
  </section>

  <!-- ===== 大字陈述 ===== -->
  <section class="statement">
    <div class="container statement-inner" v-reveal>
      <div class="statement-main">
        <p class="statement-label">WHY / 2026</p>
        <p class="statement-line">{{ t('stmt.lines') }}</p>
      </div>
      <p class="statement-sub">{{ t('stmt.sub') }}</p>
    </div>
  </section>

  <!-- ===== 功能卖点 ===== -->
  <section id="features" class="features">
    <div class="container">
      <div class="sec-head" v-reveal>
        <span class="sec-num">04</span>
        <div>
          <h2 class="sec-title">{{ t('features.t1') }}<span class="grad">{{ t('features.t2') }}</span></h2>
          <p class="sec-sub">{{ t('features.sub') }}</p>
        </div>
        <span class="sec-meta">FEATURES / 06</span>
      </div>
      <div class="feature-grid">
        <article class="feature-card" v-for="i in 6" :key="i" v-reveal="((i - 1) % 3) * 70">
          <span class="feature-num">{{ String(i).padStart(2, '0') }}</span>
          <h4>{{ t('features.f' + i + 't') }}</h4>
          <p>{{ t('features.f' + i + 'd') }}</p>
          <div class="tags">
            <span>{{ t('features.f' + i + 'a') }}</span>
            <span>{{ t('features.f' + i + 'b') }}</span>
          </div>
        </article>
      </div>
    </div>
  </section>

  <!-- ===== 三种方式对比（05） ===== -->
  <section id="compare" class="compare">
    <div class="container">
      <div class="sec-head" v-reveal>
        <span class="sec-num">05</span>
        <div>
          <h2 class="sec-title">{{ t('cmp.title') }}</h2>
          <p class="sec-sub">{{ t('cmp.sub') }}</p>
        </div>
        <span class="sec-meta">COMPARISON / 03</span>
      </div>
      <table class="cmp-table" v-reveal="80">
        <thead>
          <tr>
            <th scope="col" class="cmp-corner"></th>
            <th scope="col" class="cmp-us">{{ t('cmp.h1') }}</th>
            <th scope="col">{{ t('cmp.h2') }}</th>
            <th scope="col">{{ t('cmp.h3') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="i in 4" :key="i">
            <th scope="row">{{ t('cmp.r' + i) }}</th>
            <td class="cmp-us" :class="{ on: i <= 3 }">{{ i <= 3 ? '✓' : '—' }}</td>
            <td :class="{ on: i === 4 }">{{ i === 4 ? '✓' : '—' }}</td>
            <td :class="{ on: i === 4 }">{{ i === 4 ? '✓' : '—' }}</td>
          </tr>
        </tbody>
      </table>
      <p class="cmp-legend">{{ t('cmp.legend') }}</p>
    </div>
  </section>

  <!-- ===== 使用场景 ===== -->
  <section id="scene" class="scene">
    <div class="container">
      <div class="sec-head" v-reveal>
        <span class="sec-num">06</span>
        <div>
          <h2 class="sec-title">{{ t('scene.t1') }}<span class="grad"> {{ t('scene.t2') }}</span></h2>
          <p class="sec-sub">{{ t('scene.sub') }}</p>
        </div>
        <span class="sec-meta">USE CASES / 04</span>
      </div>
      <div class="scene-grid">
        <div class="scene-card" v-for="i in 4" :key="i" v-reveal="(i - 1) * 60">
          <span class="scene-num">{{ String(i).padStart(2, '0') }}</span>
          <h4>{{ t('scene.c' + i + 't') }}</h4>
          <p>{{ t('scene.c' + i + 'd') }}</p>
        </div>
      </div>
    </div>
  </section>

  <!-- ===== 定价 ===== -->
  <section id="pricing" class="pricing">
    <div class="container">
      <div class="sec-head" v-reveal>
        <span class="sec-num">07</span>
        <div>
          <h2 class="sec-title">{{ t('pricing.t1') }}<span class="grad">{{ t('pricing.t2') }}</span></h2>
          <p class="sec-sub">{{ t('pricing.sub') }}</p>
        </div>
        <span class="sec-meta">PRICING / CNY</span>
      </div>
      <div class="plan-grid">
        <div
          v-for="(p, i) in plansView"
          :key="p.id"
          v-reveal="i * 80"
          class="plan-card"
          :class="{ highlight: p.highlight }"
        >
          <span v-if="p.highlight" class="plan-badge">{{ t('pricing.popular') }}</span>
          <h4>{{ p.name }}</h4>
          <div class="plan-price">
            <template v-if="p.price > 0"><em>¥</em>{{ p.price }}<small>{{ p.period }}</small></template>
            <template v-else><em>¥</em>0<small>{{ t('plans.free.p') }}</small></template>
            <small v-if="approx(p.price, locale)" class="price-approx">{{ approx(p.price, locale) }}</small>
          </div>
          <ul class="plan-feats">
            <li v-for="f in p.features" :key="f">{{ f }}</li>
          </ul>
          <button
            class="btn btn-block"
            :class="{ 'btn-outline': !p.highlight, 'btn-lg': p.highlight }"
            @click="p.price > 0 ? startPay(p) : null"
          >
            {{ p.price > 0 ? (isVip ? t('pricing.renew') : t('pricing.buy')) : t('pricing.freeBtn') }}
          </button>
        </div>
      </div>
    </div>
  </section>

  <!-- ===== FAQ ===== -->
  <section id="faq" class="faq">
    <div class="container faq-inner" v-reveal>
      <div class="sec-head" v-reveal>
        <span class="sec-num">08</span>
        <div>
          <h2 class="sec-title">{{ t('faq.title') }}</h2>
        </div>
        <span class="sec-meta">FAQ / 08</span>
      </div>
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
      <details class="faq-item">
        <summary>{{ t('faq.q7') }}</summary>
        <p>{{ t('faq.a7') }}</p>
      </details>
    </div>
  </section>

  <!-- ===== 底部 CTA（反白块 / 大红按钮） ===== -->
  <section class="cta-band">
    <div class="container cta-inner" v-reveal>
      <div>
        <p class="cta-eyebrow">09 — START</p>
        <h2>{{ t('cta.t') }}</h2>
        <p>{{ t('cta.sub') }}</p>
      </div>
      <a class="btn btn-lg btn-orange" href="#hero">{{ t('cta.btn') }}<span class="btn-arrow" aria-hidden="true">→</span></a>
    </div>
  </section>

  <!-- ===== 页脚（三栏 / 细线 / 小号元信息） ===== -->
  <footer class="footer">
    <div class="container footer-grid" v-reveal>
      <div>
        <p class="footer-logo">极易下</p>
        <p class="footer-meta">YIJIXIA — VIDEO DOWNLOADER<br />CN / 2026</p>
      </div>
      <p class="footer-disclaimer">{{ t('footer.disclaimer') }}</p>
      <p class="footer-copy">{{ t('footer.copy') }}<br />12-COLUMN GRID / SWISS STYLE</p>
    </div>
  </footer>

  <!-- ===== 登录 / 注册弹窗 ===== -->
  <div v-if="authModal" class="modal-mask" @click.self="authModal = false">
    <div class="modal auth-modal">
      <h3 class="modal-title">{{ authMode === 'login' ? t('auth.loginTitle') : t('auth.registerTitle') }}</h3>
      <form class="auth-form" @submit.prevent="submitAuth">
        <label class="auth-field">
          <span>{{ t('auth.email') }}</span>
          <input v-model="authForm.email" type="email" required autocomplete="email" />
        </label>
        <label class="auth-field">
          <span>{{ t('auth.password') }}</span>
          <input v-model="authForm.password" type="password" minlength="8" required
                 :autocomplete="authMode === 'login' ? 'current-password' : 'new-password'" />
        </label>
        <p v-if="authError" class="error-tip">{{ authError }}</p>
        <button class="btn btn-block btn-lg" type="submit" :disabled="authBusy">
          {{ authMode === 'login' ? t('auth.submitLogin') : t('auth.submitRegister') }}
        </button>
      </form>
      <button class="link-btn" @click="authMode = authMode === 'login' ? 'register' : 'login'; authError = ''">
        {{ authMode === 'login' ? t('auth.toRegister') : t('auth.toLogin') }}
      </button>
    </div>
  </div>

  <!-- ===== 支付弹窗（Stripe 跳转 / 模拟收银台 / 结果确认） ===== -->
  <div v-if="payModal" class="modal-mask" @click.self="closePay">
    <div class="modal">
      <template v-if="payState === 'redirecting'">
        <h3 class="modal-title">{{ selectedPlan?.name }}</h3>
        <div class="pay-success">
          <span class="spinner"></span>
          <p>{{ t('pay.redirecting') }}</p>
        </div>
      </template>
      <template v-else-if="payState === 'confirming'">
        <div class="pay-success">
          <span class="spinner"></span>
          <h3>{{ t('pay.confirming') }}</h3>
        </div>
        <button class="link-btn" @click="closePay">{{ t('pay.cancel') }}</button>
      </template>
      <template v-else-if="payState === 'paying'">
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
          <p>
            <template v-if="selectedPlan">{{ t('pay.successDesc', { n: selectedPlan.name }) }}<br /></template>
            <template v-if="vipUntilText">{{ t('nav.vipUntil', { d: vipUntilText }) }}<br /></template>
            <template v-if="payOrder?.pay?.pay_type === 'mock_qr'">{{ t('pay.mockNote') }}</template>
          </p>
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
/* ==========================================================================
   瑞士风格 · 组件层
   版面：12 栏网格 / 细线分隔 / 左对齐右不齐 / 大片留白
   字重：只用 400 与 700；标题极大、正文极小
   平面：无圆角、无阴影、无渐变、无装饰插画
   ========================================================================== */

/* ---------- 通用 ---------- */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: var(--ink);
  color: var(--paper);
  border: 1px solid var(--ink);
  padding: 12px 22px;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.04em;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
}
.btn:hover { background: var(--red); border-color: var(--red); color: #fff; }
.btn:active { transform: translateY(1px); }
.btn:disabled { background: var(--btn-disabled); border-color: var(--btn-disabled); color: var(--paper); cursor: not-allowed; }
.btn-lg { padding: 15px 28px; font-size: 14px; }
.btn-sm { padding: 8px 14px; font-size: 11.5px; }
.btn-block { width: 100%; }
.btn-outline { background: transparent; color: var(--ink); border-color: var(--ink); }
.btn-outline:hover { background: var(--ink); color: var(--paper); border-color: var(--ink); }
.btn-orange { background: var(--red); border-color: var(--red); color: #fff; }
.btn-orange:hover { background: var(--red-dark); border-color: var(--red-dark); }

/* 旧的渐变文字类改为纯红——瑞士风格不允许渐变 */
.grad { color: var(--red); }

/* ---------- 区块标题（编号 + 大标题 + 元信息，三段对齐网格） ---------- */
.sec-head {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr) auto;
  gap: 0 24px;
  align-items: start;
  border-top: 1px solid var(--rule);
  padding-top: 16px;
  margin-bottom: 48px;
}
.sec-num {
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.12em;
  color: var(--red);
  padding-top: 8px;
}
.sec-title {
  font-size: clamp(30px, 3.6vw, 52px);
  font-weight: 700;
  letter-spacing: -0.035em;
  line-height: 1.02;
}
.sec-sub {
  font-size: 13px;
  line-height: 1.7;
  color: var(--ink-2);
  margin-top: 14px;
  max-width: 56ch;
}
.sec-meta {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.1em;
  color: var(--ink-3);
  text-align: right;
  padding-top: 8px;
  white-space: nowrap;
}

/* ---------- 导航 ---------- */
.nav {
  position: sticky;
  top: 0;
  z-index: 50;
  background: var(--paper);
  border-bottom: 1px solid var(--rule);
  border-top: 5px solid var(--red);
}
.nav-inner {
  display: flex;
  align-items: center;
  height: 58px;
  gap: 22px;
}
.logo {
  display: flex;
  align-items: center;
  gap: 9px;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: -0.02em;
  flex-shrink: 0;
}
.logo-mark { display: flex; }
.logo-mark svg { display: block; }
.nav-links {
  display: flex;
  gap: 22px;
  margin-left: auto;
}
.nav-links a {
  font-size: 12.5px;
  letter-spacing: 0.03em;
  color: var(--ink-2);
  padding-bottom: 2px;
  border-bottom: 1px solid transparent;
}
.nav-links a:hover { color: var(--ink); border-bottom-color: var(--red); }

.lang-select {
  height: 28px;
  border: 1px solid var(--rule-soft);
  background: transparent;
  color: var(--ink-2);
  font-size: 11px;
  font-family: var(--font-mono);
  letter-spacing: 0.02em;
  padding: 0 6px;
  cursor: pointer;
  outline: none;
  appearance: none;
}
.lang-select:hover { border-color: var(--ink); color: var(--ink); }
.lang-select:focus { border-color: var(--red); }

.user-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 210px;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ink-2);
  border: 1px solid var(--rule-soft);
  padding: 3px 8px;
}
.user-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.vip-badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.08em;
  color: #fff;
  background: var(--red);
  padding: 1px 5px;
  flex: none;
}
.user-chip.vip { border-color: var(--red); }
.vip-days { font-family: var(--font-mono); font-size: 10px; color: var(--red); white-space: nowrap; }

/* ---------- Hero ---------- */
.hero {
  border-bottom: 1px solid var(--rule);
  padding: 76px 0 64px;
}
.hero-grid {
  display: grid;
  grid-template-columns: repeat(12, 1fr);
  gap: 0 24px;
  align-items: start;
}
.hero-main { grid-column: 1 / span 8; }
.hero-aside {
  grid-column: 10 / span 3;
  border-left: 1px solid var(--rule);
  padding-left: 20px;
}
.eyebrow {
  display: flex;
  align-items: baseline;
  gap: 12px;
  font-family: var(--font-mono);
  font-size: 10.5px;
  letter-spacing: 0.12em;
  color: var(--ink-3);
  margin-bottom: 28px;
}
.eyebrow b { color: var(--red); font-weight: 400; }
.hero-title {
  font-size: clamp(44px, 7.4vw, 96px);
  line-height: 0.94;
  letter-spacing: -0.045em;
  font-weight: 700;
}
.hero-title .hero-grad { display: block; color: var(--red); }
.showcase .sec-title .grad { margin-left: 0.3em; }
.hero-sub {
  margin-top: 28px;
  font-size: 13.5px;
  line-height: 1.75;
  color: var(--ink-2);
  max-width: 46ch;
}
.input-card {
  margin-top: 36px;
  border: 1px solid var(--rule);
  background: transparent;
  padding: 0;
}
.input-row {
  display: flex;
  gap: 0;
  max-width: none;
  margin: 0;
  align-items: stretch;
}
.url-input {
  flex: 1;
  height: 56px;
  border: none;
  border-right: 1px solid var(--rule);
  background: transparent;
  padding: 0 16px;
  font-size: 13.5px;
  color: var(--ink);
  outline: none;
}
.url-input::placeholder { color: var(--ink-3); }
.url-input:focus { outline: 2px solid var(--red); outline-offset: -2px; }
.input-row .btn {
  height: 56px;
  border: none;
  background: var(--red);
  color: #fff;
  padding: 0 26px;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.04em;
  flex-shrink: 0;
}
.input-row .btn:hover { background: var(--red-dark); }
.input-row .btn:disabled { background: var(--red); opacity: 0.38; color: #fff; cursor: not-allowed; }
.hero-hint {
  font-family: var(--font-mono);
  font-size: 10px;
  line-height: 1.8;
  letter-spacing: 0.02em;
  color: var(--ink-3);
  margin-top: 12px;
}
.hero-error {
  color: var(--red);
  font-size: 12.5px;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--rule-soft);
}

.aside-label {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.12em;
  color: var(--ink-3);
  padding-bottom: 12px;
  border-bottom: 1px solid var(--rule);
}
.hero-stats { display: flex; flex-direction: column; }
.stat { padding: 16px 0 14px; border-bottom: 1px solid var(--rule-soft); }
.stat:last-child { border-bottom: none; }
.stat b {
  display: block;
  font-size: 30px;
  font-weight: 700;
  letter-spacing: -0.03em;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}
.stat span {
  display: block;
  margin-top: 6px;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.06em;
  color: var(--ink-3);
}

/* ---------- 解析结果工作台 ---------- */
.result { padding: 40px 0 88px; }
.result-card {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  border: 1px solid var(--rule);
  background: transparent;
}
.result-side {
  padding: 20px;
  border-right: 1px solid var(--rule);
  display: flex;
  flex-direction: column;
}
.result-media {
  border: 1px solid var(--rule-soft);
  overflow: hidden;
  background: transparent;
  margin-bottom: 16px;
}
/* 真实封面统一灰度：瑞士风格里图片只用黑白 */
.result-media img {
  width: 100%;
  display: block;
  aspect-ratio: 16 / 9;
  object-fit: cover;
  filter: grayscale(1) contrast(1.06);
}
.thumb-fallback {
  display: grid;
  place-items: center;
  height: 158px;
  color: var(--ink-3);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.08em;
}
.result-title-row { display: flex; align-items: flex-start; gap: 10px; }
.result-title {
  font-size: 17px;
  line-height: 1.34;
  font-weight: 700;
  letter-spacing: -0.01em;
  flex: 1;
}
.icon-btn {
  border: 1px solid var(--rule-soft);
  background: transparent;
  color: var(--ink-3);
  width: 26px;
  height: 26px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}
.icon-btn:hover { color: var(--red); border-color: var(--red); }
.result-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 12px;
  margin-top: 10px;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.03em;
  color: var(--ink-3);
}
.platform-tag {
  border: 1px solid var(--ink);
  color: var(--ink);
  font-size: 9.5px;
  letter-spacing: 0.08em;
  padding: 1px 5px;
}
.fmt-label {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.1em;
  color: var(--ink-3);
  margin: 22px 0 8px;
}
.fmt-list {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  border-top: 1px solid var(--rule-soft);
  border-left: 1px solid var(--rule-soft);
}
.fmt-chip {
  border: none;
  border-right: 1px solid var(--rule-soft);
  border-bottom: 1px solid var(--rule-soft);
  background: transparent;
  color: var(--ink);
  padding: 9px 10px;
  font-size: 12px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  text-align: left;
  transition: background 0.1s, color 0.1s;
}
.fmt-chip:hover { color: var(--red); }
.fmt-chip small { font-family: var(--font-mono); font-size: 9px; color: var(--ink-3); }
.fmt-chip:last-child:nth-child(odd) { grid-column: span 2; }
.fmt-chip.active { background: var(--ink); color: var(--paper); }
.fmt-chip.active small { color: var(--paper); opacity: 0.65; }
.fmt-chip.vip { color: var(--ink-3); }
.fmt-chip .vip-lock { color: var(--red); margin-left: 2px; vertical-align: -1px; }
.fmt-chip.active .vip-lock { color: #fff; }

.progress-area { margin-top: 20px; padding-top: 14px; border-top: 1px solid var(--rule); }
.progress-head {
  display: flex;
  justify-content: space-between;
  font-family: var(--font-mono);
  font-size: 10.5px;
  letter-spacing: 0.04em;
  color: var(--ink);
  margin-bottom: 8px;
}
.progress-bar { height: 6px; background: var(--rule-soft); overflow: hidden; }
.progress-fill { height: 100%; background: var(--red); transition: width 0.35s; }
.progress-meta {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--ink-3);
  margin-top: 8px;
}
.done-row { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.done-badge {
  border: 1px solid var(--ink);
  padding: 6px 10px;
  font-family: var(--font-mono);
  font-size: 10.5px;
  letter-spacing: 0.04em;
}

.result-main {
  padding: 20px;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.ai-row { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.ai-btn { min-width: 168px; }
.quota-tip { font-family: var(--font-mono); font-size: 10.5px; color: var(--ink-3); letter-spacing: 0.03em; }
.quota-tip.vip { color: var(--red); }
.spinner {
  width: 12px;
  height: 12px;
  border: 2px solid var(--rule-soft);
  border-top-color: var(--red);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.summary-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: flex-start;
  gap: 12px;
  color: var(--ink-3);
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.04em;
  padding: 48px 24px;
  border: 1px dashed var(--rule-soft);
}
.summary-empty svg { color: var(--ink-3); }

.summary-panel {
  margin-top: 16px;
  border: 1px solid var(--rule);
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1;
}
.summary-tabs {
  display: flex;
  align-items: stretch;
  gap: 0;
  padding: 0;
  border-bottom: 1px solid var(--rule);
  flex-wrap: wrap;
}
.stab {
  border: none;
  border-right: 1px solid var(--rule-soft);
  background: transparent;
  color: var(--ink-2);
  font-size: 12px;
  padding: 10px 14px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.stab:hover { color: var(--ink); background: var(--paper-2); }
.stab.active { background: var(--ink); color: var(--paper); }
.stab-ico { flex-shrink: 0; width: 13px; height: 13px; }
.stab-right { margin-left: auto; display: flex; align-items: center; gap: 12px; padding: 0 12px; }
.copy-link { font-size: 11px; }
.basis-badge {
  font-family: var(--font-mono);
  font-size: 9.5px;
  letter-spacing: 0.04em;
  border: 1px solid var(--rule-soft);
  color: var(--ink-2);
  padding: 2px 6px;
  white-space: nowrap;
  cursor: help;
}
.basis-badge.subtitles { border-color: var(--ink); color: var(--ink); }
.basis-badge.meta { border-style: dashed; }

.summary-content { padding: 18px; max-height: 620px; overflow-y: auto; flex: 1; }
.stream-box {
  margin: 0;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.75;
  color: var(--ink);
  white-space: pre-wrap;
  word-break: break-word;
}
.stream-box::after { content: '▌'; color: var(--red); animation: blink 1s step-start infinite; }
@keyframes blink { 50% { opacity: 0; } }

.summary-line {
  font-size: 14.5px;
  line-height: 1.7;
  border-left: 3px solid var(--red);
  padding-left: 12px;
}
.summary-points { margin: 18px 0 0; padding: 0; list-style: none; }
.summary-points li {
  position: relative;
  padding: 6px 0 6px 18px;
  font-size: 13px;
  line-height: 1.65;
  border-bottom: 1px solid var(--rule-soft);
}
.summary-points li::before {
  content: '';
  position: absolute;
  left: 2px;
  top: 14px;
  width: 5px;
  height: 5px;
  background: var(--red);
}
.summary-sec {
  font-family: var(--font-mono);
  font-size: 10.5px;
  font-weight: 400;
  letter-spacing: 0.1em;
  margin: 24px 0 8px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--rule);
}
.summary-sec small {
  color: var(--ink-3);
  font-weight: 400;
  font-size: 10px;
  margin-left: 8px;
  letter-spacing: 0.04em;
}
.chapter-row {
  display: flex;
  gap: 12px;
  padding: 9px 0;
  align-items: flex-start;
  border-bottom: 1px solid var(--rule-soft);
}
.ts-chip {
  flex-shrink: 0;
  background: var(--ink);
  color: var(--paper);
  font-family: var(--font-mono);
  font-size: 10.5px;
  letter-spacing: 0.02em;
  padding: 2px 6px;
  min-width: 46px;
  text-align: center;
  margin-top: 1px;
}
.ts-chip:hover { background: var(--red); color: #fff; }
.chapter-text b { font-size: 13px; font-weight: 700; }
.chapter-text p, .tl-text { color: var(--ink-2); font-size: 12.5px; line-height: 1.6; margin-top: 3px; }
.transcript-list { display: block; }
.empty-tip {
  color: var(--ink-3);
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.03em;
  padding: 22px 0;
}

/* ---------- 三步流程 ---------- */
.steps { border-top: 1px solid var(--rule); }
.steps-grid { display: grid; grid-template-columns: repeat(3, 1fr); }
.step { padding: 34px 28px 38px; border-right: 1px solid var(--rule-soft); }
.step:first-child { padding-left: 0; }
.step:last-child { border-right: none; padding-right: 0; }
.step-num {
  display: block;
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.12em;
  color: var(--red);
  margin-bottom: 22px;
}
.step h4 { font-size: 21px; margin-bottom: 8px; letter-spacing: -0.02em; }
.step p { font-size: 12.5px; line-height: 1.7; color: var(--ink-2); max-width: 32ch; }

/* ---------- AI 效果展示 ---------- */
.showcase { border-top: 1px solid var(--rule); padding: 84px 0 88px; }
.showcase-grid {
  display: grid;
  grid-template-columns: 5fr 6fr;
  gap: 0 56px;
  align-items: start;
}
.sc-f {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr);
  gap: 0 16px;
  padding: 18px 0 20px;
  border-top: 1px solid var(--rule-soft);
}
.sc-f:first-child { border-top: 1px solid var(--rule); }
.sc-f-icon {
  font-family: var(--font-mono);
  font-size: 10.5px;
  letter-spacing: 0.08em;
  color: var(--red);
  padding-top: 3px;
}
.sc-f h4 { font-size: 16px; margin-bottom: 6px; letter-spacing: -0.01em; }
.sc-f p { font-size: 12.5px; line-height: 1.7; color: var(--ink-2); }

.demo-card { border: 1px solid var(--rule); }
.demo-head {
  display: flex;
  gap: 14px;
  align-items: center;
  padding: 14px 16px;
  border-bottom: 1px solid var(--rule);
}
.demo-thumb {
  flex: none;
  width: 64px;
  height: 40px;
  background: var(--ink);
  display: grid;
  place-items: center;
}
.demo-thumb svg { color: var(--red); }
.demo-tag {
  display: inline-block;
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.1em;
  border: 1px solid var(--rule-soft);
  color: var(--ink-3);
  padding: 1px 5px;
}
.demo-head-txt b { display: block; font-size: 13px; margin-top: 5px; letter-spacing: -0.01em; }
.demo-ov {
  font-size: 12.5px;
  line-height: 1.75;
  color: var(--ink);
  padding: 16px;
  margin: 0;
  border-bottom: 1px solid var(--rule-soft);
}
.demo-points { list-style: none; margin: 0; padding: 6px 16px 12px; }
.demo-points li {
  font-size: 12px;
  line-height: 1.7;
  color: var(--ink-2);
  padding: 7px 0 7px 18px;
  position: relative;
  border-bottom: 1px solid var(--rule-soft);
}
.demo-points li:last-child { border-bottom: none; }
.demo-points li::before {
  content: '';
  position: absolute;
  left: 2px;
  top: 14px;
  width: 5px;
  height: 5px;
  background: var(--red);
}
.demo-chapters { border-top: 1px solid var(--rule); }
.demo-ch {
  display: grid;
  grid-template-columns: 52px minmax(0, 1fr);
  gap: 12px;
  padding: 9px 16px;
  border-bottom: 1px solid var(--rule-soft);
  font-size: 12px;
  color: var(--ink-2);
}
.demo-ch i {
  font-style: normal;
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--red);
}
.demo-chips { display: flex; }
.demo-chip {
  flex: 1;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 10px 16px;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.04em;
  color: var(--ink-2);
  border-right: 1px solid var(--rule-soft);
}
.demo-chip:last-child { border-right: none; }
.demo-chip svg { color: var(--red); }

/* ---------- 功能 ---------- */
.features { border-top: 1px solid var(--rule); padding: 84px 0 88px; }
.feature-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  border-top: 1px solid var(--rule);
  border-left: 1px solid var(--rule-soft);
}
.feature-card {
  border-right: 1px solid var(--rule-soft);
  border-bottom: 1px solid var(--rule-soft);
  padding: 24px 22px 28px;
}
.feature-card:hover { background: var(--paper-2); }
.feature-num {
  display: block;
  font-family: var(--font-mono);
  font-size: 10.5px;
  letter-spacing: 0.12em;
  color: var(--red);
  margin-bottom: 22px;
}
.feature-card h4 { font-size: 18px; margin-bottom: 8px; letter-spacing: -0.02em; }
.feature-card p { font-size: 12.5px; line-height: 1.7; color: var(--ink-2); }
.tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 14px; }
.tags span {
  font-family: var(--font-mono);
  font-size: 9.5px;
  letter-spacing: 0.04em;
  color: var(--ink-3);
  border: 1px solid var(--rule-soft);
  padding: 1px 5px;
}

/* ---------- 场景 ---------- */
.scene { border-top: 1px solid var(--rule); padding: 84px 0 88px; }
.scene-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  border-top: 1px solid var(--rule);
  border-left: 1px solid var(--rule-soft);
}
.scene-card {
  border-right: 1px solid var(--rule-soft);
  border-bottom: 1px solid var(--rule-soft);
  padding: 22px 20px 26px;
}
.scene-card:hover { background: var(--paper-2); }
.scene-num {
  display: block;
  font-family: var(--font-mono);
  font-size: 10.5px;
  letter-spacing: 0.12em;
  color: var(--red);
  margin-bottom: 20px;
}
.scene-card h4 { font-size: 16px; margin-bottom: 8px; letter-spacing: -0.01em; }
.scene-card p { font-size: 12px; line-height: 1.7; color: var(--ink-2); }

/* ---------- 定价 ---------- */
.pricing { border-top: 1px solid var(--rule); padding: 84px 0 88px; }
.plan-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0;
  max-width: none;
  margin: 0;
  border-top: 1px solid var(--rule);
  border-left: 1px solid var(--rule-soft);
  align-items: stretch;
}
.plan-card {
  position: relative;
  border-right: 1px solid var(--rule-soft);
  border-bottom: 1px solid var(--rule-soft);
  padding: 26px 24px 30px;
  display: flex;
  flex-direction: column;
  background: transparent;
}
.plan-card.highlight { border-top: 4px solid var(--red); padding-top: 22px; }
.plan-badge {
  position: absolute;
  top: 0;
  right: 0;
  background: var(--red);
  color: #fff;
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.1em;
  padding: 3px 8px;
  white-space: nowrap;
}
.plan-card.highlight .plan-badge { top: -4px; }
.plan-card.highlight .btn { background: var(--red); border-color: var(--red); color: #fff; }
.plan-card.highlight .btn:hover { background: var(--red-dark); border-color: var(--red-dark); }
.plan-card h4 {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 400;
  letter-spacing: 0.12em;
  color: var(--ink-2);
}
.plan-price {
  margin: 16px 0 20px;
  font-weight: 700;
  font-size: 48px;
  line-height: 1;
  letter-spacing: -0.04em;
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 2px;
}
.plan-price em { font-style: normal; font-size: 18px; font-weight: 400; }
.plan-price small { font-size: 11px; font-weight: 400; color: var(--ink-3); margin-left: 6px; }
.plan-price .price-approx {
  display: block;
  flex-basis: 100%;
  font-family: var(--font-mono);
  font-size: 9.5px;
  font-weight: 400;
  color: var(--ink-3);
  letter-spacing: 0.04em;
  margin-top: 6px;
}
.plan-feats { list-style: none; flex: 1; margin: 0 0 22px; }
.plan-feats li {
  font-size: 12px;
  color: var(--ink-2);
  padding: 8px 0 8px 16px;
  border-bottom: 1px solid var(--rule-soft);
  position: relative;
}
.plan-feats li::before {
  content: '—';
  position: absolute;
  left: 0;
  color: var(--red);
}

/* ---------- FAQ ---------- */
.faq { border-top: 1px solid var(--rule); padding: 84px 0 88px; }
.faq-inner { display: block; }
.faq-item { border-bottom: 1px solid var(--rule-soft); }
.faq-item:first-of-type { border-top: 1px solid var(--rule); }
.faq-item summary {
  position: relative;
  list-style: none;
  cursor: pointer;
  font-size: 16.5px;
  font-weight: 700;
  letter-spacing: -0.01em;
  padding: 18px 48px 18px 0;
}
.faq-item summary::-webkit-details-marker { display: none; }
.faq-item summary::after {
  content: '+';
  position: absolute;
  right: 2px;
  top: 14px;
  font-size: 22px;
  font-weight: 400;
  color: var(--red);
  line-height: 1;
}
.faq-item[open] summary::after { content: '–'; }
.faq-item p {
  font-size: 12.5px;
  line-height: 1.8;
  color: var(--ink-2);
  max-width: 80ch;
  padding: 0 0 22px;
}

/* ---------- 底部 CTA ---------- */
.cta-band {
  border-top: 1px solid var(--rule);
  background: var(--ink);
  color: var(--paper);
  padding: 80px 0;
}
.cta-inner {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 40px;
  align-items: end;
}
.cta-band h2 {
  color: var(--paper);
  font-size: clamp(30px, 4.4vw, 56px);
  letter-spacing: -0.035em;
  line-height: 1.02;
}
.cta-band p { color: var(--paper); opacity: 0.66; font-size: 12.5px; line-height: 1.7; margin-top: 14px; }
.cta-eyebrow {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.14em;
  color: var(--red);
  margin-bottom: 22px;
}
.cta-band .btn {
  background: var(--red);
  border-color: var(--red);
  color: #fff;
  white-space: nowrap;
}
.cta-band .btn:hover { background: var(--paper); border-color: var(--paper); color: var(--ink); }

/* ---------- 页脚 ---------- */
.footer { border-top: 1px solid var(--rule); padding: 40px 0 56px; }
.footer-grid {
  display: grid;
  grid-template-columns: 3fr 6fr 3fr;
  gap: 32px;
  align-items: start;
}
.footer-logo { font-size: 21px; font-weight: 700; letter-spacing: -0.02em; }
.footer-meta {
  font-family: var(--font-mono);
  font-size: 9.5px;
  letter-spacing: 0.08em;
  color: var(--ink-3);
  margin-top: 8px;
}
.footer-disclaimer {
  font-size: 11.5px;
  line-height: 1.85;
  color: var(--ink-3);
  max-width: none;
  margin: 0;
}
.footer-copy {
  font-family: var(--font-mono);
  font-size: 9.5px;
  letter-spacing: 0.04em;
  color: var(--ink-3);
  text-align: right;
  line-height: 1.9;
}

/* ---------- 弹窗 ---------- */
.modal-mask {
  position: fixed;
  inset: 0;
  z-index: 100;
  background: rgba(17, 17, 17, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.modal {
  width: 400px;
  max-width: 100%;
  background: var(--paper);
  border: 1px solid var(--ink);
  padding: 26px;
  text-align: left;
}
.modal-title {
  font-size: 21px;
  letter-spacing: -0.02em;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--rule);
  margin-bottom: 18px;
}
.auth-form { display: flex; flex-direction: column; gap: 14px; margin: 0 0 8px; }
.auth-field { display: flex; flex-direction: column; gap: 6px; }
.auth-field span {
  font-family: var(--font-mono);
  font-size: 9.5px;
  letter-spacing: 0.1em;
  color: var(--ink-3);
}
.auth-field input {
  height: 44px;
  border: 1px solid var(--rule-soft);
  background: transparent;
  color: var(--ink);
  padding: 0 12px;
  font-size: 14px;
  outline: none;
}
.auth-field input:focus { border-color: var(--ink); }
.link-btn {
  background: none;
  border: none;
  color: var(--ink-3);
  font-size: 12px;
  letter-spacing: 0.02em;
  margin-top: 14px;
  padding: 0;
  text-decoration: underline;
  text-underline-offset: 3px;
  text-decoration-color: var(--rule-soft);
}
.link-btn:hover { color: var(--red); text-decoration-color: var(--red); }
.modal-amount { font-size: 46px; font-weight: 700; letter-spacing: -0.04em; margin: 8px 0 18px; }
.fake-qr { margin-bottom: 20px; }
.fake-qr canvas { border: 1px solid var(--rule-soft); padding: 8px; background: #fff; display: block; margin: 0 auto; }
.qr-tip {
  font-family: var(--font-mono);
  font-size: 9.5px;
  line-height: 1.9;
  color: var(--ink-3);
  margin-top: 12px;
  text-align: center;
}
.pay-success { padding: 6px 0 22px; }
.success-icon {
  width: 44px;
  height: 44px;
  border: 1px solid var(--red);
  color: var(--red);
  display: grid;
  place-items: center;
  font-size: 22px;
  margin-bottom: 16px;
}
.pay-success h3 { font-size: 20px; letter-spacing: -0.02em; margin-bottom: 10px; }
.pay-success p { color: var(--ink-2); font-size: 12.5px; line-height: 1.8; }
.error-tip { color: var(--red); font-size: 12.5px; line-height: 1.7; }

/* ---------- 导航滚动进度 ---------- */
.nav-progress {
  position: absolute;
  left: 0;
  bottom: -1px;
  height: 2px;
  width: 0;
  background: var(--red);
  transition: width 0.1s linear;
}

/* ---------- 展示字体：大标题用衬线，正文仍是无衬线 ---------- */
.hero-title,
.sec-title,
.statement-line,
.cta-band h2 {
  font-family: var(--font-display);
  font-weight: 400;
  letter-spacing: -0.012em;
}
.hero-title { font-size: clamp(46px, 7.8vw, 102px); line-height: 0.96; }
.sec-title { font-size: clamp(32px, 3.9vw, 58px); line-height: 1.02; }

/* ---------- Hero 入场（加载即播放，不依赖滚动） ---------- */
.hero .eyebrow { animation: rise 0.8s 0.05s both cubic-bezier(0.2, 0.65, 0.3, 1); }
.hero-title { animation: rise 0.95s 0.14s both cubic-bezier(0.2, 0.65, 0.3, 1); }
.hero-sub { animation: rise 0.9s 0.26s both cubic-bezier(0.2, 0.65, 0.3, 1); }
.input-card { animation: rise 0.9s 0.36s both cubic-bezier(0.2, 0.65, 0.3, 1); }
.hero-hint, .hero-error { animation: rise 0.8s 0.46s both cubic-bezier(0.2, 0.65, 0.3, 1); }
.hero-aside .stat:nth-child(1) { animation: rise 0.8s 0.42s both cubic-bezier(0.2, 0.65, 0.3, 1); }
.hero-aside .stat:nth-child(2) { animation: rise 0.8s 0.49s both cubic-bezier(0.2, 0.65, 0.3, 1); }
.hero-aside .stat:nth-child(3) { animation: rise 0.8s 0.56s both cubic-bezier(0.2, 0.65, 0.3, 1); }
.hero-aside .stat:nth-child(4) { animation: rise 0.8s 0.63s both cubic-bezier(0.2, 0.65, 0.3, 1); }

/* ---------- 按钮箭头微动 ---------- */
.btn-arrow { display: inline-block; transition: transform 0.28s cubic-bezier(0.2, 0.65, 0.3, 1); }
.btn:hover .btn-arrow { transform: translateX(4px); }

/* ---------- 平台跑马灯 ---------- */
.brandbar {
  display: grid;
  grid-template-columns: 188px minmax(0, 1fr);
  border-bottom: 1px solid var(--rule);
  background: var(--paper);
}
.brandbar-label {
  display: flex;
  align-items: center;
  padding: 0 20px;
  border-right: 1px solid var(--rule);
  font-family: var(--font-mono);
  font-size: 9.5px;
  letter-spacing: 0.1em;
  color: var(--ink-3);
  white-space: nowrap;
}
.marquee { overflow: hidden; }
.marquee-track {
  display: flex;
  width: max-content;
  animation: marquee 34s linear infinite;
}
.marquee:hover .marquee-track { animation-play-state: paused; }
.marquee-item {
  display: flex;
  align-items: center;
  padding: 13px 26px;
  border-right: 1px solid var(--rule-soft);
  font-size: 12.5px;
  letter-spacing: 0.02em;
  color: var(--ink-2);
  white-space: nowrap;
}

/* ---------- 大字陈述 ---------- */
.statement { border-bottom: 1px solid var(--rule); padding: 96px 0 104px; }
.statement-inner {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 260px;
  gap: 40px;
  align-items: end;
}
.statement-label {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.14em;
  color: var(--red);
  margin-bottom: 28px;
}
.statement-line {
  font-size: clamp(34px, 4.8vw, 70px);
  line-height: 1.06;
  white-space: pre-line;
}
.statement-line::after {
  content: '';
  display: inline-block;
  width: 0.13em;
  height: 0.13em;
  background: var(--red);
  margin-left: 0.1em;
  vertical-align: 0.08em;
}
.statement-sub {
  font-size: 12.5px;
  line-height: 1.75;
  color: var(--ink-2);
  padding-bottom: 12px;
}

/* ---------- 三种方式对比 ---------- */
.compare { border-top: 1px solid var(--rule); padding: 84px 0 88px; }
.cmp-table {
  width: 100%;
  border-collapse: collapse;
  text-align: left;
}
.cmp-table th,
.cmp-table td {
  border-top: 1px solid var(--rule-soft);
  padding: 16px 18px;
  font-size: 13px;
  vertical-align: middle;
}
.cmp-table thead th {
  border-top: 1px solid var(--rule);
  border-bottom: 1px solid var(--rule);
  padding-top: 12px;
  padding-bottom: 12px;
  font-family: var(--font-mono);
  font-size: 9.5px;
  font-weight: 400;
  letter-spacing: 0.08em;
  color: var(--ink-3);
}
.cmp-table thead th.cmp-us { color: var(--ink); border-top: 3px solid var(--red); padding-top: 10px; background: var(--paper-2); }
.cmp-table tbody th {
  font-size: 12.5px;
  font-weight: 400;
  color: var(--ink-2);
  width: 30%;
}
.cmp-table td {
  width: 23%;
  text-align: left;
  font-family: var(--font-mono);
  font-size: 15px;
  color: var(--ink-3);
}
.cmp-table th + th, .cmp-table td + td { border-left: 1px solid var(--rule-soft); }
.cmp-table td.cmp-us { background: var(--paper-2); color: var(--ink); }
.cmp-table td.cmp-us.on { color: var(--red); }
.cmp-table td.on { color: var(--ink); }
.cmp-table tbody tr:hover td { background: var(--paper-2); }
.cmp-legend {
  margin-top: 12px;
  font-family: var(--font-mono);
  font-size: 9.5px;
  letter-spacing: 0.04em;
  color: var(--ink-3);
}

/* ---------- 悬停细节 ---------- */
.feature-card,
.scene-card,
.plan-card,
.cmp-table td { transition: background 0.22s ease; }
.demo-card { transition: transform 0.35s cubic-bezier(0.2, 0.65, 0.3, 1); }
.demo-card:hover { transform: translateY(-2px); }
.feature-card:hover .feature-num,
.scene-card:hover .scene-num { color: var(--ink); }
.faq-item summary { transition: color 0.18s ease; }
.faq-item summary:hover { color: var(--red); }
.faq-item p { animation: fade-up 0.35s both cubic-bezier(0.2, 0.65, 0.3, 1); }

@media (prefers-reduced-motion: reduce) {
  .marquee-track { animation: none; }
  .demo-card:hover { transform: none; }
}

/* ---------- 响应式 ---------- */
@media (max-width: 1080px) {
  .hero-main { grid-column: 1 / span 8; }
  .hero-aside { grid-column: 9 / span 4; }
  .showcase-grid { gap: 0 40px; }
}

@media (max-width: 920px) {
  .hero { padding: 56px 0 48px; }
  .hero-main { grid-column: 1 / -1; }
  .hero-aside {
    grid-column: 1 / -1;
    border-left: none;
    border-top: 1px solid var(--rule);
    padding-left: 0;
    margin-top: 40px;
    padding-top: 0;
  }
  .hero-stats { flex-direction: row; flex-wrap: wrap; }
  .stat { flex: 1 1 40%; padding: 16px 16px 14px 0; border-bottom: 1px solid var(--rule-soft); }
  .stat:nth-child(odd) { border-right: 1px solid var(--rule-soft); padding-right: 16px; }
  .showcase-grid { grid-template-columns: 1fr; gap: 40px; }
  .scene-grid { grid-template-columns: repeat(2, 1fr); }
  .feature-grid { grid-template-columns: repeat(2, 1fr); }
  .plan-grid { grid-template-columns: 1fr; }
  .plan-card { border-right: none; }
  .steps-grid { grid-template-columns: 1fr; }
  .step { padding: 26px 0 30px; border-right: none; border-bottom: 1px solid var(--rule-soft); }
  .step:last-child { border-bottom: none; }
  .result-card { grid-template-columns: 1fr; }
  .result-side { border-right: none; border-bottom: 1px solid var(--rule); }
  .cta-inner { grid-template-columns: 1fr; align-items: start; gap: 26px; }
  .footer-grid { grid-template-columns: 1fr; gap: 20px; }
  .footer-copy { text-align: left; }
  .sec-head { grid-template-columns: 44px minmax(0, 1fr); }
  .sec-meta { display: none; }
  .brandbar { grid-template-columns: 1fr; }
  .brandbar-label { border-right: none; border-bottom: 1px solid var(--rule); padding: 8px 18px; }
  .statement { padding: 64px 0 72px; }
  .statement-inner { grid-template-columns: 1fr; gap: 22px; }
  .statement-sub { padding-bottom: 0; }
  .cmp-table th, .cmp-table td { padding: 12px 10px; }
  .cmp-table thead th { font-size: 9px; }
}

@media (max-width: 600px) {
  .nav-links { display: none; }
  .nav-inner { gap: 8px; height: 54px; }
  .logo { flex-shrink: 0; white-space: nowrap; font-size: 15px; }
  .theme-select { display: none; }
  .lang-select { max-width: 82px; font-size: 10px; padding: 0 4px; }
  .nav-inner .btn-sm { flex-shrink: 0; white-space: nowrap; padding: 9px 10px; min-height: 34px; font-size: 10.5px; }
  .hero-title { font-size: clamp(38px, 11.5vw, 56px); }
  .hero-sub { font-size: 13px; }
  .input-row { flex-direction: column; }
  .url-input { flex: none; width: 100%; height: 50px; border-right: none; border-bottom: 1px solid var(--rule); }
  .input-row .btn { height: 48px; width: 100%; }
  .hero-stats { flex-direction: column; }
  .stat, .stat:nth-child(odd) { border-right: none; padding-right: 0; }
  .scene-grid, .feature-grid { grid-template-columns: 1fr; }
  .cta-band { padding: 56px 0; }
  .cta-band h2 { font-size: 30px; }
  .btn-lg { width: 100%; }
  .done-row { flex-direction: column; align-items: stretch; }
  .summary-content { max-height: 60vh; padding: 12px; }
  .stab { font-size: 11px; padding: 9px 10px; }
  .stab-right { padding: 0 8px; }
  .result-side, .result-main { padding: 13px; }
  .modal { padding: 20px; }
  .hero-title { font-size: clamp(40px, 11.4vw, 58px); }
  .statement-line { font-size: clamp(34px, 10vw, 44px); }
  .cmp-table th, .cmp-table td { padding: 10px 6px; }
  .cmp-table tbody th { font-size: 11px; width: 34%; }
  .cmp-table thead th { font-size: 7.5px; letter-spacing: 0; white-space: nowrap; }
  .cmp-table td { font-size: 12px; width: 22%; }
  .marquee-item { padding: 11px 18px; font-size: 11.5px; }
}
</style>
