import React, { useState, useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getBot, getStrategies, updateBot, rankAssets, stopBot, optimizeBotParams, getBots } from '../api'
import { ArrowLeft, Save, Loader2, Sparkles, List, CheckCircle2, AlertTriangle, Wand2 } from 'lucide-react'
import { useLanguage } from '../i18n/LanguageContext'
import AiUsageNotice from '../components/AiUsageNotice'

const SYMBOLS    = [
  'AAPL', 'MSFT', 'NVDA', 'AMZN', 'META', 'TSLA', 'GOOGL', 'NFLX', 'AMD',
  'BTC/USD', 'ETH/USD', 'LTC/USD', 'BCH/USD', 'SOL/USD', 'UNI/USD', 'LINK/USD', 'DOGE/USD'
]
const TIMEFRAMES = ['1m','5m','15m','1h','4h','1D']
const MEDALS     = ['🥇','🥈','🥉','','','','','','','']
const FIXED_STAKE_USD = 100

const LABEL_COLORS = {
  Excelente: 'text-green-400',
  Bom:       'text-blue-400',
  Moderado:  'text-yellow-400',
  Fraco:     'text-muted',
}

function AssetCard({ asset, selected, onSelect, disabled }) {
  const details = asset.details ?? {}
  const [baseSymbol, ...quoteParts] = asset.symbol.split('-')
  const suffix = quoteParts.join('-')
  return (
    <button
      type="button"
      onClick={() => !disabled && onSelect(asset.symbol)}
      disabled={disabled}
      className={`w-full text-left p-3 rounded-lg border transition-all ${
        disabled
          ? 'border-border opacity-40 cursor-not-allowed'
          : selected
          ? 'border-accent bg-accent/10'
          : 'border-border hover:border-muted/60'
      }`}
    >
      <div className="flex items-center justify-between">
        <span className={`text-sm font-medium ${disabled ? 'text-muted' : ''}`}>
          {MEDALS[asset.rank - 1]} {baseSymbol}{suffix ? `-${suffix}` : ''}
        </span>
        {disabled
          ? <span className="text-[10px] font-bold text-muted bg-white/5 px-1.5 py-0.5 rounded border border-white/10">EM USO</span>
          : <span className={`text-xs font-semibold ${LABEL_COLORS[asset.label] ?? 'text-muted'}`}>{asset.label}</span>
        }
      </div>
      <div className="mt-2 h-1 bg-border rounded-full">
        <div
          className="h-1 rounded-full"
          style={{
            width: `${Math.round(asset.score * 100)}%`,
            backgroundColor: disabled ? '#374151' : selected ? 'var(--color-accent, #22c55e)' : '#4b5563',
          }}
        />
      </div>
    </button>
  )
}

