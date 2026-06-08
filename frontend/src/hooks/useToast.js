import { useEffect, useRef, useState, useCallback } from 'react'

let _addToast = null

// Registra o handler global — chamado uma única vez pelo ToastContainer
export function _registerToastHandler(fn) {
  _addToast = fn
}

// API pública para qualquer componente disparar um toast
export function toast(msg) {
  if (_addToast) _addToast(msg)
}

/**
 * Hook interno usado pelo ToastContainer.
 * Escuta os eventos do WebSocket e transforma em toasts.
 */
export function useToastEngine() {
  const [toasts, setToasts] = useState([])
  const counterRef = useRef(0)

  const add = useCallback((msg) => {
    const id = ++counterRef.current
    setToasts(prev => [...prev, { id, ...msg }])
    // Auto-remove após duração
    setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id))
    }, msg.duration ?? 7000)
  }, [])

  // Registra o handler global
  useEffect(() => {
    _registerToastHandler(add)
    return () => { _addToast = null }
  }, [add])

  const dismiss = useCallback((id) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  // Escuta WebSocket globalmente
  useEffect(() => {
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${protocol}://${location.host}/ws`
    let ws
    let retryTimeout

    function connect() {
      ws = new WebSocket(url)

      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data)
          handleWsEvent(msg, add)
        } catch {}
      }

      ws.onclose = () => {
        retryTimeout = setTimeout(connect, 3000)
      }
    }

    connect()
    return () => {
      ws?.close()
      clearTimeout(retryTimeout)
    }
  }, [add])

  return { toasts, dismiss }
}

// ── Mapeamento de eventos WS → toasts ─────────────────────────────────────────

function botLabel(msg) {
  return msg.bot_name ? `🤖 ${msg.bot_name}` : `Bot #${msg.bot_id}`
}

function handleWsEvent(msg, add) {
  switch (msg.type) {

    case 'order_confirmed':
      add({
        kind:    'success',
        title:   '✅ Ordem Confirmada',
        body:    `${botLabel(msg)} — ${msg.symbol} ${msg.direction?.toUpperCase()} @ $${Number(msg.filled_price).toFixed(2)}`,
        detail:  `ID: ${msg.order_id}  |  Qty: ${msg.qty}`,
        duration: 10000,
      })
      break

    case 'order_error':
      add({
        kind:    'error',
        title:   '❌ Ordem Rejeitada',
        body:    `${botLabel(msg)} — ${msg.error?.symbol ?? ''}`,
        detail:  msg.error?.message ?? 'Erro desconhecido',
        duration: 15000,
      })
      break

    case 'trade':
      if (msg.event === 'entry') {
        add({
          kind:    'info',
          title:   `${msg.direction === 'long' ? '🟢' : '🔴'} Entrada ${msg.direction?.toUpperCase()}`,
          body:    `${botLabel(msg)} — ${msg.symbol ?? ''} @ $${Number(msg.price ?? 0).toFixed(2)}`,
          detail:  `Stop: $${Number(msg.sl ?? 0).toFixed(2)}  |  TP: $${Number(msg.tp1 ?? 0).toFixed(2)}`,
          duration: 8000,
        })
      }
      if (msg.event === 'exit' || msg.event === 'stop') {
        const pnl = msg.pnl ?? 0
        add({
          kind:    pnl >= 0 ? 'profit' : 'loss',
          title:   pnl >= 0 ? '💰 Trade Fechado (Lucro)' : '📉 Trade Fechado (Perda)',
          body:    `${botLabel(msg)} — ${msg.symbol ?? ''} @ $${Number(msg.price ?? 0).toFixed(2)}`,
          detail:  `P&L: ${pnl >= 0 ? '+' : ''}$${Number(pnl).toFixed(2)}`,
          duration: 10000,
        })
      }
      if (msg.event === 'shadow_trigger') {
        add({
          kind:    'shield',
          title:   '🛡️ Dynamic Shadow Ativada',
          body:    `${botLabel(msg)} — Lucro protegido`,
          detail:  `Stop dinâmico acoplado @ $${Number(msg.price ?? 0).toFixed(2)}`,
          duration: 8000,
        })
      }
      break

    default:
      break
  }
}
