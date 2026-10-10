<script setup>
/**
 * 思维导图（SVG 横向树）：根节点在左，向右展开。
 * 后端已约束：最多 3 层、每层最多 5 个节点。
 * 功能：覆盖层全屏（自适应铺满 / 原始大小切换，ESC 关闭）+ 导出 PNG/SVG（浏览器本地生成）。
 * 对外接口不变：仅接收 data prop。
 */
import { ref, computed, onBeforeUnmount } from 'vue'
import { t } from '../i18n'

const props = defineProps({
  data: { type: Object, required: true }, // {title, children: [{title, children}]}
})

/* ---------- 视图状态 ---------- */
const fullscreen = ref(false)  // 覆盖层全屏
const fitView = ref(true)      // 全屏时：true=自适应铺满 false=原始大小+滚动

/* ---------- 布局 ---------- */
const PAD = 12
const LINK_PAD = 50

// 全屏时列宽/节点略放大、标题截断放宽，铺满视口后可读性更好
const dims = computed(() => fullscreen.value
  ? { ROW_H: 46, COL_W: 230, NODE_W: 150, ROOT_W: 168, NODE_H: 34, TITLE_MAX: 14 }
  : { ROW_H: 42, COL_W: 190, NODE_W: 120, ROOT_W: 130, NODE_H: 30, TITLE_MAX: 8 })

// 布局：叶子节点均匀分配行，父节点取子节点行的中位数
const tree = computed(() => {
  const { ROW_H, COL_W, NODE_W, NODE_H } = dims.value
  const nodes = []
  const links = []
  let leafRow = 0

  function walk(node, depth, parentId) {
    // 先 push 占位再递归：保证 id 与数组下标一致。
    // （原实现 id 在递归前取 nodes.length、push 在递归后，子节点先占坑导致 id 重复，
    //   连线端点 byId 映射错乱 —— 表现为部分父子节点之间没有连线）
    const id = nodes.length
    nodes.push({ id, depth, row: 0, title: node.title, x: 0, y: 0, isRoot: depth === 0 })
    const children = node.children || []
    let row
    if (!children.length) {
      row = leafRow++
    } else {
      const rows = children.map(c => walk(c, depth + 1, id).row)
      row = (Math.min(...rows) + Math.max(...rows)) / 2
    }
    nodes[id].row = row
    if (parentId !== null) links.push({ from: parentId, to: id })
    return { id, row }
  }

  walk(props.data, 0, null)

  // 行号可能出现小数（父节点中位数），按行排序重排 y 保证无重叠
  const sorted = [...nodes].sort((a, b) => a.row - b.row)
  sorted.forEach((n, i) => { n.row = i })

  const maxDepth = Math.max(...nodes.map(n => n.depth))
  const width = PAD * 2 + maxDepth * COL_W + NODE_W + LINK_PAD
  const height = PAD * 2 + (nodes.length - 1) * ROW_H + NODE_H

  nodes.forEach(n => {
    n.x = PAD + n.depth * COL_W
    n.y = PAD + n.row * ROW_H
  })
  const byId = Object.fromEntries(nodes.map(n => [n.id, n]))
  const finalLinks = links.map(l => ({
    x1: byId[l.from].x + NODE_W, y1: byId[l.from].y + NODE_H / 2,
    x2: byId[l.to].x, y2: byId[l.to].y + NODE_H / 2,
  }))
  return { nodes, links: finalLinks, width, height }
})

function short(title) {
  const max = dims.value.TITLE_MAX
  return title.length > max ? title.slice(0, max) + '…' : title
}

/* ---------- 全屏 ---------- */
function openFullscreen() {
  fullscreen.value = true
  fitView.value = true
  document.addEventListener('keydown', onKeydown)
}
function closeFullscreen() {
  fullscreen.value = false
  document.removeEventListener('keydown', onKeydown)
}
function onKeydown(e) { if (e.key === 'Escape') closeFullscreen() }
onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))

