import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import {
    User,
    getCurrentUser,
    getOAuthProviders,
    isAuthenticated,
    login as apiLogin,
    loginWithOAuth as apiLoginWithOAuth,
    logout as apiLogout,
    register as apiRegister,
    type OAuthProvider,
    type OAuthProvidersResponse
} from '../services/api';
import { rememberKnownAccountId } from '../utils/deviceScope';

interface AuthContextType {
    user: User | null;
    isLoading: boolean;
    isLoggedIn: boolean;
    error: string | null;
    oauthProviders: OAuthProvidersResponse;
    refreshUser: () => Promise<void>;
    login: (email: string, password: string) => Promise<boolean>;
    loginWithOAuth: (provider: OAuthProvider) => Promise<boolean>;
    register: (email: string, username: string, password: string) => Promise<boolean>;
    logout: () => void;
    clearError: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
    const [user, setUser] = useState<User | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [oauthProviders, setOauthProviders] = useState<OAuthProvidersResponse>({
        google: false,
        github: false
    });

    // Check if user is already logged in on mount
    useEffect(() => {
        const checkAuth = async () => {
            try {
                const providers = await getOAuthProviders();
                setOauthProviders(providers);
            } catch {
                setOauthProviders({ google: false, github: false });
            }

            if (isAuthenticated()) {
                try {
                    const userData = await getCurrentUser();
                    setUser(userData);
                } catch {
                    // Token is invalid, clear it
                    apiLogout();
                }
            }
            setIsLoading(false);
        };
        checkAuth();
    }, []);

    useEffect(() => {
        if (user?.id) {
            rememberKnownAccountId(user.id);
        }
    }, [user?.id]);

    const refreshUser = useCallback(async () => {
        if (!isAuthenticated()) {
            setUser(null);
            return;
        }
        const userData = await getCurrentUser();
        setUser(userData);
    }, []);

    const login = useCallback(async (email: string, password: string): Promise<boolean> => {
        setError(null);
        setIsLoading(true);
        try {
            await apiLogin(email, password);
            const userData = await getCurrentUser();
            setUser(userData);
            return true;
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Login failed');
            return false;
        } finally {
            setIsLoading(false);
        }
    }, []);

    const loginWithOAuth = useCallback(async (provider: OAuthProvider): Promise<boolean> => {
        setError(null);
        setIsLoading(true);
        try {
            await apiLoginWithOAuth(provider);
            const userData = await getCurrentUser();
            setUser(userData);
            return true;
        } catch (err) {
            setError(err instanceof Error ? err.message : 'OAuth login failed');
            return false;
        } finally {
            setIsLoading(false);
        }
    }, []);

    const register = useCallback(async (email: string, username: string, password: string): Promise<boolean> => {
        setError(null);
        setIsLoading(true);
        try {
            await apiRegister(email, username, password);
            // Auto-login after registration
            await apiLogin(email, password);
            const userData = await getCurrentUser();
            setUser(userData);
            return true;
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Registration failed');
            return false;
        } finally {
            setIsLoading(false);
        }
    }, []);

    const logout = useCallback(() => {
        apiLogout();
        setUser(null);
    }, []);

    const clearError = useCallback(() => {
        setError(null);
    }, []);

    return (
        <AuthContext.Provider value={{
            user,
            isLoading,
            isLoggedIn: !!user,
            error,
            oauthProviders,
            refreshUser,
            login,
            loginWithOAuth,
            register,
            logout,
            clearError
        }}>
            {children}
        </AuthContext.Provider>
    );
}

export function useAuth() {
    const context = useContext(AuthContext);
    if (context === undefined) {
        throw new Error('useAuth must be used within an AuthProvider');
    }
    return context;
}
