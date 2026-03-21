import { AuthProvider, useAuth } from './contexts/AuthContext'
import { I18nProvider, useI18n } from './contexts/I18nContext'
import Dashboard from './components/Layout/Dashboard'
import AuthPage from './components/Auth/AuthPage'
import { ToastProvider } from './components/Shared/Toast'
import { useTheme } from './hooks/useTheme'

function AppContent() {
    const { user, isLoggedIn, isLoading } = useAuth();
    const { isVietnamese } = useI18n();
    // Initialize theme system - applies dark/light mode class to root
    useTheme();

    if (isLoading) {
        return (
            <div className="app-loading">
                <div className="app-loading-spinner" />
                <span>{isVietnamese ? 'Đang tải PigTex...' : 'Loading PigTex...'}</span>
            </div>
        );
    }

    return isLoggedIn ? <Dashboard key={user?.id || 'authenticated'} /> : <AuthPage />;
}

function App() {
    return (
        <I18nProvider>
            <AuthProvider>
                <AppContent />
                <ToastProvider />
            </AuthProvider>
        </I18nProvider>
    );
}

export default App