/* ---------- 导出 PNG / SVG（浏览器本地生成，不经服务器） ---------- */
const XML_ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' }
const esc = s => String(s).replace(/[&<>"']/g, c => XML_ESC[c])
const COLORS = { primary: '#3370ff', nodeBg: '#e8efff', text: '#1f2329', link: '#e5e6eb' }

// 独立构造 SVG 字符串（内联样式，不依赖组件 CSS），同时用于 SVG 下载与 PNG 转换
function buildSvgString() {
  const { nodes, links, width, height } = tree.value
  const { NODE_W, ROOT_W, NODE_H } = dims.value
  const out = []
  out.push(`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">`)
  out.push(`<rect width="${width}" height="${height}" fill="#ffffff"/>`)
  for (const l of links) {
    const mx = (l.x1 + l.x2) / 2
    out.push(`<path d="M ${l.x1} ${l.y1} C ${mx} ${l.y1}, ${mx} ${l.y2}, ${l.x2} ${l.y2}" fill="none" stroke="${COLORS.link}" stroke-width="1.5"/>`)
  }
  for (const n of nodes) {
    const w = n.isRoot ? ROOT_W : NODE_W
    const stroke = n.isRoot ? '' : ` stroke="${COLORS.primary}" stroke-width="1"`
    out.push(`<rect x="${n.x}" y="${n.y}" width="${w}" height="${NODE_H}" rx="8" fill="${n.isRoot ? COLORS.primary : COLORS.nodeBg}"${stroke}/>`)
    out.push(`<text x="${n.x + w / 2}" y="${n.y + NODE_H / 2 + 1}" text-anchor="middle" dominant-baseline="middle" font-size="${n.isRoot ? 13 : 12.5}" font-weight="${n.isRoot ? 600 : 400}" fill="${n.isRoot ? '#fff' : COLORS.text}" font-family="PingFang SC, Microsoft YaHei, sans-serif">${esc(short(n.title))}</text>`)
  }
  out.push('</svg>')
  return out.join('\n')
}

function baseName() {
  return (props.data?.title || 'mindmap').replace(/[\\/:*?"<>|\s]+/g, '_').slice(0, 40) || 'mindmap'
}

function triggerDownload(blob, filename) {
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename
  a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 1000)
}

function downloadSvg() {
  triggerDownload(new Blob([buildSvgString()], { type: 'image/svg+xml;charset=utf-8' }),
    `${baseName()}.svg`)
}

function downloadPng() {
  const svg = buildSvgString()
  const { width, height } = tree.value
  const scale = 2  // 2 倍清晰度
  const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml;charset=utf-8' }))
  const img = new Image()
  img.onload = () => {
    const canvas = document.createElement('canvas')
    canvas.width = width * scale
    canvas.height = height * scale
    const ctx = canvas.getContext('2d')
    ctx.fillStyle = '#fff'
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
    URL.revokeObjectURL(url)
    canvas.toBlob(b => b && triggerDownload(b, `${baseName()}.png`), 'image/png')
  }
  img.onerror = () => URL.revokeObjectURL(url)
  img.src = url
}
</script>

<template>
  <div class="mindmap-wrap">
    <!-- 工具条 -->
    <div class="mm-toolbar">
      <button class="mm-tool-btn" :title="t('mm.fullscreen')" @click="openFullscreen">⛶ {{ t('mm.fullscreen') }}</button>
      <button class="mm-tool-btn" title="PNG" @click="downloadPng">PNG</button>
      <button class="mm-tool-btn" title="SVG" @click="downloadSvg">SVG</button>
    </div>

    <div class="mm-scroll">
      <svg :width="tree.width" :height="tree.height" :viewBox="`0 0 ${tree.width} ${tree.height}`" class="mindmap-svg">
        <path
          v-for="(l, i) in tree.links"
          :key="i"
          :d="`M ${l.x1} ${l.y1} C ${(l.x1 + l.x2) / 2} ${l.y1}, ${(l.x1 + l.x2) / 2} ${l.y2}, ${l.x2} ${l.y2}`"
          style="fill: none; stroke: var(--border, #dbe8e5); stroke-width: 1.5"
        />
        <g v-for="n in tree.nodes" :key="n.id" :transform="`translate(${n.x}, ${n.y})`">
          <rect
            :width="n.isRoot ? dims.ROOT_W : dims.NODE_W"
            :height="dims.NODE_H"
            rx="8"
            :style="n.isRoot
              ? { fill: 'var(--primary, #0d9488)' }
              : { fill: 'var(--primary-bg, #e6f5f2)', stroke: 'var(--primary, #0d9488)', strokeWidth: '1px' }"
          />
          <text :x="(n.isRoot ? dims.ROOT_W : dims.NODE_W) / 2" :y="dims.NODE_H / 2 + 1" text-anchor="middle" dominant-baseline="middle" :style="n.isRoot
            ? { fill: '#fff', fontSize: '13px', fontWeight: 600 }
            : { fill: 'var(--text-1, #152523)', fontSize: '12.5px' }">
            {{ short(n.title) }}
          </text>
        </g>
      </svg>
    </div>

    <!-- 全屏覆盖层 -->
    <div v-if="fullscreen" class="mm-overlay">
      <div class="mm-overlay-head">
        <span class="mm-overlay-title">{{ data.title }}</span>
        <div class="mm-overlay-actions">
          <button class="mm-tool-btn" @click="fitView = !fitView">{{ fitView ? t('mm.actual') : t('mm.fit') }}</button>
          <button class="mm-tool-btn" @click="downloadPng">PNG</button>
          <button class="mm-tool-btn" @click="downloadSvg">SVG</button>
          <button class="mm-tool-btn mm-close" @click="closeFullscreen">{{ t('mm.close') }}</button>
        </div>
      </div>
      <div class="mm-overlay-body" :class="{ scroll: !fitView }">
        <svg
          :width="fitView ? '100%' : tree.width"
          :height="fitView ? '100%' : tree.height"
          :viewBox="`0 0 ${tree.width} ${tree.height}`"
          preserveAspectRatio="xMidYMid meet"
          class="mm-overlay-svg"
        >
          <path
            v-for="(l, i) in tree.links"
            :key="i"
            :d="`M ${l.x1} ${l.y1} C ${(l.x1 + l.x2) / 2} ${l.y1}, ${(l.x1 + l.x2) / 2} ${l.y2}, ${l.x2} ${l.y2}`"
            style="fill: none; stroke: var(--border, #dbe8e5); stroke-width: 1.5"
          />
          <g v-for="n in tree.nodes" :key="n.id" :transform="`translate(${n.x}, ${n.y})`">
            <rect
              :width="n.isRoot ? dims.ROOT_W : dims.NODE_W"
              :height="dims.NODE_H"
              rx="8"
              :style="n.isRoot
                ? { fill: 'var(--primary, #0d9488)' }
                : { fill: 'var(--primary-bg, #e6f5f2)', stroke: 'var(--primary, #0d9488)', strokeWidth: '1px' }"
            />
            <text :x="(n.isRoot ? dims.ROOT_W : dims.NODE_W) / 2" :y="dims.NODE_H / 2 + 1" text-anchor="middle" dominant-baseline="middle" :style="n.isRoot
              ? { fill: '#fff', fontSize: '13px', fontWeight: 600 }
              : { fill: 'var(--text-1, #152523)', fontSize: '12.5px' }">
              {{ short(n.title) }}
            </text>
          </g>
        </svg>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mm-toolbar { display: flex; justify-content: flex-end; gap: 6px; margin-bottom: 6px; }
.mm-tool-btn {
  border: 1px solid var(--border, #dbe8e5);
  background: var(--bg-elevated, #fff);
  color: var(--text-2, #4e5969);
  font-size: 12px;
  padding: 4px 10px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.12s;
}
.mm-tool-btn:hover { color: var(--primary, #0d9488); border-color: var(--primary, #0d9488); }
.mm-scroll { overflow: auto; }
.mindmap-svg { display: block; }
/* 节点/连线颜色用内联 style 绑定（vite 8 rolldown 会裁剪只出现在动态 :class 里的规则） */

/* 覆盖层全屏（浅色模式保持白色主题；深色模式跟随 --bg-page 变量切换） */
.mm-overlay {
  position: fixed;
  inset: 0;
  z-index: 200;
  background: var(--bg-page, rgba(255, 255, 255, 0.98));
  display: flex;
  flex-direction: column;
}
.mm-overlay-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 18px;
  flex-shrink: 0;
  border-bottom: 1px solid var(--border, #dbe8e5);
}
.mm-overlay-title {
  color: var(--text-1, #152523);
  font-size: 15px;
  font-weight: 600;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mm-overlay-actions { display: flex; gap: 8px; flex-shrink: 0; }
.mm-close { margin-left: 8px; }
.mm-overlay-body {
  flex: 1;
  min-height: 0;
  padding: 8px 18px 20px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.mm-overlay-body.scroll { display: block; overflow: auto; }
.mm-overlay-svg { display: block; }</style>
