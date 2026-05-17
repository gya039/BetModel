import { useRef, useEffect } from 'react'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Filler,
  Tooltip,
} from 'chart.js'
import { Line } from 'react-chartjs-2'
import { fmtMoney, fmtSignedMoney, toFiniteNumber } from '../utils/format'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Filler, Tooltip)

export default function BankrollChart({ history, startBankroll = 500 }) {
  const chartRef = useRef(null)

  if (!history || history.length === 0) {
    return (
      <div className="de-chart-wrap" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 180 }}>
        <span style={{ color: 'var(--muted)', fontSize: 13 }}>No bankroll data yet</span>
      </div>
    )
  }

  const cleanHistory = history.filter(r => toFiniteNumber(r.bankroll) != null)
  if (cleanHistory.length === 0) {
    return (
      <div className="de-chart-wrap" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 180 }}>
        <span style={{ color: 'var(--muted)', fontSize: 13 }}>No bankroll data yet</span>
      </div>
    )
  }

  // Show at most last 60 data points for performance
  const display = cleanHistory.length > 60 ? cleanHistory.slice(-60) : cleanHistory

  const labels  = display.map(r => formatLabel(r.date))
  const values  = display.map(r => toFiniteNumber(r.bankroll))
  const current = values[values.length - 1]
  const pnl     = current - startBankroll
  const pnlPos  = pnl >= 0

  const mainColor  = pnlPos ? '#22c55e' : '#ff4f7b'
  const glowColor  = pnlPos ? 'rgba(34,197,94,0.20)' : 'rgba(255,79,123,0.16)'

  function getGradient(ctx, chartArea) {
    const gradient = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom)
    gradient.addColorStop(0, pnlPos ? 'rgba(34,197,94,0.30)' : 'rgba(255,79,123,0.24)')
    gradient.addColorStop(0.6, pnlPos ? 'rgba(59,130,246,0.08)' : 'rgba(255,79,123,0.05)')
    gradient.addColorStop(1, 'rgba(0,0,0,0)')
    return gradient
  }

  const data = {
    labels,
    datasets: [
      {
        data: values,
        borderColor: mainColor,
        borderWidth: 2,
        fill: true,
        backgroundColor: ctx => {
          const chart = ctx.chart
          if (!chart.chartArea) return glowColor
          return getGradient(chart.ctx, chart.chartArea)
        },
        tension: 0.4,
        pointRadius: 0,
        pointHoverRadius: 5,
        pointHoverBackgroundColor: mainColor,
        pointHoverBorderColor: '#07060d',
        pointHoverBorderWidth: 2,
      },
    ],
  }

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'nearest', axis: 'x', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: '#0a0f1c',
        borderColor: '#2563eb',
        borderWidth: 1,
        titleColor: '#91a0b8',
        bodyColor: '#e7edf7',
        titleFont: { family: 'Inter', size: 11 },
        bodyFont: { family: 'JetBrains Mono', size: 13, weight: '600' },
        padding: 10,
        callbacks: {
          title: items => items[0].label,
          label: item => fmtMoney(item.raw),
        },
      },
    },
    scales: {
      x: {
        grid: { display: false },
        border: { display: false },
        ticks: {
          color: '#5a6a82',
          font: { family: 'Inter', size: 10 },
          maxTicksLimit: 6,
          maxRotation: 0,
        },
      },
      y: {
        grid: { color: 'rgba(36,50,77,0.5)', drawTicks: false },
        border: { display: false, dash: [4, 4] },
        ticks: {
          color: '#5a6a82',
          font: { family: 'JetBrains Mono', size: 11 },
          callback: v => fmtMoney(v, 0),
          maxTicksLimit: 5,
        },
      },
    },
  }

  const pnlStr = fmtSignedMoney(pnl)

  return (
    <div className="de-chart-wrap">
      <div className="de-chart-wrap__header">
        <span className="de-chart-wrap__title">Bankroll</span>
        <span
          className="de-chart-wrap__sub"
          style={{ color: pnlPos ? 'var(--bet)' : 'var(--skip)' }}
        >
          {pnlStr} from {fmtMoney(startBankroll, 0)}
        </span>
      </div>
      <div style={{ height: 180 }}>
        <Line ref={chartRef} data={data} options={options} />
      </div>
    </div>
  )
}

function formatLabel(dateStr) {
  if (!dateStr) return ''
  const dt = new Date(dateStr + 'T12:00:00')
  return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}
