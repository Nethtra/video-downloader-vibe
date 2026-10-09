<script setup>
/**
 * 思维导图（SVG 横向树）：根节点在左，向右展开。
 * 后端已约束：最多 3 层、每层最多 5 个节点。
 */
import { computed } from 'vue'

const props = defineProps({
  data: { type: Object, required: true }, // {title, children: [{title, children}]}
})

const ROW_H = 42       // 每行高度
const COL_W = 190      // 每层列宽
const NODE_H = 30      // 节点高
const PAD = 12

// 布局：叶子节点均匀分配行，父节点取子节点行的中位数
const { nodes, links, width, height } = computed(() => {
  const nodes = []
  const links = []
  let leafRow = 0

  function walk(node, depth, parentId, forceRow = null) {
    const id = nodes.length
    const children = node.children || []
    let row
    if (!children.length) {
      row = forceRow !== null ? forceRow : leafRow++
    } else {
      const childRows = children.map((c, i) =>
        walk(c, depth + 1, id, i === 0 ? null : null).row
      )
      row = (Math.min(...childRows) + Math.max(...childRows)) / 2
    }
    nodes.push({
      id, depth, row, title: node.title,
      x: PAD + depth * COL_W,
      y: PAD + row * ROW_H,
      isRoot: depth === 0,
    })
    if (parentId !== null) links.push({ from: parentId, to: id })
    return { id, row }
  }

  walk(props.data, 0, null)

  // 行号可能出现小数（父节点中位数），按行排序重排 y 保证无重叠
  const sorted = [...nodes].sort((a, b) => a.row - b.row)
  sorted.forEach((n, i) => { n.row = i })

  const maxDepth = Math.max(...nodes.map(n => n.depth))
  const w = PAD * 2 + maxDepth * COL_W + 170
  const h = PAD * 2 + (nodes.length - 1) * ROW_H + NODE_H

  // 重新计算最终坐标
  nodes.forEach(n => {
    n.x = PAD + n.depth * COL_W
    n.y = PAD + n.row * ROW_H
  })
  const byId = Object.fromEntries(nodes.map(n => [n.id, n]))
  const finalLinks = links.map(l => ({
    x1: byId[l.from].x + 120, y1: byId[l.from].y + NODE_H / 2,
    x2: byId[l.to].x, y2: byId[l.to].y + NODE_H / 2,
  }))

  return { nodes, links: finalLinks, width: w, height: h }
}).value
</script>

<template>
  <div class="mindmap-wrap">
    <svg :width="width" :height="height" :viewBox="`0 0 ${width} ${height}`" class="mindmap-svg">
      <path
        v-for="(l, i) in links"
        :key="i"
        class="mm-link"
        :d="`M ${l.x1} ${l.y1} C ${(l.x1 + l.x2) / 2} ${l.y1}, ${(l.x1 + l.x2) / 2} ${l.y2}, ${l.x2} ${l.y2}`"
      />
      <g v-for="n in nodes" :key="n.id" :transform="`translate(${n.x}, ${n.y})`">
        <rect
          :width="n.isRoot ? 130 : 120"
          :height="NODE_H"
          rx="8"
          :class="n.isRoot ? 'mm-root' : 'mm-node'"
        />
        <text :x="n.isRoot ? 65 : 60" :y="NODE_H / 2 + 1" text-anchor="middle" dominant-baseline="middle" :class="n.isRoot ? 'mm-root-text' : 'mm-text'">
          {{ n.title.length > 8 ? n.title.slice(0, 8) + '…' : n.title }}
        </text>
      </g>
    </svg>
  </div>
</template>

<style scoped>
.mindmap-wrap { overflow-x: auto; }
.mindmap-svg { max-width: 100%; }
.mm-link { fill: none; stroke: var(--border, #e5e6eb); stroke-width: 1.5; }
.mm-root { fill: var(--primary, #3370ff); }
.mm-node { fill: var(--primary-bg, #e8efff); stroke: var(--primary, #3370ff); stroke-width: 1; }
.mm-root-text { fill: #fff; font-size: 13px; font-weight: 600; }
.mm-text { fill: var(--text-1, #1f2329); font-size: 12.5px; }
</style>
