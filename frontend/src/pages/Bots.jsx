import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { getBots } from '../api'
import { useLanguage } from '../i18n/LanguageContext'
import BotCard from '../components/BotCard'
import { Plus, Bot } from 'lucide-react'

export default function Bots() {
  const nav = useNavigate()
  const { t } = useLanguage()
  const { data: bots = [], isLoading } = useQuery({ queryKey: ['bots'], queryFn: getBots })

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold">{t('bots.title')}</h1>
          <p className="text-muted text-sm mt-0.5">{t('bots.count', { n: bots.length })}</p>
        </div>
        <button onClick={() => nav('/bots/new')} className="btn-primary btn flex items-center gap-2">
          <Plus size={15} /> {t('bots.new')}
        </button>
      </div>

      {isLoading ? (
        <div className="text-muted text-center py-16">{t('c.loading')}</div>
      ) : bots.length === 0 ? (
        <div className="card text-center py-16">
          <Bot size={40} className="mx-auto mb-4 text-muted opacity-30" />
          <p className="text-muted mb-4">{t('bots.no_bots')}</p>
          <button onClick={() => nav('/bots/new')} className="btn-primary btn">
            {t('bots.create_first')}
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {bots.map(bot => <BotCard key={bot.id} bot={bot} />)}
        </div>
      )}
    </div>
  )
}
