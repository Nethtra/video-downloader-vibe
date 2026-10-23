# 极易下 · 前端

Vue 3 + Vite 单页应用，构建产物输出到 `../backend/static`，由 FastAPI 直接托管。
视觉为瑞士国际主义风格（详见 `docs/11-前端瑞士风格改版.md`）。

## 开发

```bash
npm install
npm run dev      # http://127.0.0.1:5173，/api 已代理到 127.0.0.1:8000
npm run build    # 产物写入 ../backend/static（emptyOutDir）
```

后端需先启动：`cd ../backend && python run.py`。

## 结构

```
src/
├── App.vue                # 单页应用：导航 / Hero / 工作台 / 各区块 / 弹窗
├── components/
│   └── MindMap.vue        # SVG 思维导图：全屏、PNG/SVG 导出（浏览器本地生成）
├── i18n.js                # 零依赖 i18n：zh-CN / en / ja / ko / es / fr 扁平字典
├── theme.js               # 三态主题：auto / light / dark（localStorage + prefers-color-scheme）
├── currency.js            # 套餐参考价换算（仅展示）
├── style.css              # 设计令牌、@font-face、基础排版、动效原语
└── main.js                # 挂载 + v-reveal 指令（IntersectionObserver 滚动入场）
public/
├── fonts/                 # 自托管 woff2（Inter、Instrument Serif，latin 子集）
├── favicon.svg
├── icons.svg
└── og-image.png
```

## 约定

- **样式**：组件内 `<style scoped>`；颜色/字体只引用 `style.css` 中的 CSS 变量（`--paper / --ink / --red / --rule`）
- **字体**：大标题用 `var(--font-display)`（Instrument Serif + 宋体回退），正文用 `var(--font-sans)`（Inter）
- **动效**：新增滚动入场元素加 `v-reveal`，可传延迟毫秒（`v-reveal="120"`）；不引入动画库
- **i18n**：所有用户可见文案走 `t('key')`；新增 key 必须同时补齐 6 种语言（缺失回退 zh-CN）
- **禁用**：emoji 当图标、渐变/阴影/圆角装饰（当前设计系统不允许）

## 注意

- `vite.config.js` 的 `outDir` 指向 `../backend/static`，构建会**清空该目录**，不要把需要保留的文件放在里面
- 生成的 CSS 会被 vite 8 (rolldown) 做 unused 裁剪：只出现在动态 `:class` 三元里的类可能被移除，
  状态样式请同时出现在静态 class 中，或用内联 `:style` 绑定
- 前端不直接读取任何密钥；所有敏感配置在后端 `.env`

## 字体许可

`public/fonts/` 内为 SIL Open Font License 字体子集（Inter、Instrument Serif），可随项目分发。