export default function EditBot() {
  const { id } = useParams()
  const nav = useNavigate()
  const qc  = useQueryClient()
  const { t } = useLanguage()

  const { data: bot, isLoading: loadingBot } = useQuery({
    queryKey: ['bot', id],
    queryFn:  () => getBot(id)
  })

  const { data: strategies = [] } = useQuery({
    queryKey: ['strategies'],
    queryFn:  getStrategies,
  })

  const { data: allBots = [] } = useQuery({
    queryKey: ['bots'],
    queryFn:  getBots,
  })
  const usedSymbols = new Set(allBots.filter(b => b.id !== parseInt(id)).map(b => b.symbol))

  const [form, setForm] = useState(null)
  const [assetMode, setAssetMode] = useState('manual')
  const [ranking, setRanking] = useState(null)
  const [rankLoading, setRankLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [optimizing, setOptimizing] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (bot && !form) {
      setForm({
        name: bot.name,
        symbol: bot.symbol,
        timeframe: bot.timeframe,
        stake_usd: FIXED_STAKE_USD,
        leverage: 1,
        stop_loss_usd: bot.stop_loss_usd,
        strategy_params: bot.strategy_params || {},
        demo: bot.demo
      })
    }
  }, [bot, form])

  if (loadingBot || !form) return <div className="p-6 text-muted">Carregando configurações...</div>

  const selectedStrategy = strategies.find(s => s.id === bot.strategy_id)

  function setField(k, v) { setForm(f => ({ ...f, [k]: v })) }

  async function fetchRanking() {
    setRankLoading(true)
    setAssetMode('auto')
    try {
      const data = await rankAssets(bot.strategy_id, form.timeframe)
      setRanking(data)
      if (data.assets?.length > 0) setField('symbol', data.assets[0].symbol)
    } catch (err) {
      setError("Erro ao analisar ativos: " + err.message)
      setAssetMode('manual')
    } finally {
      setRankLoading(false)
    }
  }

  async function handleOptimize() {
    setOptimizing(true)
    setError('')
    try {
      const data = await optimizeBotParams(id)
      if (data.best_params) {
        setField('strategy_params', data.best_params)
      } else if (data.error) {
        setError(data.error)
      }
    } catch (err) {
      setError("Falha na otimização: " + err.message)
    } finally {
      setOptimizing(false)
    }
  }

  async function submit(e) {
    e.preventDefault()
    setSaving(true)
    setError('')
    try {
      if (bot.active) {
        await stopBot(id)
      }
      await updateBot(id, { ...form, stake_usd: FIXED_STAKE_USD })
      qc.invalidateQueries({ queryKey: ['bot', id] })
      qc.invalidateQueries({ queryKey: ['bots'] })
      nav(`/bots/${id}`)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="p-6 max-w-2xl mx-auto space-y-6">
      <div className="flex items-center gap-3">
        <button onClick={() => nav(-1)} className="btn-ghost btn p-2"><ArrowLeft size={16} /></button>
        <div>
          <h1 className="text-xl font-bold">Reconfigurar {bot.name}</h1>
          <p className="text-muted text-sm">{bot.strategy_id} · {bot.symbol}</p>
        </div>
      </div>

      <form onSubmit={submit} className="space-y-5">
        <div className="card space-y-4">
          <h2 className="font-semibold text-sm">Configurações Gerais</h2>
          <div>
            <label className="label">Nome do Robô</label>
            <input className="input" value={form.name} onChange={e => setField('name', e.target.value)} />
          </div>
          <div>
            <label className="label">Timeframe</label>
            <select className="input" value={form.timeframe} onChange={e => setField('timeframe', e.target.value)}>
              {TIMEFRAMES.map(tf => <option key={tf}>{tf}</option>)}
            </select>
          </div>
        </div>

        <div className="card space-y-4">
          <h2 className="font-semibold text-sm">Escolha do Ativo</h2>
          {assetMode === 'manual' ? (
            <div className="space-y-3">
              <AiUsageNotice consumesTokens={false} text="Escolha automática de ativo usa ranking local de mercado. Não consome tokens de IA." />
              <select className="input" value={form.symbol} onChange={e => setField('symbol', e.target.value)}>
                <optgroup label="Ações (Stocks)">
                  {[['AAPL','Apple'],['MSFT','Microsoft'],['NVDA','NVIDIA'],['AMZN','Amazon'],['META','Meta Platforms'],['TSLA','Tesla'],['GOOGL','Alphabet'],['NFLX','Netflix'],['AMD','Advanced Micro Devices']].map(([sym, name]) => (
                    <option key={sym} value={sym} disabled={usedSymbols.has(sym)}>
                      {sym} ({name}){usedSymbols.has(sym) ? ' — em uso' : ''}
                    </option>
                  ))}
                </optgroup>
                <optgroup label="Criptomoedas (Crypto)">
                  {[['BTC/USD','Bitcoin'],['ETH/USD','Ethereum'],['LTC/USD','Litecoin'],['BCH/USD','Bitcoin Cash'],['SOL/USD','Solana'],['UNI/USD','Uniswap'],['LINK/USD','Chainlink'],['DOGE/USD','Dogecoin']].map(([sym, name]) => (
                    <option key={sym} value={sym} disabled={usedSymbols.has(sym)}>
                      {sym} ({name}){usedSymbols.has(sym) ? ' — em uso' : ''}
                    </option>
                  ))}
                </optgroup>
              </select>
              <button type="button" onClick={fetchRanking} className="btn-ghost btn w-full flex items-center justify-center gap-2 text-xs">
                <Sparkles size={14} className="text-accent" /> Usar Inteligência para escolher ativo
              </button>
            </div>
          ) : (
            <div className="space-y-3">
              {rankLoading ? (
                <div className="flex items-center justify-center gap-2 text-muted text-sm py-4"><Loader2 size={16} className="animate-spin" /> Analisando mercado...</div>
              ) : (
                <>
                  <div className="space-y-2">
                    {ranking?.assets.map(asset => (
                      <AssetCard key={asset.symbol} asset={asset} selected={form.symbol === asset.symbol} onSelect={sym => setField('symbol', sym)} disabled={usedSymbols.has(asset.symbol)} />
                    ))}
                  </div>
                  <button type="button" onClick={() => setAssetMode('manual')} className="text-xs text-muted hover:text-white">Voltar para escolha manual</button>
                </>
              )}
            </div>
          )}
        </div>

        <div className="card space-y-4">
          <h2 className="font-semibold text-sm">Gestão de Risco</h2>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="label">Stake</label>
              <div className="input flex items-center font-mono font-semibold text-white">$100</div>
            </div>
            <div><label className="label">Stop Diário</label><input className="input" type="number" value={form.stop_loss_usd} onChange={e => setField('stop_loss_usd', Number(e.target.value))} /></div>
          </div>
          <p className="text-xs text-muted">Spot sem alavancagem: toda entrada usa stake fixo de US$100.</p>
        </div>

        {selectedStrategy && Object.keys(selectedStrategy.params).length > 0 && (
          <div className="card space-y-4 relative overflow-hidden">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold text-sm">Parâmetros de {selectedStrategy.name}</h2>
              <button 
                type="button" 
                onClick={handleOptimize}
                disabled={optimizing}
                className="btn-ghost btn btn-xs flex items-center gap-1.5 text-accent hover:bg-accent/10 border border-accent/20"
              >
                {optimizing ? <Loader2 size={12} className="animate-spin" /> : <Wand2 size={12} />}
                {optimizing ? "Otimizando..." : "Otimizar"}
              </button>
            </div>
            <AiUsageNotice consumesTokens={false} text="Otimização usa análise estatística local. Não consome tokens de IA." />
            
            {optimizing && (
              <div className="absolute inset-0 bg-panel/60 backdrop-blur-[1px] flex items-center justify-center z-10 animate-in fade-in duration-300">
                <div className="flex flex-col items-center gap-2">
                  <div className="w-8 h-8 border-4 border-accent border-t-transparent rounded-full animate-spin" />
                  <p className="text-xs font-bold text-white uppercase tracking-tighter">Buscando Melhor Setup...</p>
                </div>
              </div>
            )}

            <div className="grid grid-cols-2 gap-4">
              {Object.entries(selectedStrategy.params).map(([k, p]) => (
                <div key={k}>
                  <label className="label">{k}</label>
                  <input 
                    className="input" 
                    type={p.type === 'bool' ? 'checkbox' : 'number'} 
                    value={form.strategy_params[k] ?? p.default} 
                    onChange={e => setField('strategy_params', {...form.strategy_params, [k]: p.type === 'int' ? parseInt(e.target.value) : parseFloat(e.target.value)})} 
                  />
                  <p className="text-xs text-muted mt-1">{p.description}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        {error && <p className="text-red-400 text-sm">{error}</p>}

        <div className="flex gap-3">
          <button type="button" onClick={() => nav(-1)} className="btn-ghost btn flex-1">Cancelar</button>
          <button type="submit" disabled={saving || optimizing} className="btn-primary btn flex-1 flex items-center justify-center gap-2">
            {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
            {bot.active ? "Parar Bot e Salvar" : "Salvar Alterações"}
          </button>
        </div>
      </form>
    </div>
  )
}
