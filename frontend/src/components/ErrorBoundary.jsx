import React from 'react'
import { AlertTriangle } from 'lucide-react'
import { LanguageContext } from '../i18n/LanguageContext'

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('[ErrorBoundary]', error, info)
  }

  render() {
    if (this.state.error) {
      return (
        <LanguageContext.Consumer>
          {({ t }) => (
            <div className="flex flex-col items-center justify-center min-h-[300px] gap-4 text-center p-8">
              <AlertTriangle size={40} className="text-bear opacity-70" />
              <div>
                <p className="font-semibold text-white mb-1">{t('err.title')}</p>
                <p className="text-muted text-sm mb-4">{this.state.error.message}</p>
                <button
                  className="btn btn-ghost text-sm"
                  onClick={() => this.setState({ error: null })}
                >
                  {t('err.retry')}
                </button>
              </div>
            </div>
          )}
        </LanguageContext.Consumer>
      )
    }
    return this.props.children
  }
}
