import React, { createContext, useContext, useState, useCallback } from 'react'
import T from './translations'

export const LanguageContext = createContext(null)

const STORAGE_KEY = 'tradebot_lang'
const DEFAULT_LANG = 'pt-BR'
const SUPPORTED    = ['pt-BR', 'en-US']

function interpolate(str, params) {
  if (!params || typeof str !== 'string') return str
  return Object.entries(params).reduce(
    (s, [k, v]) => s.replace(new RegExp(`\\{\\{${k}\\}\\}`, 'g'), String(v ?? '')),
    str
  )
}

export function LanguageProvider({ children }) {
  const [lang, setLangState] = useState(() => {
    const stored = typeof localStorage !== 'undefined' && localStorage.getItem(STORAGE_KEY)
    return SUPPORTED.includes(stored) ? stored : DEFAULT_LANG
  })

  const setLang = useCallback(l => {
    if (!SUPPORTED.includes(l)) return
    localStorage.setItem(STORAGE_KEY, l)
    setLangState(l)
  }, [])

  const t = useCallback((key, params) => {
    const dict = T[lang] ?? T[DEFAULT_LANG]
    const raw  = dict[key] ?? T[DEFAULT_LANG][key] ?? key
    return interpolate(raw, params)
  }, [lang])

  return (
    <LanguageContext.Provider value={{ lang, setLang, t }}>
      {children}
    </LanguageContext.Provider>
  )
}

export function useLanguage() {
  const ctx = useContext(LanguageContext)
  if (!ctx) throw new Error('useLanguage must be used inside LanguageProvider')
  return ctx
}
