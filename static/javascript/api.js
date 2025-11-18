/**
 * Yahooquiz API Client
 * Handles authentication and API calls
 */

const API_BASE_URL = '/api';

// Helper function to get access token
function getAccessToken() {
    return localStorage.getItem('access_token');
}

// Helper function to check if user is authenticated
function isAuthenticated() {
    return !!getAccessToken();
}

// Helper function to get current user from localStorage
function getCurrentUser() {
    const userStr = localStorage.getItem('user');
    return userStr ? JSON.parse(userStr) : null;
}

// Make authenticated API request
async function apiRequest(endpoint, options = {}) {
    const token = getAccessToken();
    const headers = {
        'Content-Type': 'application/json',
        ...(options.headers || {})
    };

    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    try {
        const response = await fetch(`${API_BASE_URL}${endpoint}`, {
            ...options,
            headers
        });

        // Handle 401 Unauthorized - token expired
        if (response.status === 401 && token) {
            // Try to refresh token
            const refreshed = await refreshAccessToken();
            if (refreshed) {
                // Retry original request
                headers['Authorization'] = `Bearer ${getAccessToken()}`;
                return await fetch(`${API_BASE_URL}${endpoint}`, {
                    ...options,
                    headers
                });
            } else {
                // Refresh failed, redirect to login
                logout();
                return response;
            }
        }

        return response;
    } catch (error) {
        console.error('API request error:', error);
        throw error;
    }
}

// Refresh access token
async function refreshAccessToken() {
    try {
        const response = await fetch(`${API_BASE_URL}/auth/refresh`, {
            method: 'POST',
            credentials: 'include' // Include cookies
        });

        if (response.ok) {
            const data = await response.json();
            localStorage.setItem('access_token', data.access_token);
            return true;
        }

        return false;
    } catch (error) {
        console.error('Token refresh error:', error);
        return false;
    }
}

// Logout and clear storage
function logout() {
    localStorage.removeItem('access_token');
    localStorage.removeItem('user');
    window.location.href = '/login';
}

// Auth API calls
const AuthAPI = {
    async register(email, password, display_name = null) {
        return await apiRequest('/auth/register', {
            method: 'POST',
            body: JSON.stringify({ email, password, display_name })
        });
    },

    async login(email, password) {
        return await apiRequest('/auth/login', {
            method: 'POST',
            body: JSON.stringify({ email, password })
        });
    },

    async refresh() {
        return await refreshAccessToken();
    }
};

// User API calls
const UserAPI = {
    async getCurrentUser() {
        return await apiRequest('/users/me');
    },

    async getUserIcons() {
        return await apiRequest('/users/me/icons');
    },

    async getUserTransactions(limit = 50) {
        return await apiRequest(`/users/me/transactions?limit=${limit}`);
    },

    async getUserMatches(limit = 10) {
        return await apiRequest(`/users/me/matches?limit=${limit}`);
    }
};

// Match API calls
const MatchAPI = {
    async createMatch(matchData) {
        return await apiRequest('/matches', {
            method: 'POST',
            body: JSON.stringify(matchData)
        });
    }
};

// Icon API calls
const IconAPI = {
    async getIcons() {
        return await apiRequest('/icons');
    },

    async purchaseIcon(iconId) {
        return await apiRequest(`/icons/${iconId}/purchase`, {
            method: 'POST'
        });
    }
};

// Leaderboard API calls
const LeaderboardAPI = {
    async getLeaderboard(limit = 100) {
        return await apiRequest(`/leaderboard?limit=${limit}`);
    }
};

// Export for use in other scripts
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        isAuthenticated,
        getCurrentUser,
        logout,
        AuthAPI,
        UserAPI,
        MatchAPI,
        IconAPI,
        LeaderboardAPI
    };
}
