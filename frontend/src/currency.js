// 多语言价格参考换算。
// 收款始终为 CNY（Stripe Checkout 以人民币结算），此处仅做展示层参考价换算：
// rate 为 1 CNY 兑该货币的静态近似汇率（不必精确，标注"≈"），dp 为显示小数位。
// 更新方式：有需要时手动微调，或将来接入汇率接口。

export const CURRENCIES = {
  'zh-CN': null,                                 // 中文直接显示 ¥
  en: { symbol: '$', rate: 0.14, dp: 2 },        // ≈ $1.39
  ja: { symbol: '¥', rate: 21, dp: 0 },          // ≈ ¥208（日元）
  ko: { symbol: '₩', rate: 193, dp: 0 },         // ≈ ₩1,911
  es: { symbol: '€', rate: 0.13, dp: 2 },        // ≈ €1.29
  fr: { symbol: '€', rate: 0.13, dp: 2 },
}

/**
 * 返回换算后的参考价字符串（如 "≈ $1.39"）；
 * 中文或无对应币种时返回 null（不显示）。
 */
export function approx(cny, localeValue) {
  const cur = CURRENCIES[localeValue]
  if (!cur || !(cny > 0)) return null
  const val = cny * cur.rate
  const s = val.toLocaleString('en-US', {
    minimumFractionDigits: cur.dp, maximumFractionDigits: cur.dp,
  })
  return `≈ ${cur.symbol}${s}`
}
