import React from 'react'
import s from './ErrorBoundary.module.css'

interface ErrorBoundaryProps {
  name: string
  children: React.ReactNode
}

interface ErrorBoundaryState {
  hasError: boolean
  error: Error | null
}

export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error(`ErrorBoundary [${this.props.name}]:`, error, info.componentStack)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className={s.errorBoundary}>
          <strong>{this.props.name} encountered an error</strong>
          <p>{this.state.error?.message ?? 'Unknown error'}</p>
          <div className={s.actions}>
            <button type="button" onClick={() => this.setState({ hasError: false, error: null })}>
              Dismiss
            </button>
            <button type="button" onClick={() => window.location.reload()}>
              Reload
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
