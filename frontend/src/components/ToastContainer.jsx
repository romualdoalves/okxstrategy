import { useToastEngine } from '../hooks/useToast'

const KIND_STYLES = {
  success: {
    bar:    'bg-emerald-400',
    icon:   'bg-emerald-400/15 border-emerald-400/30',
    title:  'text-emerald-400',
    glow:   'shadow-[0_0_24px_rgba(52,211,153,0.15)]',
  },
  error: {
    bar:    'bg-red-500',
    icon:   'bg-red-500/15 border-red-500/30',
    title:  'text-red-400',
    glow:   'shadow-[0_0_24px_rgba(239,68,68,0.15)]',
  },
  info: {
    bar:    'bg-blue-400',
    icon:   'bg-blue-400/15 border-blue-400/30',
    title:  'text-blue-400',
    glow:   'shadow-[0_0_24px_rgba(96,165,250,0.15)]',
  },
  profit: {
    bar:    'bg-emerald-400',
    icon:   'bg-emerald-400/15 border-emerald-400/30',
    title:  'text-emerald-400',
    glow:   'shadow-[0_0_24px_rgba(52,211,153,0.15)]',
  },
  loss: {
    bar:    'bg-red-500',
    icon:   'bg-red-500/15 border-red-500/30',
    title:  'text-red-400',
    glow:   'shadow-[0_0_24px_rgba(239,68,68,0.15)]',
  },
  shield: {
    bar:    'bg-yellow-400',
    icon:   'bg-yellow-400/15 border-yellow-400/30',
    title:  'text-yellow-400',
    glow:   'shadow-[0_0_24px_rgba(250,204,21,0.15)]',
  },
}

function ToastItem({ toast, onDismiss }) {
  const s = KIND_STYLES[toast.kind] ?? KIND_STYLES.info

  return (
    <div
      className={`relative flex gap-3 w-80 rounded-xl border border-white/10 bg-[#0f1117]/95 backdrop-blur-md p-3.5 ${s.glow}
        animate-in slide-in-from-right-4 fade-in duration-300`}
      style={{ WebkitBackdropFilter: 'blur(16px)' }}
    >
      {/* Barra de cor lateral */}
      <div className={`absolute left-0 top-0 bottom-0 w-1 rounded-l-xl ${s.bar}`} />

      {/* Conteúdo */}
      <div className="flex-1 min-w-0 pl-1">
        <p className={`text-[11px] font-bold uppercase tracking-wider ${s.title}`}>
          {toast.title}
        </p>
        <p className="text-[13px] text-white/90 font-medium mt-0.5 truncate">
          {toast.body}
        </p>
        {toast.detail && (
          <p className="text-[11px] text-white/45 mt-0.5 font-mono truncate">
            {toast.detail}
          </p>
        )}
      </div>

      {/* Fechar */}
      <button
        onClick={() => onDismiss(toast.id)}
        className="shrink-0 text-white/30 hover:text-white/70 transition-colors text-lg leading-none mt-0.5"
        aria-label="Fechar"
      >
        ×
      </button>
    </div>
  )
}

export default function ToastContainer() {
  const { toasts, dismiss } = useToastEngine()

  if (toasts.length === 0) return null

  return (
    <div
      className="fixed bottom-5 right-5 z-[9999] flex flex-col gap-2 items-end"
      aria-live="polite"
      aria-label="Notificações"
    >
      {toasts.map(t => (
        <ToastItem key={t.id} toast={t} onDismiss={dismiss} />
      ))}
    </div>
  )
}
