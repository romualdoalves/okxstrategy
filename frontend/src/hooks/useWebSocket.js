import { useEffect, useRef, useState } from 'react'

export function useWebSocket() {
  const [lastMsg, setLastMsg] = useState(null)
  const wsRef = useRef(null)

  useEffect(() => {
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
    const url      = `${protocol}://${location.host}/ws`

    function connect() {
      const ws = new WebSocket(url)
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
