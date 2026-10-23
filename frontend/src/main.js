import { createApp } from 'vue'
import './style.css'
import App from './App.vue'

const app = createApp(App)

/* v-reveal：元素进入视口时淡入上移。
   用法：v-reveal 或 v-reveal="120"（延迟毫秒，用于同组元素错峰）。
   尊重 prefers-reduced-motion：直接显示，不做动画。 */
const reduce = window.matchMedia
  && window.matchMedia('(prefers-reduced-motion: reduce)').matches

let observer = null
if (!reduce && 'IntersectionObserver' in window) {
  observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue
      entry.target.classList.add('is-in')
      observer.unobserve(entry.target)
    }
  }, { rootMargin: '0px', threshold: 0 })
}

/* 兜底：滚到页面底部时，把还没触发的元素一次性显示，避免"最后一块永远不出现" */
let bottomSwept = false
function sweepIfBottom() {
  if (bottomSwept) return
  const doc = document.documentElement
  if (window.scrollY + window.innerHeight >= doc.scrollHeight - 4) {
    bottomSwept = true
    document.querySelectorAll('.reveal:not(.is-in)').forEach(el => el.classList.add('is-in'))
  }
}
window.addEventListener('scroll', sweepIfBottom, { passive: true })

app.directive('reveal', {
  mounted(el, binding) {
    if (!observer) {
      el.classList.add('reveal', 'is-in')
      return
    }
    el.classList.add('reveal')
    if (binding.value) el.style.transitionDelay = `${binding.value}ms`
    observer.observe(el)
  },
  unmounted(el) {
    observer?.unobserve(el)
  },
})

app.mount('#app')
