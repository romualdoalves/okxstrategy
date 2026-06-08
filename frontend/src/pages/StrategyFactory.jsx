import { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  FlaskConical, ChevronRight, ChevronLeft, Loader2,
  CheckCircle2, XCircle, AlertTriangle, Rocket,
  Code2, ClipboardList, FileText, Trash2, Eye,
} from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  factoryStatus, factoryPlan, factoryGenerate, factoryFix,
  factoryValidate, factoryDeploy, factoryList,
  factoryGetCode, factoryDisable,
} from '../api'
import { toast } from '../hooks/useToast'

// ── Stepper ────────────────────────────────────────────────────────────────────

const STEPS = [
  { id: 1, label: 'Descrever' },
  { id: 2, label: 'Revisar Plano' },
  { id: 3, label: 'Gerar Código' },
  { id: 4, label: 'Validar' },
  { id: 5, label: 'Implantar' },
]

function Stepper({ current }) {
  return (
    <div className="flex items-center gap-0 mb-8">
      {STEPS.map((step, i) => (
        <div key={step.id} className="flex items-center">
          <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-bold transition-all
            ${current === step.id ? 'bg-accent text-black' :
              current > step.id ? 'bg-bull/20 text-bull' : 'bg-border/30 text-muted'}`}>
            {current > step.id ? <CheckCircle2 size={12} /> : <span>{step.id}</span>}
            <span className="hidden sm:inline">{step.label}</span>
          </div>
          {i < STEPS.length - 1 && (
            <div className={`h-px w-6 mx-1 ${current > step.id ? 'bg-bull/40' : 'bg-border/30'}`} />
          )}
        </div>
      ))}
    </div>
  )
}

// ── Step 1: Descrever ──────────────────────────────────────────────────────────

function StepDescribe({ onNext }) {
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleAnalyze() {
    if (text.trim().length < 50) {
      toast({ kind: 'error', title: 'Descrição muito curta', body: 'Descreva a estratégia com pelo menos 50 caracteres.' })
      return
    }
    setLoading(true)
    try {
      const data = await factoryPlan(text)
      onNext(text, data.plan)
    } catch (err) {
      toast({ kind: 'error', title: 'Erro ao gerar plano', body: err.message })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h2 className="text-lg font-bold text-white mb-1">Descreva a estratégia</h2>
        <p className="text-sm text-muted">
          Explique a lógica operacional, os indicadores, condições de entrada/saída e gestão de risco.
          Quanto mais detalhada a descrição, melhor o plano gerado.
        </p>
      </div>
      <textarea
        value={text}
        onChange={e => setText(e.target.value)}
        placeholder="Ex: Estratégia de cruzamento de médias com confirmação de volume e RSI. Comprar quando EMA9 cruzar acima da EMA21, RSI entre 50 e 70 e volume 1.5x acima da média. Stop na mínima do candle anterior, alvo 2:1..."
        className="w-full h-64 bg-black/30 border border-border rounded-xl p-4 text-sm text-white placeholder:text-muted/50 resize-y font-mono focus:outline-none focus:border-accent/50"
      />
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted">{text.length} caracteres</span>
        <button
          onClick={handleAnalyze}
          disabled={loading || text.trim().length < 50}
          className="flex items-center gap-2 px-6 py-2.5 bg-accent text-black rounded-lg text-sm font-bold hover:bg-accent/80 disabled:opacity-40 transition-all"
        >
          {loading ? <Loader2 size={15} className="animate-spin" /> : <FlaskConical size={15} />}
          {loading ? 'Analisando com KIMI...' : 'Analisar com IA'}
          {!loading && <ChevronRight size={15} />}
        </button>
      </div>
    </div>
  )
}

// ── Step 2: Revisar Plano ──────────────────────────────────────────────────────

function PlanField({ label, value, onChange, type = 'text', monospace = false }) {
  return (
    <div className="space-y-1">
      <label className="text-[10px] font-bold text-muted uppercase tracking-wider">{label}</label>
      <input
        type={type}
        value={value ?? ''}
        onChange={e => onChange(e.target.value)}
        className={`w-full bg-black/30 border border-border rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent/50 ${monospace ? 'font-mono' : ''}`}
      />
    </div>
  )
}

// Campo editável para parâmetro de risco (lê e escreve dentro de plan.params)
function RiskParamField({ label, tooltip, paramName, params, onChange, suffix, accent = '' }) {
  const param = params?.find(p => p.name === paramName)
  if (!param) return null

  const accentClass = accent === 'bear' ? 'text-bear' : accent === 'bull' ? 'text-bull' : accent === 'accent' ? 'text-accent' : 'text-white'

  function handleChange(val) {
    const num = param.type === 'int' ? parseInt(val) : parseFloat(val)
    if (isNaN(num)) return
    const updated = params.map(p => p.name === paramName ? { ...p, default: num } : p)
    onChange(updated)
  }

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-1.5">
        <label className="text-[10px] font-bold text-muted uppercase tracking-wider">{label}</label>
        {tooltip && (
          <span className="text-[10px] text-muted/50 cursor-help" title={tooltip}>ⓘ</span>
        )}
      </div>
      <div className="flex items-center gap-2 bg-black/30 border border-border rounded-lg px-3 py-2">
        <input
          type="number"
          value={param.default ?? ''}
          step={param.step ?? 1}
          min={param.min}
          max={param.max}
          onChange={e => handleChange(e.target.value)}
          className={`flex-1 bg-transparent text-sm font-mono font-bold ${accentClass} focus:outline-none w-16`}
        />
        <span className="text-[10px] text-muted shrink-0">{suffix}</span>
      </div>
      {param.min != null && param.max != null && (
        <p className="text-[9px] text-muted/50">min {param.min} · max {param.max}</p>
      )}
    </div>
  )
}


function StepReviewPlan({ plan, onNext, onBack, loading }) {
  const [p, setP] = useState(plan)

  const update = (key, val) => setP(prev => ({ ...prev, [key]: val }))

  const TF_OPTIONS = ['1m','3m','5m','15m','30m','1h','2h','4h','6h','1d','1w']

  return (
    <div className="space-y-6 max-w-4xl">
      <div>
        <h2 className="text-lg font-bold text-white mb-1">Revisar o Plano</h2>
        <p className="text-sm text-muted">
          Verifique a interpretação da IA. Edite qualquer campo antes de gerar o código.
        </p>
      </div>

      {/* Identidade */}
      <section className="bg-panel border border-border rounded-xl p-4 space-y-4">
        <h3 className="text-xs font-bold text-muted uppercase tracking-widest">Identidade</h3>
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1">
            <label className="text-[10px] font-bold text-muted uppercase tracking-wider">ID</label>
            <div className="px-3 py-2 bg-accent/10 border border-accent/25 rounded-lg text-accent font-mono font-bold text-sm">
              {p.id}
            </div>
          </div>
          <PlanField label="Nome" value={p.name} onChange={v => update('name', v)} />
        </div>
        <PlanField label="Descrição" value={p.description} onChange={v => update('description', v)} />
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1">
            <label className="text-[10px] font-bold text-muted uppercase tracking-wider">Timeframe Recomendado</label>
            <select
              value={p.recommended_timeframe}
              onChange={e => update('recommended_timeframe', e.target.value)}
              className="w-full bg-black/30 border border-border rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent/50"
            >
              {TF_OPTIONS.map(tf => <option key={tf} value={tf}>{tf}</option>)}
            </select>
          </div>
          <div className="space-y-1">
            <label className="text-[10px] font-bold text-muted uppercase tracking-wider">Min. Candles (aquecimento)</label>
            <input
              type="number"
              value={p.min_candles ?? 60}
              onChange={e => update('min_candles', parseInt(e.target.value))}
              className="w-full bg-black/30 border border-border rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent/50"
            />
          </div>
        </div>
        {p.interpretation_notes && (
          <div className="p-3 bg-yellow-400/5 border border-yellow-400/20 rounded-lg">
            <p className="text-xs text-yellow-400/80 font-medium">
              📋 Nota da IA: {p.interpretation_notes}
            </p>
          </div>
        )}
      </section>

      {/* Critérios de Entrada */}
      <section className="bg-panel border border-border rounded-xl p-4 space-y-3">
        <h3 className="text-xs font-bold text-muted uppercase tracking-widest">
          Critérios de Entrada ({p.criteria?.length ?? 0})
        </h3>
        {(p.criteria || []).map((c, i) => (
          <div key={i} className="flex items-center gap-3 p-2 bg-black/20 rounded-lg">
            <div className="w-6 h-6 rounded-full bg-accent/20 text-accent text-xs font-bold flex items-center justify-center shrink-0">
              {i + 1}
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-xs font-bold text-white">{c.label}</p>
              <p className="text-xs text-muted truncate">{c.description}</p>
            </div>
          </div>
        ))}
      </section>

      {/* Lógica de Entrada */}
      <section className="bg-panel border border-border rounded-xl p-4 space-y-3">
        <h3 className="text-xs font-bold text-muted uppercase tracking-widest">Lógica de Entrada</h3>
        <PlanField label="Condição BUY" value={p.entry_buy} onChange={v => update('entry_buy', v)} monospace />
        <PlanField label="Condição SELL" value={p.entry_sell} onChange={v => update('entry_sell', v)} monospace />
      </section>

      {/* Parâmetros */}
      <section className="bg-panel border border-border rounded-xl p-4 space-y-3">
        <h3 className="text-xs font-bold text-muted uppercase tracking-widest">
          Parâmetros ({p.params?.length ?? 0})
        </h3>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted border-b border-border">
                <th className="text-left pb-2 font-medium">Nome</th>
                <th className="text-left pb-2 font-medium">Tipo</th>
                <th className="text-left pb-2 font-medium">Default</th>
                <th className="text-left pb-2 font-medium">Min</th>
                <th className="text-left pb-2 font-medium">Max</th>
                <th className="text-left pb-2 font-medium">Descrição</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/30">
              {(p.params || []).map((param, i) => (
                <tr key={i} className="text-white/80">
                  <td className="py-1.5 font-mono text-accent">{param.name}</td>
                  <td className="py-1.5">{param.type}</td>
                  <td className="py-1.5 font-mono">{param.default}</td>
                  <td className="py-1.5 font-mono">{param.min}</td>
                  <td className="py-1.5 font-mono">{param.max}</td>
                  <td className="py-1.5 text-muted">{param.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Gestão de Risco */}
      <section className="bg-panel border border-border rounded-xl p-4 space-y-4">
        <h3 className="text-xs font-bold text-muted uppercase tracking-widest">Gestão de Risco</h3>

        {/* Métodos */}
        <div className="grid grid-cols-3 gap-3">
          <div className="p-2.5 bg-black/20 rounded-lg text-center">
            <p className="text-[10px] text-muted uppercase mb-1">Tipo de SL</p>
            <p className="text-xs font-bold text-white">{p.sl_method}</p>
          </div>
          <div className="p-2.5 bg-black/20 rounded-lg text-center">
            <p className="text-[10px] text-muted uppercase mb-1">Tipo de TP</p>
            <p className="text-xs font-bold text-white">{p.tp_method}</p>
          </div>
          <div className="p-2.5 bg-black/20 rounded-lg text-center">
            <p className="text-[10px] text-muted uppercase mb-1">R:R do Plano</p>
            <p className="text-xs font-bold text-accent">{p.tp_rr}×</p>
          </div>
        </div>

        {/* Parâmetros oficiais de risco — editáveis */}
        <div className="border-t border-border/40 pt-3 space-y-3">
          <p className="text-[10px] text-muted">
            Parâmetros de risco usados pela estratégia. Todos são configuráveis por bot após a implantação.
          </p>
          <div className="grid grid-cols-2 gap-3">
            {/* ATR Period */}
            <RiskParamField
              label="Período ATR"
              tooltip="Janela do ATR para calcular a volatilidade do ativo"
              paramName="atr_period"
              params={p.params}
              onChange={params => update('params', params)}
              suffix="períodos"
            />
            {/* SL Mult */}
            <RiskParamField
              label="Stop Loss (× ATR)"
              tooltip="SL = entrada ± ATR × este valor. Ex: 2.0 = stop a 2× a volatilidade"
              paramName="sl_mult"
              params={p.params}
              onChange={params => update('params', params)}
              suffix="× ATR"
              accent="bear"
            />
            {/* TP1 R:R */}
            <RiskParamField
              label="Take Profit TP1 (R:R)"
              tooltip="TP1 = risco × este valor. Ex: 2.0 = alvo com lucro de 2× o risco. Ao atingir, ativa o Trailing Stop"
              paramName="tp1_rr"
              params={p.params}
              onChange={params => update('params', params)}
              suffix="× Risco"
              accent="bull"
            />
            {/* TS Mult — Lucro Garantido */}
            <RiskParamField
              label="Trailing Stop (× ATR)"
              tooltip="Após atingir TP1, o Trailing Stop (Lucro Garantido) é ativado com callback = ATR × este valor"
              paramName="ts_mult"
              params={p.params}
              onChange={params => update('params', params)}
              suffix="× ATR"
              accent="accent"
            />
          </div>
          <div className="p-2.5 bg-accent/5 border border-accent/15 rounded-lg flex items-start gap-2">
            <span className="text-accent text-xs shrink-0">⚡</span>
            <p className="text-[10px] text-muted leading-relaxed">
              <span className="text-white font-medium">Lucro Garantido (SW-TS):</span> ao atingir o TP1, o sistema cancela o Stop Loss fixo e ativa o Trailing Stop dinâmico com callback baseado no ATR do momento — preservando o lucro acumulado enquanto segue a tendência.
            </p>
          </div>
        </div>
      </section>

      <div className="flex items-center justify-between pt-2">
        <button onClick={onBack} className="flex items-center gap-2 px-4 py-2.5 text-sm text-muted hover:text-white transition-colors">
          <ChevronLeft size={15} /> Voltar
        </button>
        <button
          onClick={() => onNext(p)}
          disabled={loading}
          className="flex items-center gap-2 px-6 py-2.5 bg-accent text-black rounded-lg text-sm font-bold hover:bg-accent/80 disabled:opacity-40 transition-all"
        >
          {loading ? <Loader2 size={15} className="animate-spin" /> : <Code2 size={15} />}
          {loading ? 'Gerando código...' : 'Aprovar e Gerar Código'}
          {!loading && <ChevronRight size={15} />}
        </button>
      </div>
    </div>
  )
}

// ── Step 3: Revisar Código ─────────────────────────────────────────────────────

function StepReviewCode({ code, onNext, onBack, loading }) {
  const [currentCode, setCurrentCode] = useState(code)

  return (
    <div className="space-y-6 max-w-4xl">
      <div>
        <h2 className="text-lg font-bold text-white mb-1">Revisar o Código Gerado</h2>
        <p className="text-sm text-muted">
          Verifique o código Python. Você pode editar antes de validar.
        </p>
      </div>

      <div className="bg-black rounded-xl border border-border overflow-hidden">
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-border bg-panel">
          <div className="flex items-center gap-2">
            <Code2 size={13} className="text-accent" />
            <span className="text-xs font-mono text-muted">strategy.py</span>
          </div>
          <span className="text-xs text-muted">{currentCode.split('\n').length} linhas</span>
        </div>
        <textarea
          value={currentCode}
          onChange={e => setCurrentCode(e.target.value)}
          className="w-full h-[420px] bg-transparent p-4 text-sm font-mono text-green-300 resize-none focus:outline-none leading-relaxed"
          spellCheck={false}
        />
      </div>

      <div className="flex items-center justify-between pt-2">
        <button onClick={onBack} className="flex items-center gap-2 px-4 py-2.5 text-sm text-muted hover:text-white transition-colors">
          <ChevronLeft size={15} /> Voltar ao Plano
        </button>
        <button
          onClick={() => onNext(currentCode)}
          disabled={loading}
          className="flex items-center gap-2 px-6 py-2.5 bg-accent text-black rounded-lg text-sm font-bold hover:bg-accent/80 disabled:opacity-40 transition-all"
        >
          {loading ? <Loader2 size={15} className="animate-spin" /> : <ClipboardList size={15} />}
          {loading ? 'Validando...' : 'Executar Validação'}
          {!loading && <ChevronRight size={15} />}
        </button>
      </div>
    </div>
  )
}

// ── Step 4: Validação ─────────────────────────────────────────────────────────

function CheckRow({ check }) {
  return (
    <div className={`flex items-start gap-3 p-3 rounded-lg border ${check.ok ? 'bg-bull/5 border-bull/15' : 'bg-bear/5 border-bear/15'}`}>
      {check.ok
        ? <CheckCircle2 size={15} className="text-bull shrink-0 mt-0.5" />
        : <XCircle     size={15} className="text-bear shrink-0 mt-0.5" />}
      <div className="min-w-0">
        <p className="text-xs font-bold text-white">{check.name}</p>
        {check.detail && <p className="text-xs text-muted mt-0.5 break-all">{check.detail}</p>}
      </div>
    </div>
  )
}

function StepValidation({ report, code, plan, onNext, onBack, onFix, onRegenerate, fixing, regenerating }) {
  const passed    = report?.passed
  const checks    = report?.checks || []
  const okCount   = checks.filter(c => c.ok).length
  const failCount = checks.filter(c => !c.ok).length
  const failedChecks = checks.filter(c => !c.ok)
  const busy = fixing || regenerating

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h2 className="text-lg font-bold text-white mb-1">Resultado da Validação</h2>
        <p className="text-sm text-muted">
          A estratégia foi executada em ambiente sandbox com 250 candles sintéticos.
        </p>
      </div>

      {/* Resumo */}
      <div className={`p-4 rounded-xl border flex items-center gap-4 ${passed ? 'bg-bull/8 border-bull/25' : 'bg-bear/8 border-bear/25'}`}>
        {passed
          ? <CheckCircle2 size={32} className="text-bull shrink-0" />
          : <XCircle     size={32} className="text-bear shrink-0" />}
        <div>
          <p className={`text-base font-bold ${passed ? 'text-bull' : 'text-bear'}`}>
            {passed ? 'Validação aprovada' : 'Validação falhou'}
          </p>
          <p className="text-xs text-muted">
            {okCount} verificações OK · {failCount} falhas
          </p>
        </div>
      </div>

      {/* Checklist */}
      <div className="space-y-2">
        {checks.map((c, i) => <CheckRow key={i} check={c} />)}
      </div>

      {report?.error && (
        <div className="p-3 bg-bear/8 border border-bear/25 rounded-lg">
          <p className="text-xs font-mono text-bear break-all">{report.error}</p>
        </div>
      )}

      {/* Lista dos erros a corrigir */}
      {!passed && failedChecks.length > 0 && (
        <div className="p-3 bg-black/30 border border-border rounded-lg space-y-1">
          <p className="text-[10px] font-bold text-muted uppercase tracking-wider mb-2">Erros que serão corrigidos:</p>
          {failedChecks.map((c, i) => (
            <div key={i} className="flex items-start gap-2">
              <XCircle size={11} className="text-bear shrink-0 mt-0.5" />
              <p className="text-xs text-white/70">{c.name}{c.detail ? ` — ${c.detail}` : ''}</p>
            </div>
          ))}
        </div>
      )}

      <div className="flex items-center justify-between pt-2">
        <button onClick={onBack} disabled={busy} className="flex items-center gap-2 px-4 py-2.5 text-sm text-muted hover:text-white transition-colors disabled:opacity-40">
          <ChevronLeft size={15} /> Editar Código
        </button>
        <div className="flex gap-3">
          {!passed && (
            <>
              {/* Correção cirúrgica — só toca nos erros */}
              <button
                onClick={onFix}
                disabled={busy}
                className="flex items-center gap-2 px-5 py-2.5 bg-accent text-black rounded-lg text-sm font-bold hover:bg-accent/80 transition-all disabled:opacity-40"
              >
                {fixing ? <Loader2 size={14} className="animate-spin" /> : <FlaskConical size={14} />}
                {fixing ? 'Corrigindo...' : `Corrigir ${failedChecks.length} erro${failedChecks.length !== 1 ? 's' : ''}`}
              </button>
              {/* Regeneração completa — opção de último recurso */}
              <button
                onClick={onRegenerate}
                disabled={busy}
                className="flex items-center gap-2 px-4 py-2.5 border border-border rounded-lg text-xs text-muted hover:text-white hover:border-white/20 transition-all disabled:opacity-40"
                title="Gera o código do zero a partir do plano"
              >
                {regenerating ? <Loader2 size={13} className="animate-spin" /> : <Code2 size={13} />}
                {regenerating ? 'Gerando...' : 'Regenerar do zero'}
              </button>
            </>
          )}
          {passed && (
            <button
              onClick={onNext}
              className="flex items-center gap-2 px-6 py-2.5 bg-bull text-black rounded-lg text-sm font-bold hover:bg-bull/80 transition-all"
            >
              <Rocket size={15} /> Implantar Estratégia <ChevronRight size={15} />
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Step 5: Confirmação ────────────────────────────────────────────────────────

function StepConfirmation({ result }) {
  const navigate = useNavigate()
  return (
    <div className="space-y-6 max-w-lg text-center">
      <div className="flex flex-col items-center gap-4">
        <div className="w-20 h-20 rounded-full bg-bull/15 flex items-center justify-center">
          <Rocket size={40} className="text-bull" />
        </div>
        <div>
          <h2 className="text-xl font-bold text-white mb-1">Estratégia implantada!</h2>
          <p className="text-sm text-muted">
            A estratégia <span className="text-accent font-bold">{result?.strategy_id}</span> está
            disponível imediatamente para criação de bots — sem reiniciar o servidor.
          </p>
        </div>
      </div>

      <div className="p-4 bg-bull/5 border border-bull/20 rounded-xl text-left space-y-2">
        <div className="flex justify-between text-xs">
          <span className="text-muted">ID</span>
          <span className="font-mono font-bold text-accent">{result?.strategy_id}</span>
        </div>
        <div className="flex justify-between text-xs">
          <span className="text-muted">Classe</span>
          <span className="font-mono text-white">{result?.class_name}</span>
        </div>
        <div className="flex justify-between text-xs">
          <span className="text-muted">Status</span>
          <span className="text-bull font-bold">Ativo no REGISTRY</span>
        </div>
      </div>

      <div className="flex flex-col gap-3 pt-2">
        <button
          onClick={() => navigate('/bots/new')}
          className="flex items-center justify-center gap-2 px-6 py-2.5 bg-accent text-black rounded-lg text-sm font-bold hover:bg-accent/80 transition-all"
        >
          <Rocket size={15} /> Criar Bot com esta Estratégia
        </button>
        <button
          onClick={() => window.location.reload()}
          className="px-6 py-2.5 border border-border rounded-lg text-sm text-muted hover:text-white hover:border-white/20 transition-all"
        >
          Criar outra Estratégia
        </button>
      </div>
    </div>
  )
}

// ── Lista de estratégias de fábrica ───────────────────────────────────────────

function FactoryList() {
  const qc = useQueryClient()
  const { data: strategies = [], isLoading } = useQuery({
    queryKey: ['factory-strategies'],
    queryFn:  factoryList,
  })
  const [viewing, setViewing] = useState(null)
  const [viewCode, setViewCode] = useState('')

  async function handleView(sid) {
    try {
      const data = await factoryGetCode(sid)
      setViewCode(data.code)
      setViewing(sid)
    } catch (err) {
      toast({ kind: 'error', title: 'Erro', body: err.message })
    }
  }

  async function handleDisable(sid) {
    if (!window.confirm(`Desativar a estratégia ${sid}? O arquivo é preservado.`)) return
    try {
      await factoryDisable(sid)
      qc.invalidateQueries({ queryKey: ['factory-strategies'] })
      toast({ kind: 'success', title: 'Desativada', body: `${sid} removida do REGISTRY.` })
    } catch (err) {
      toast({ kind: 'error', title: 'Erro', body: err.message })
    }
  }

  if (isLoading) return <div className="text-muted text-sm">Carregando...</div>
  if (!strategies.length) return (
    <div className="text-center py-8 text-muted text-sm">
      Nenhuma estratégia criada ainda. Use o wizard acima para criar a primeira.
    </div>
  )

  return (
    <div className="space-y-3">
      {strategies.map(s => (
        <div key={s.id} className="flex items-center gap-4 p-4 bg-panel border border-border rounded-xl">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-xs font-mono font-bold text-accent">{s.strategy_id}</span>
              <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-bold ${
                s.status === 'deployed' ? 'bg-bull/15 text-bull' :
                s.status === 'disabled' ? 'bg-muted/20 text-muted' : 'bg-yellow-400/15 text-yellow-400'
              }`}>{s.status}</span>
            </div>
            <p className="text-sm font-medium text-white truncate">{s.name}</p>
            <p className="text-xs text-muted truncate">{s.description}</p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => handleView(s.strategy_id)}
              className="p-2 rounded-lg hover:bg-border/40 text-muted hover:text-white transition-colors"
              title="Ver código"
            >
              <Eye size={14} />
            </button>
            {s.status === 'deployed' && (
              <button
                onClick={() => handleDisable(s.strategy_id)}
                className="p-2 rounded-lg hover:bg-bear/20 text-muted hover:text-bear transition-colors"
                title="Desativar"
              >
                <Trash2 size={14} />
              </button>
            )}
          </div>
        </div>
      ))}

      {/* Modal de código */}
      {viewing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onClick={() => setViewing(null)}>
          <div className="bg-panel border border-border rounded-2xl w-full max-w-3xl max-h-[80vh] flex flex-col" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between px-5 py-3.5 border-b border-border">
              <div className="flex items-center gap-2">
                <Code2 size={15} className="text-accent" />
                <span className="text-sm font-bold text-white">{viewing}</span>
              </div>
              <button onClick={() => setViewing(null)} className="text-muted hover:text-white text-xl leading-none">×</button>
            </div>
            <pre className="flex-1 overflow-auto p-5 text-xs font-mono text-green-300 leading-relaxed whitespace-pre">{viewCode}</pre>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Página principal ───────────────────────────────────────────────────────────

export default function StrategyFactory() {
  const [step, setStep] = useState(1)
  const [sourceText, setSourceText]   = useState('')
  const [plan, setPlan]               = useState(null)
  const [code, setCode]               = useState('')
  const [validationReport, setValidationReport] = useState(null)
  const [deployResult, setDeployResult]         = useState(null)
  const [generatingCode, setGeneratingCode]     = useState(false)
  const [validating, setValidating]             = useState(false)
  const [deploying, setDeploying]               = useState(false)
  const [fixing, setFixing]                     = useState(false)
  const [regenerating, setRegenerating]         = useState(false)

  const { data: status } = useQuery({
    queryKey: ['factory-status'],
    queryFn:  factoryStatus,
    retry: false,
  })

  // ── Handlers de etapa ──────────────────────────────────────────────────────

  async function handlePlanDone(text, newPlan) {
    setSourceText(text)
    setPlan(newPlan)
    setStep(2)
  }

  async function handlePlanApproved(approvedPlan) {
    setPlan(approvedPlan)
    setGeneratingCode(true)
    try {
      const data = await factoryGenerate(approvedPlan)
      setCode(data.code)
      setStep(3)
    } catch (err) {
      toast({ kind: 'error', title: 'Erro ao gerar código', body: err.message })
    } finally {
      setGeneratingCode(false)
    }
  }

  async function handleCodeApproved(currentCode) {
    setCode(currentCode)
    setValidating(true)
    try {
      const report = await factoryValidate(currentCode, plan)
      setValidationReport(report)
      setStep(4)
    } catch (err) {
      toast({ kind: 'error', title: 'Erro na validação', body: err.message })
    } finally {
      setValidating(false)
    }
  }

  // Correção cirúrgica — envia o código atual + só os erros que falharam
  async function handleFix() {
    const failedChecks = validationReport?.checks?.filter(c => !c.ok) || []
    if (!failedChecks.length) return
    const errors = failedChecks.map(c => `${c.name}${c.detail ? ': ' + c.detail : ''}`)
    setFixing(true)
    try {
      const data = await factoryFix(code, plan, errors)
      setCode(data.code)
      setValidationReport(data.validation)
      if (data.validation?.passed) {
        toast({ kind: 'success', title: 'Corrigido!', body: 'Todos os erros críticos foram resolvidos.' })
      } else {
        const remaining = (data.validation?.checks || []).filter(c => !c.ok).length
        toast({ kind: 'info', title: `${remaining} erro(s) restante(s)`, body: 'Continue corrigindo ou edite o código manualmente.' })
      }
    } catch (err) {
      toast({ kind: 'error', title: 'Erro ao corrigir', body: err.message })
    } finally {
      setFixing(false)
    }
  }

  // Regeneração completa — último recurso, reconstrói do plano do zero
  async function handleRegenerate() {
    setRegenerating(true)
    try {
      const data = await factoryGenerate(plan)
      setCode(data.code)
      const report = await factoryValidate(data.code, plan)
      setValidationReport(report)
      if (report.passed) {
        toast({ kind: 'success', title: 'Regenerado!', body: 'Novo código passou na validação.' })
      }
    } catch (err) {
      toast({ kind: 'error', title: 'Erro ao regenerar', body: err.message })
    } finally {
      setRegenerating(false)
    }
  }

  async function handleDeploy() {
    setDeploying(true)
    try {
      const result = await factoryDeploy(code, plan, sourceText)
      setDeployResult(result)
      setStep(5)
      toast({ kind: 'success', title: 'Implantada!', body: `${result.strategy_id} está disponível.` })
    } catch (err) {
      toast({ kind: 'error', title: 'Erro ao implantar', body: err.message })
    } finally {
      setDeploying(false)
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="p-6 space-y-8">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <FlaskConical size={22} className="text-accent" />
            <h1 className="text-xl font-bold text-white">Fábrica de Estratégias</h1>
            <span className="text-[10px] font-bold px-2 py-0.5 bg-accent/15 text-accent rounded-full uppercase tracking-wider">
              {status?.provider || 'AI'}
            </span>
          </div>
          <p className="text-sm text-muted">
            Descreva uma estratégia em linguagem natural e a IA implementa como código nativo.
          </p>
        </div>
        {status && (
          <div className={`text-[10px] font-bold px-2.5 py-1.5 rounded-lg flex items-center gap-1.5 ${
            status.configured ? 'bg-bull/10 text-bull border border-bull/20' : 'bg-bear/10 text-bear border border-bear/20'
          }`}>
            <span className={`w-1.5 h-1.5 rounded-full ${status.configured ? 'bg-bull' : 'bg-bear'}`} />
            {status.configured ? `${status.provider} · ${status.model}` : 'LLM não configurado'}
          </div>
        )}
      </div>

      {!status?.configured && (
        <div className="p-4 bg-yellow-400/8 border border-yellow-400/25 rounded-xl flex items-start gap-3">
          <AlertTriangle size={16} className="text-yellow-400 shrink-0 mt-0.5" />
          <div className="text-sm">
            <p className="font-bold text-yellow-400">LLM não configurado</p>
            <p className="text-yellow-400/70 text-xs mt-0.5">
              Adicione <code className="font-mono">DEEPSEEK_API_KEY=...</code> ao <code className="font-mono">.env</code> do VPS e reinicie. Ou defina <code className="font-mono">FACTORY_LLM_KEY</code> para outro provedor.
            </p>
          </div>
        </div>
      )}

      {/* Wizard */}
      {step < 5 && <Stepper current={step} />}

      <div>
        {step === 1 && <StepDescribe onNext={handlePlanDone} />}
        {step === 2 && plan && (
          <StepReviewPlan
            plan={plan}
            onNext={handlePlanApproved}
            onBack={() => setStep(1)}
            loading={generatingCode}
          />
        )}
        {step === 3 && (
          <StepReviewCode
            code={code}
            onNext={handleCodeApproved}
            onBack={() => setStep(2)}
            loading={validating}
          />
        )}
        {step === 4 && (
          <StepValidation
            report={validationReport}
            code={code}
            plan={plan}
            onNext={handleDeploy}
            onBack={() => setStep(3)}
            onFix={handleFix}
            onRegenerate={handleRegenerate}
            fixing={fixing}
            regenerating={regenerating || deploying}
          />
        )}
        {step === 5 && <StepConfirmation result={deployResult} />}
      </div>

      {/* Lista de estratégias existentes */}
      {step === 1 && (
        <div className="border-t border-border pt-8 space-y-4">
          <div className="flex items-center gap-2">
            <FileText size={15} className="text-muted" />
            <h2 className="text-sm font-bold text-white">Estratégias Criadas</h2>
          </div>
          <FactoryList />
        </div>
      )}
    </div>
  )
}
