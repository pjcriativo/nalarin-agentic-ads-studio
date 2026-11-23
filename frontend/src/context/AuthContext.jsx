/* eslint-disable react-refresh/only-export-components -- context modules intentionally export the provider and its hook together */
import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { bindUser, clearTenant, dropStaleResponse, getEpoch, isAccessLostMessage, reportAccessLost, tenantHeaders } from '../lib/tenant';
import { registerAuthBridge } from '../lib/http';

const AuthContext = createContext();

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

export const useAuth = () => {
    const context = useContext(AuthContext);
    if (!context) {
        throw new Error('useAuth must be used within AuthProvider');
    }
    return context;
};

export const AuthProvider = ({ children }) => {
    const [user, setUser] = useState(null);
    const [accessToken, setAccessToken] = useState(localStorage.getItem('accessToken'));
    const [refreshToken, setRefreshToken] = useState(localStorage.getItem('refreshToken'));
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    // Check if user is authenticated on mount
    useEffect(() => {
        const initAuth = async () => {
            if (accessToken) {
                try {
                    await fetchUser();
                } catch {
                    // Token might be expired, try to refresh
                    if (refreshToken) {
                        try {
                            await refreshAccessToken();
                        } catch (refreshErr) {
                            // Only logout if it's a definitive auth failure (401/403)
                            // If it's a network error or 500, keep the tokens so we can try again later
                            if (refreshErr.status === 401 || refreshErr.status === 403) {
                                logout();
                            }
                        }
                    } else {
                        logout();
                    }
                }
            }
            setLoading(false);
        };
        initAuth();
    }, []);

    // Auto-refresh token every 6 days to prevent expiration (tokens last 7 days)
    useEffect(() => {
        if (!refreshToken) return;

        const refreshInterval = setInterval(async () => {
            try {
                await refreshAccessToken();
            } catch {
                // Silently fail - will retry on next interval or next API call
                console.log('Background token refresh failed, will retry');
            }
        }, 6 * 24 * 60 * 60 * 1000); // 6 days in ms

        return () => clearInterval(refreshInterval);
    }, [refreshToken]);

    // `token` matters after a refresh: the closure's `accessToken` is still the EXPIRED one
    const fetchUser = async (token = accessToken) => {
        const response = await fetch(`${API_URL}/auth/me`, {
            headers: {
                'Authorization': `Bearer ${token}`,
            },
        });

        if (!response.ok) {
            throw new Error('Failed to fetch user');
        }

        const userData = await response.json();
        bindUser(userData.id); // loads THIS user's persisted organization selection (never another user's)
        setUser(userData);
        return userData;
    };

    const login = async (email, password) => {
        setError(null);
        try {
            const response = await fetch(`${API_URL}/auth/login/json`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ email, password }),
            });

            if (!response.ok) {
                const data = await response.json();
                throw new Error(data.detail || 'Login failed');
            }

            const data = await response.json();
            // A new session: forget the previous user's organization BEFORE the new token is
            // in place, so no request can pair the new identity with the old tenant.
            clearTenant();
            setAccessToken(data.access_token);
            setRefreshToken(data.refresh_token);
            localStorage.setItem('accessToken', data.access_token);
            localStorage.setItem('refreshToken', data.refresh_token);

            // Fetch user data
            const userResponse = await fetch(`${API_URL}/auth/me`, {
                headers: {
                    'Authorization': `Bearer ${data.access_token}`,
                },
            });

            if (userResponse.ok) {
                const userData = await userResponse.json();
                bindUser(userData.id);
                setUser(userData);
            }

            return data;
        } catch (err) {
            setError(err.message);
            throw err;
        }
    };

    const register = async (email, password, name) => {
        setError(null);
        try {
            const response = await fetch(`${API_URL}/auth/register`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ email, password, name }),
            });

            if (!response.ok) {
                const data = await response.json();
                throw new Error(data.detail || 'Registration failed');
            }

            const userData = await response.json();

            // Auto-login after registration
            await login(email, password);

            return userData;
        } catch (err) {
            setError(err.message);
            throw err;
        }
    };

    const logout = useCallback(async () => {
        // Optionally call the logout endpoint
        if (accessToken && refreshToken) {
            try {
                await fetch(`${API_URL}/auth/logout`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${accessToken}`,
                    },
                    body: JSON.stringify({ refresh_token: refreshToken }),
                });
            } catch {
                // Ignore errors during logout
            }
        }

        setUser(null);
        setAccessToken(null);
        setRefreshToken(null);
        localStorage.removeItem('accessToken');
        localStorage.removeItem('refreshToken');
        clearTenant(); // selection + tenant-scoped preferences die with the session
    }, [accessToken, refreshToken]);

    // Shared in-flight refresh promise: when several concurrent requests hit
    // 401 at once (e.g. Overview firing Meta+Google+TikTok in parallel), every
    // caller must await the SAME refresh — otherwise the second refresh uses
    // the already-rotated (deleted) token, fails, and logs the user out for
    // no reason. The promise is cleared on settle so a later expiry can
    // refresh again.
    const refreshPromiseRef = React.useRef(null);

    const refreshAccessToken = async () => {
        if (refreshPromiseRef.current) {
            return refreshPromiseRef.current;
        }
        if (!refreshToken) {
            throw new Error('No refresh token');
        }

        const promise = (async () => {
            // A network error propagates as-is (no status attached, so we don't logout).
            const response = await fetch(`${API_URL}/auth/refresh`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ refresh_token: localStorage.getItem('refreshToken') }),
            });

            if (!response.ok) {
                const error = new Error('Failed to refresh token');
                error.status = response.status;
                throw error;
            }

            const data = await response.json();
            setAccessToken(data.access_token);
            localStorage.setItem('accessToken', data.access_token);

            // Update refresh token if new one provided (rolling refresh)
            if (data.refresh_token) {
                setRefreshToken(data.refresh_token);
                localStorage.setItem('refreshToken', data.refresh_token);
            }

            // Re-fetch user data with the NEW token
            await fetchUser(data.access_token);

            return data.access_token;
        })();

        refreshPromiseRef.current = promise;
        try {
            return await promise;
        } finally {
            refreshPromiseRef.current = null;
        }
    };

    // Helper to make authenticated API calls
    // `options.tenant === false` marks a request that is not organization-scoped
    // (the organization list itself): no X-Organization-Id, no stale-tenant guard.
    const authFetch = useCallback(async (url, options = {}) => {
        const { tenant = true, ...fetchOptions } = options;
        const currentToken = localStorage.getItem('accessToken');
        const currentRefreshToken = localStorage.getItem('refreshToken');

        if (!currentToken) {
            throw new Error('No access token available');
        }

        const startEpoch = getEpoch();
        const makeRequest = async (authToken) => {
            const response = await fetch(url, {
                ...fetchOptions,
                // Needed so the browser stores/sends the oauth_state cookie
                // (Sprint 0's OAuth CSRF mechanism) across the cross-origin
                // fetch between the frontend (5173) and backend (8010) — the
                // default fetch credentials mode ('same-origin') silently
                // drops Set-Cookie on cross-origin responses.
                credentials: 'include',
                headers: {
                    // read at call time: after a token refresh the retry carries the CURRENT organization
                    ...(tenant ? tenantHeaders() : {}),
                    ...fetchOptions.headers,
                    'Authorization': `Bearer ${authToken}`,
                },
            });
            return response;
        };

        let response = await makeRequest(currentToken);

        // If unauthorized, try to refresh token
        if (response.status === 401 && currentRefreshToken) {
            try {
                const newToken = await refreshAccessToken();
                response = await makeRequest(newToken);
            } catch (err) {
                // Only logout if it's a definitive auth failure
                if (err.status === 401 || err.status === 403) {
                    logout();
                    throw new Error('Session expired. Please login again.');
                }
                // For network errors, we throw but don't logout
                throw err;
            }
        }

        if (tenant) {
            // The organization (or user) changed while this request was in flight:
            // the answer belongs to the PREVIOUS tenant and must never reach the UI.
            if (getEpoch() !== startEpoch) return dropStaleResponse();
            if (response.status === 403 || response.status === 400) {
                try {
                    const body = await response.clone().json();
                    if (isAccessLostMessage(body?.detail)) reportAccessLost();
                } catch { /* not JSON: nothing to learn */ }
            }
        }

        return response;
    }, [refreshAccessToken, logout]);

    // axios callers (src/api/*.js) share this refresh through the bridge in lib/http.js
    useEffect(() => {
        registerAuthBridge({ refresh: refreshAccessToken });
        return () => registerAuthBridge(null);
    });

    // Check if user has a specific role
    const hasRole = useCallback((roleName) => {
        if (!user) return false;
        if (user.is_superuser) return true;
        return user.roles?.some(role => role.name === roleName) || false;
    }, [user]);

    // Check if user has a specific permission
    const hasPermission = useCallback((permissionName) => {
        if (!user) return false;
        if (user.is_superuser) return true;
        return user.roles?.some(role =>
            role.permissions?.some(perm => perm.name === permissionName)
        ) || false;
    }, [user]);

    const value = {
        user,
        accessToken,
        loading,
        error,
        isAuthenticated: !!user,
        login,
        register,
        logout,
        authFetch,
        hasRole,
        hasPermission,
        refreshAccessToken,
    };

    return (
        <AuthContext.Provider value={value}>
            {children}
        </AuthContext.Provider>
    );
};
