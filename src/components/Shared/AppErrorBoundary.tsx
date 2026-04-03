import { Component, type ErrorInfo, type ReactNode } from 'react'

type AppErrorBoundaryProps = {
    children: ReactNode
}

type AppErrorBoundaryState = {
    error: Error | null
}

class AppErrorBoundary extends Component<AppErrorBoundaryProps, AppErrorBoundaryState> {
    state: AppErrorBoundaryState = {
        error: null
    }

    static getDerivedStateFromError(error: Error): AppErrorBoundaryState {
        return { error }
    }

    componentDidCatch(error: Error, errorInfo: ErrorInfo) {
        console.error('PigTex runtime error:', error, errorInfo)
    }

    handleReload = () => {
        window.location.reload()
    }

    render() {
        if (!this.state.error) {
            return this.props.children
        }

        const isVietnamese = typeof navigator !== 'undefined'
            ? navigator.language.toLowerCase().startsWith('vi')
            : false

        return (
            <div className="app-runtime-error">
                <div className="app-runtime-error-card">
                    <p className="app-runtime-error-kicker">PigTex</p>
                    <h1>
                        {isVietnamese
                            ? 'PigTex vua gap loi giao dien.'
                            : 'PigTex hit a renderer error.'}
                    </h1>
                    <p className="app-runtime-error-copy">
                        {isVietnamese
                            ? 'Man hinh trang thuong xuat hien khi mot component loi luc render. PigTex da chan loi nay de ban co the tai lai thay vi bi trang toan bo.'
                            : 'A blank screen usually means a component crashed while rendering. PigTex caught the error so you can reload instead of losing the whole window.'}
                    </p>
                    <button className="app-runtime-error-btn" type="button" onClick={this.handleReload}>
                        {isVietnamese ? 'Tai lai PigTex' : 'Reload PigTex'}
                    </button>
                    {this.state.error.message && (
                        <pre className="app-runtime-error-details">{this.state.error.message}</pre>
                    )}
                </div>
            </div>
        )
    }
}

export default AppErrorBoundary
