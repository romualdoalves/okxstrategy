import React, { useState, useEffect } from 'react'
import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import {
  LayoutDashboard, Bot, Zap, BookText, TrendingUp, Trophy, Plug, CheckCircle2, FlaskConical, MonitorIcon, ScanSearch, Database, ShieldCheck
} from 'lucide-react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { LanguageProvider, useLanguage } from './i18n/LanguageContext'
import Dashboard    from './pages/Dashboard'
import Bots         from './pages/Bots'
import NewBot       from './pages/NewBot'
import EditBot      from './pages/EditBot'
import BotDetail    from './pages/BotDetail'
import Strategies   from './pages/Strategies'
import Registros    from './pages/Registros'
import Performance  from './pages/Performance'
import TradeReport  from './pages/TradeReport'
import StrategyFactory  from './pages/StrategyFactory'
import Monitor          from './pages/Monitor'
import BatchBacktest    from './pages/BatchBacktest'
import MarketData       from './pages/MarketData'
import Integrity        from './pages/Integrity'
import ErrorBoundary   from './components/ErrorBoundary'
import ToastContainer  from './components/ToastContainer'
import OkxConnectModal from './components/OkxConnectModal'
import { disconnectOkxAccount, getOkxStatus, getHealth } from './api'
import { toast } from './hooks/useToast'

