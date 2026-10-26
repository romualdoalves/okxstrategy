import { useEffect, useRef, useState } from 'react'
import { wsUrl } from '../auth'

export function useWebSocket() {
  const [lastMsg, setLastMsg] = useState(null)
  const wsRef = useRef(null)

  useEffect(() => {
    function connect() {
      const ws = new WebSocket(wsUrl())
      wsRef.current = ws

      ws.onmessage = (e) => {
        try { setLastMsg(JSON.parse(e.data)) }
        catch {}
      }
      ws.onclose = () => {
        setTimeout(connect, 3000)   // reconecta em 3s
      }
    }
    connect()
    return () => wsRef.current?.close()
  }, [])

  return lastMsg
}
