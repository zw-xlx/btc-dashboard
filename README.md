# BTC 市场指标看板

暗色主题单文件 HTML 仪表盘，展示 BTC 核心市场指标。

## 文件结构

```
btc-dashboard/
├── index.html   # 单文件看板（内联 Chart.js + 所有 CSS/JS）
└── README.md
```

## 数据结构

`RAW_DATA` 数组，每日一条，格式：

```javascript
{
  date: "2026-04-15",
  btc_price: 85300,       // BTC 美元价格
  btc_change: 0.9,        // 24h 涨跌 %
  fear: 36,               // 恐惧贪婪指数 0-100
  fear_label: "恐惧",     // 标签
  ahr999: 0.61,           // AHR999 指标
  mvrv: 1.33,             // MVRV 比率
  wma200: 44950,          // 200周移动均线
  mstr_btc: 528185,       // MSTR 持仓 BTC 数量
  mstr_price: 116.80,     // MSTR 股价 USD
  mstr_mnav: 1.60,        // mNAV 倍数
  mstr_debt_ratio: 0.27,  // 负债率
  etf_btc: 1097000,       // 现货 ETF 总持仓 BTC
  usdc_mcap: 60900000000, // USDC 流通市值 USD
}
```

## 追加数据

在 `RAW_DATA` 数组末尾追加新行，**注意最后一条必须有尾逗号 `,`**。

## Sections

| Section | 内容 |
|---|---|
| 📊 今日概览 | 价格/涨跌/恐惧贪婪/AHR999/MVRV/200WMA |
| 📈 BTC 价格走势 | 折线图 + 200WMA + 周/月汇总 |
| 📉 恐惧贪婪走势 | 渐变面积图 |
| 🔢 AHR999 走势 | 折线图 + 三色区间标注 |
| 📊 MVRV 走势 | 折线图 + 参考线 |
| 🏢 MSTR 持仓 | 卡片：持仓/股价/mNAV/负债率 |
| 💰 ETF 持仓 | 卡片：总量/市值/供应占比 |
| 💵 USDC 走势 | 面积折线图 |

## 技术说明

- Chart.js v4.4.7 完整内联（中国可用）
- chartjs-adapter-date-fns v3.0.0 完整内联
- 无外部依赖，纯离线运行
