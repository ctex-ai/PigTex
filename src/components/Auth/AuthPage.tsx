import React, { useState } from 'react';
import { AlertCircle, Eye, EyeOff, Loader2, Mail, Lock, User, Github } from 'lucide-react';
import { useAuth } from '../../contexts/AuthContext';
import { useI18n } from '../../contexts/I18nContext';
import './AuthPage.css';

import logoUrl from '../../../assets/pigtex_logo.png';

export function AuthPage() {
    const { login, loginWithOAuth, register, isLoading, error, clearError, oauthProviders } = useAuth();
    const { language, isVietnamese, setLanguage } = useI18n();
    const [isLogin, setIsLogin] = useState(true);
    const [oauthLoadingProvider, setOauthLoadingProvider] = useState<'google' | 'github' | null>(null);
    const [email, setEmail] = useState('');
    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [showPassword, setShowPassword] = useState(false);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (isLogin) {
            await login(email, password);
        } else {
            await register(email, username, password);
        }
    };

    const handleOAuth = async (provider: 'google' | 'github') => {
        setOauthLoadingProvider(provider);
        try {
            await loginWithOAuth(provider);
        } finally {
            setOauthLoadingProvider(null);
        }
    };

    const switchMode = () => {
        setIsLogin(!isLogin);
        clearError();
        setEmail('');
        setUsername('');
        setPassword('');
    };

    const copy = isVietnamese ? {
        signInTitle: 'Đăng nhập bằng email',
        signUpTitle: 'Tạo tài khoản',
        signInSubtitle: 'Kết nối hội thoại, dữ liệu và công việc của bạn trong một nơi.',
        signUpSubtitle: 'Tham gia PigTex để mở khóa AI workstation song ngữ.',
        email: 'Email',
        username: 'Tên người dùng',
        password: 'Mật khẩu',
        hidePassword: 'Ẩn mật khẩu',
        showPassword: 'Hiện mật khẩu',
        getStarted: 'Bắt đầu',
        createAccount: 'Tạo tài khoản',
        continueWith: 'hoặc tiếp tục với',
        continueGoogle: 'Tiếp tục với Google',
        signUpGoogle: 'Đăng ký với Google',
        continueGithub: 'Tiếp tục với GitHub',
        signUpGithub: 'Đăng ký với GitHub',
        noAccount: 'Chưa có tài khoản?',
        signUp: 'Đăng ký',
        hasAccount: 'Đã có tài khoản?',
        signIn: 'Đăng nhập',
        languageLabel: 'Ngôn ngữ',
        vietnamese: 'VI',
        english: 'EN'
    } : {
        signInTitle: 'Sign in with email',
        signUpTitle: 'Create your account',
        signInSubtitle: 'Bring your conversations, data, and work together in one place.',
        signUpSubtitle: 'Join PigTex to unlock a bilingual AI workstation.',
        email: 'Email',
        username: 'Username',
        password: 'Password',
        hidePassword: 'Hide password',
        showPassword: 'Show password',
        getStarted: 'Get Started',
        createAccount: 'Create Account',
        continueWith: 'or continue with',
        continueGoogle: 'Continue with Google',
        signUpGoogle: 'Sign up with Google',
        continueGithub: 'Continue with GitHub',
        signUpGithub: 'Sign up with GitHub',
        noAccount: "Don't have an account?",
        signUp: 'Sign up',
        hasAccount: 'Already have an account?',
        signIn: 'Sign in',
        languageLabel: 'Language',
        vietnamese: 'VI',
        english: 'EN'
    };

    return (
        <div className="auth-page">
            <div className="auth-bg-layer auth-bg-layer-primary" />
            <div className="auth-bg-layer auth-bg-layer-secondary" />

            {/* Decorative orbit rings */}
            <div className="auth-orbit auth-orbit-1" />
            <div className="auth-orbit auth-orbit-2" />

            {/* Logo top-left */}
            <div className="auth-logo-badge">
                <img src={logoUrl} alt="PigTex" className="auth-logo-img" />
                <span className="auth-logo-text">PigTex</span>
            </div>

            <div className="auth-language-switch" aria-label={copy.languageLabel}>
                <button
                    type="button"
                    className={`auth-language-btn ${language === 'vi' ? 'active' : ''}`}
                    onClick={() => setLanguage('vi')}
                >
                    {copy.vietnamese}
                </button>
                <button
                    type="button"
                    className={`auth-language-btn ${language === 'en' ? 'active' : ''}`}
                    onClick={() => setLanguage('en')}
                >
                    {copy.english}
                </button>
            </div>

            {/* Centered Glass Card */}
            <div className="auth-card">
                {/* Icon badge */}
                <div className="auth-card-icon">
                    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4" />
                        <polyline points="10 17 15 12 10 7" />
                        <line x1="15" y1="12" x2="3" y2="12" />
                    </svg>
                </div>

                <h1 className="auth-card-title">
                    {isLogin ? copy.signInTitle : copy.signUpTitle}
                </h1>
                <p className="auth-card-subtitle">
                    {isLogin
                        ? copy.signInSubtitle
                        : copy.signUpSubtitle}
                </p>

                <form className="auth-form" onSubmit={handleSubmit}>
                    {/* Email field */}
                    <div className="auth-input-group">
                        <Mail size={18} className="auth-input-icon" />
                        <input
                            id="auth-email"
                            type="email"
                            className="auth-input"
                            placeholder={copy.email}
                            value={email}
                            onChange={(e) => setEmail(e.target.value)}
                            required
                            autoComplete="email"
                        />
                    </div>

                    {/* Username field (sign up only) */}
                    {!isLogin && (
                        <div className="auth-input-group">
                            <User size={18} className="auth-input-icon" />
                            <input
                                id="auth-username"
                                type="text"
                                className="auth-input"
                                placeholder={copy.username}
                                value={username}
                                onChange={(e) => setUsername(e.target.value)}
                                required
                                autoComplete="username"
                            />
                        </div>
                    )}

                    {/* Password field */}
                    <div className="auth-input-group">
                        <Lock size={18} className="auth-input-icon" />
                        <input
                            id="auth-password"
                            type={showPassword ? 'text' : 'password'}
                            className="auth-input auth-input-password"
                            placeholder={copy.password}
                            value={password}
                            onChange={(e) => setPassword(e.target.value)}
                            required
                            autoComplete={isLogin ? 'current-password' : 'new-password'}
                        />
                        <button
                            type="button"
                            className="auth-password-toggle"
                            onClick={() => setShowPassword(!showPassword)}
                            tabIndex={-1}
                            aria-label={showPassword ? copy.hidePassword : copy.showPassword}
                        >
                            {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                        </button>
                    </div>

                    {/* Error message */}
                    {error && (
                        <div className="auth-error">
                            <AlertCircle size={16} />
                            <span>{error}</span>
                        </div>
                    )}

                    {/* Submit button */}
                    <button
                        type="submit"
                        className="auth-submit"
                        disabled={isLoading || oauthLoadingProvider !== null}
                    >
                        {isLoading ? (
                            <Loader2 size={20} className="auth-spinner" />
                        ) : (
                            isLogin ? copy.getStarted : copy.createAccount
                        )}
                    </button>
                </form>

                {(oauthProviders.google || oauthProviders.github) && (
                    <>
                        <div className="auth-divider">
                            <span>{copy.continueWith}</span>
                        </div>

                        <div className="auth-social-grid">
                            {oauthProviders.google && (
                                <button
                                    type="button"
                                    className="auth-social-btn"
                                    onClick={() => handleOAuth('google')}
                                    disabled={isLoading || oauthLoadingProvider !== null}
                                >
                                    {oauthLoadingProvider === 'google' ? (
                                        <Loader2 size={16} className="auth-spinner" />
                                    ) : (
                                        <svg className="auth-google-icon" viewBox="0 0 24 24" aria-hidden="true">
                                            <path
                                                d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
                                                fill="#4285F4"
                                            />
                                            <path
                                                d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                                                fill="#34A853"
                                            />
                                            <path
                                                d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.84z"
                                                fill="#FBBC05"
                                            />
                                            <path
                                                d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
                                                fill="#EA4335"
                                            />
                                        </svg>
                                    )}
                                    <span>{isLogin ? copy.continueGoogle : copy.signUpGoogle}</span>
                                </button>
                            )}

                            {oauthProviders.github && (
                                <button
                                    type="button"
                                    className="auth-social-btn"
                                    onClick={() => handleOAuth('github')}
                                    disabled={isLoading || oauthLoadingProvider !== null}
                                >
                                    {oauthLoadingProvider === 'github' ? (
                                        <Loader2 size={16} className="auth-spinner" />
                                    ) : (
                                        <Github size={16} />
                                    )}
                                    <span>{isLogin ? copy.continueGithub : copy.signUpGithub}</span>
                                </button>
                            )}
                        </div>
                    </>
                )}

                {/* Footer switch */}
                <div className="auth-footer-switch">
                    {isLogin ? (
                        <p>
                            {copy.noAccount}{' '}
                            <button type="button" onClick={switchMode}>{copy.signUp}</button>
                        </p>
                    ) : (
                        <p>
                            {copy.hasAccount}{' '}
                            <button type="button" onClick={switchMode}>{copy.signIn}</button>
                        </p>
                    )}
                </div>
            </div>
        </div>
    );
}

export default AuthPage;
