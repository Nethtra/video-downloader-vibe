/**
 * 三态主题：auto（跟随系统，默认）/ light / dark。
 * - 持久化 localStorage('theme')；auto 模式监听系统配色变化实时切换
 * - 切换方式：html 根元素加/去 .dark 类（style.css 中 html.dark 覆盖变量）
 * - 防闪烁：index.html <head> 有同逻辑的内联脚本，先于 Vue 挂载设好初始类；
 *   本模块 apply() 与其幂等，重复执行无害
 */
import { ref } from 'vue'

const KEY = 'theme'
const VALID = ['auto', 'light', 'dark']
const media = typeof window !== 'undefined' && window.matchMedia
  ? window.matchMedia('(prefers-color-scheme: dark)')
  : null

function readSaved() {
  try {
    const v = localStorage.getItem(KEY)
    return VALID.includes(v) ? v : 'auto'
  } catch { return 'auto' }
}

function isDark(value) {
  return value === 'dark' || (value === 'auto' && !!media?.matches)
}

function apply() {
  document.documentElement.classList.toggle('dark', isDark(theme.value))
}

export const theme = ref(readSaved())

export function setTheme(v) {
  if (!VALID.includes(v)) return
  theme.value = v
  try { localStorage.setItem(KEY, v) } catch { /* ignore */ }
  apply()
}

// auto 模式下跟随系统实时切换
media?.addEventListener?.('change', () => {
  if (theme.value === 'auto') apply()
})

apply()
