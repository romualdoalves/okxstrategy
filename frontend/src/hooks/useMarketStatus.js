import { useState, useEffect } from 'react'

const OPEN_MIN  = 9 * 60 + 30   // 09:30 ET
const CLOSE_MIN = 16 * 60        // 16:00 ET
const WEEKDAYS  = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']

function getETInfo(date) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    weekday: 'short',
    hour: 'numeric',
    minute: 'numeric',
    hour12: false,
  }).formatToParts(date)
  const p = {}
  parts.forEach(({ type, value }) => { p[type] = value })
  const hour = parseInt(p.hour === '24' ? '0' : p.hour)
  const minute = parseInt(p.minute)
  return { weekday: p.weekday, totalMinutes: hour * 60 + minute }
}

function computeStatus(now = new Date()) {
  const { weekday, totalMinutes } = getETInfo(now)
  const isWeekday = WEEKDAYS.includes(weekday)
  const isOpen = isWeekday && totalMinutes >= OPEN_MIN && totalMinutes < CLOSE_MIN

  let minsUntilOpen = 0
  if (!isOpen) {
    if (isWeekday && totalMinutes < OPEN_MIN) {
      minsUntilOpen = OPEN_MIN - totalMinutes
    } else {
      // After close or weekend — find next weekday 09:30 ET
      let daysToAdd = 1
      for (let i = 1; i <= 7; i++) {
        const next = new Date(now.getTime() + i * 24 * 60 * 60 * 1000)
        if (WEEKDAYS.includes(getETInfo(next).weekday)) { daysToAdd = i; break }
      }
      const minsUntilMidnight = 24 * 60 - totalMinutes
      minsUntilOpen = minsUntilMidnight + (daysToAdd - 1) * 24 * 60 + OPEN_MIN
    }
  }

  const h = Math.floor(minsUntilOpen / 60)
  const m = minsUntilOpen % 60
  const opensIn = h > 0 ? `${h}h ${m < 10 ? '0' + m : m}min` : `${m}min`

  return { isOpen, opensIn: isOpen ? null : opensIn }
}

export function useMarketStatus() {
  const [status, setStatus] = useState(() => computeStatus())
  useEffect(() => {
    const id = setInterval(() => setStatus(computeStatus()), 60_000)
    return () => clearInterval(id)
  }, [])
  return status
}
