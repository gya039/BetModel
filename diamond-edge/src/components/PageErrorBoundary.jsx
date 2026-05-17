import React from 'react'

export default class PageErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidUpdate(prevProps) {
    if (prevProps.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null })
    }
  }

  componentDidCatch(error, info) {
    console.error('Diamond Edge page error:', error, info)
  }

  render() {
    if (!this.state.error) return this.props.children

    return (
      <div className="de-page-error">
        <div className="de-page-error__mark">!</div>
        <div className="de-page-error__title">This tab hit a runtime error</div>
        <div className="de-page-error__sub">
          Refresh once. If it keeps happening, this message means the shell survived and the broken tab can be fixed directly.
        </div>
        <button
          className="de-page-error__btn"
          onClick={() => this.setState({ error: null })}
        >
          Try again
        </button>
      </div>
    )
  }
}