function AppShell() {
  const { lang, setLang, t } = useLanguage()
  const qc = useQueryClient()
  const [now, setNow] = useState(new Date())
  const [showOkxModal, setShowOkxModal] = useState(false)

  const { data: okxStatus } = useQuery({
    queryKey: ['okx-status'],
    queryFn:  getOkxStatus,
    refetchInterval: 60_000,
    retry: false,
  })

  const { data: healthData } = useQuery({
    queryKey: ['system-health'],
    queryFn:  getHealth,
    refetchInterval: 60_000,
    retry: false,
  })

  async function handleOkxButton() {
    if (!okxStatus?.configured) {
      setShowOkxModal(true)
      return
    }
    const ok = window.confirm(t('okx.disconnect_confirm'))
    if (!ok) return
    try {
      await disconnectOkxAccount()
      qc.invalidateQueries({ queryKey: ['okx-status'] })
      qc.invalidateQueries({ queryKey: ['bots'] })
      toast({ kind: 'success', title: t('okx.disconnected_title'), body: t('okx.disconnected_body') })
    } catch (err) {
      toast({ kind: 'error', title: 'OKX', body: err.message })
    }
  }

  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(timer)
  }, [])

  const getDayOfYear = (date) => {
    const start = new Date(Date.UTC(date.getUTCFullYear(), 0, 0))
    const diff = date - start
    const oneDay = 1000 * 60 * 60 * 24
    return Math.floor(diff / oneDay)
  }

  const getWeekOfYear = (date) => {
    const d = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()))
    const dayNum = d.getUTCDay() || 7
    d.setUTCDate(d.getUTCDate() + 4 - dayNum)
    const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1))
    return Math.ceil((((d - yearStart) / 86400000) + 1) / 7)
  }

  const nav = [
    { to: '/',                  labelKey: 'nav.dashboard',   icon: LayoutDashboard },
    { to: '/bots',              labelKey: 'nav.bots',        icon: Bot },
    { to: '/strategies',        labelKey: 'nav.strategies',  icon: Zap },
    { to: '/strategy-factory',  labelKey: 'nav.factory',     icon: FlaskConical },
    { to: '/monitor',           labelKey: 'nav.monitor',     icon: MonitorIcon },
    { to: '/batch-backtest',    labelKey: 'nav.scanner',     icon: ScanSearch },
    { to: '/market-data',       labelKey: 'MarketData',      icon: Database },
    { to: '/registros',         labelKey: 'nav.registros',   icon: BookText },
    { to: '/performance',       labelKey: 'nav.performance', icon: Trophy },
    { to: '/integridade',       labelKey: 'nav.integrity',   icon: ShieldCheck },
  ]

  return (
    <>
      <div className="flex h-screen overflow-hidden">

      {/* Sidebar */}
      <aside className="w-56 bg-panel border-r border-border flex flex-col shrink-0">
        {/* Logo */}
        <div className="flex items-center gap-2 px-5 py-5 border-b border-border">
          <TrendingUp size={22} className="text-accent shrink-0" />
          <div className="flex flex-col">
            <span className="font-bold text-base tracking-tight leading-tight">{t('app.brand')}</span>
            {healthData?.version && (
              <span className="text-[10px] text-muted/50 font-mono leading-tight tracking-wider">
                v{healthData.version}
                {healthData.timestamp && ` • ${new Date(healthData.timestamp * 1000).toLocaleString(lang, { 
                  month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' 
                })}`}
              </span>
            )}
          </div>
        </div>

        {/* Nav */}
        <nav className="flex-1 p-3 space-y-1">
          {nav.map(({ to, labelKey, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors
                 ${isActive
                   ? 'bg-accent/15 text-accent'
                   : 'text-muted hover:text-white hover:bg-border'}`
              }
            >
              <Icon size={17} />
              {t(labelKey)}
            </NavLink>
          ))}
        </nav>

        {/* Footer — idioma + relógio + copyright */}
        <div className="p-4 border-t border-border space-y-4">
          {/* Relógio Digital Estilizado */}
          <div className="bg-black/20 rounded-xl p-3 border border-white/5 shadow-inner space-y-2">
            <div className="flex justify-between items-end">
              <p className="text-accent text-2xl font-mono font-bold leading-none tracking-tighter">
                {now.toLocaleTimeString(undefined, { hour12: false, timeZone: 'UTC' })}
              </p>
              <p className="text-[10px] text-white font-mono font-bold mb-0.5">
                W{getWeekOfYear(now)} UTC
              </p>
            </div>

            <div className="flex justify-between items-center border-t border-white/5 pt-2">
              <p className="text-[10px] text-white font-bold uppercase">
                {now.toLocaleDateString(lang, { weekday: 'long', timeZone: 'UTC' })}
              </p>
              <p className="text-[10px] text-white font-bold uppercase tracking-tighter">
                DOY {getDayOfYear(now)}
              </p>
            </div>

            <p className="text-[10px] text-white font-bold tracking-widest">
              {now.toLocaleDateString(undefined, { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC' })}
            </p>
          </div>

          {/* Botão OKX Connect */}
          <button
            onClick={handleOkxButton}
            className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium transition-all border
              ${okxStatus?.configured
                ? 'bg-bull/10 border-bull/25 text-bull hover:bg-bull/20'
                : 'bg-accent/10 border-accent/25 text-accent hover:bg-accent/20'
              }`}
          >
            {okxStatus?.configured
              ? <CheckCircle2 size={13} className="shrink-0" />
              : <Plug size={13} className="shrink-0" />
            }
            <span className="flex-1 text-left truncate">
              {okxStatus?.configured ? t('okx.disconnect_btn') : t('okx.connect_btn')}
            </span>
            {okxStatus?.configured && okxStatus?.equity != null && (
              <span className="font-mono text-[10px] text-bull/70 shrink-0">
                {okxStatus.equity.toFixed(0)} USDT
              </span>
            )}
          </button>

          {/* Seletor de idioma */}
          <div className="flex items-center gap-1">
            {[['pt-BR', 'PT'], ['en-US', 'EN']].map(([code, label]) => (
              <button
                key={code}
                onClick={() => setLang(code)}
                className={`text-xs px-2.5 py-1 rounded font-medium transition-colors
                  ${lang === code
                    ? 'bg-accent/20 text-accent'
                    : 'text-muted hover:text-white hover:bg-border'}`}
              >
                {label}
              </button>
            ))}
          </div>
          <p className="text-xs text-muted/60">{t('app.footer')}</p>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 overflow-y-auto bg-surface">
        <ErrorBoundary>
          <Routes>
            <Route path="/"               element={<Dashboard />} />
            <Route path="/bots"           element={<Bots />} />
            <Route path="/bots/new"       element={<NewBot />} />
            <Route path="/bots/:id/edit"  element={<EditBot />} />
            <Route path="/bots/:id"       element={<ErrorBoundary><BotDetail /></ErrorBoundary>} />
            <Route path="/bots/:id/report" element={<ErrorBoundary><TradeReport /></ErrorBoundary>} />
            <Route path="/strategies"     element={<Strategies />} />
            <Route path="/registros"      element={<Registros />} />
            <Route path="/trades"         element={<Registros />} />
            <Route path="/activities"     element={<Registros />} />
            <Route path="/performance"    element={<Performance />} />
            <Route path="/strategy-factory" element={<StrategyFactory />} />
            <Route path="/monitor"          element={<Monitor />} />
            <Route path="/batch-backtest"   element={<BatchBacktest />} />
            <Route path="/market-data"      element={<MarketData />} />
            <Route path="/integridade"      element={<Integrity />} />
          </Routes>
        </ErrorBoundary>
      </main>

    </div>
    {showOkxModal && <OkxConnectModal onClose={() => setShowOkxModal(false)} />}
    <ToastContainer />
    </>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <LanguageProvider>
        <AppShell />
      </LanguageProvider>
    </BrowserRouter>
  )
}
