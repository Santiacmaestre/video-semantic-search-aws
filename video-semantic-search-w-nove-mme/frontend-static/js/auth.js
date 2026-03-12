// Cognito authentication with native login
const COGNITO_REGION = CONFIG.COGNITO_DOMAIN.split('.auth.')[1]?.split('.amazoncognito')[0] || 'us-east-1';
const COGNITO_IDP_URL = `https://cognito-idp.${COGNITO_REGION}.amazonaws.com/`;

class Auth {
    getIdToken() {
        return localStorage.getItem('idToken');
    }

    isAuthenticated() {
        const token = this.getIdToken();
        if (!token) return false;
        // Check JWT expiry
        try {
            const payload = JSON.parse(atob(token.split('.')[1]));
            return payload.exp * 1000 > Date.now();
        } catch { return false; }
    }

    async login(email, password) {
        const response = await fetch(COGNITO_IDP_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-amz-json-1.1',
                'X-Amz-Target': 'AWSCognitoIdentityProviderService.InitiateAuth'
            },
            body: JSON.stringify({
                AuthFlow: 'USER_AUTH',
                ClientId: CONFIG.COGNITO_CLIENT_ID,
                AuthParameters: {
                    USERNAME: email,
                    PASSWORD: password,
                    PREFERRED_CHALLENGE: 'PASSWORD'
                }
            })
        });
        const data = await response.json();
        if (data.AuthenticationResult) {
            this._storeTokens(data.AuthenticationResult);
            return { success: true };
        }
        if (data.ChallengeName === 'NEW_PASSWORD_REQUIRED') {
            return { challenge: 'NEW_PASSWORD_REQUIRED', session: data.Session };
        }
        throw new Error(data.message || data.__type || 'Login failed');
    }

    _storeTokens(authResult) {
        localStorage.setItem('idToken', authResult.IdToken);
        localStorage.setItem('accessToken', authResult.AccessToken);
        localStorage.setItem('refreshToken', authResult.RefreshToken);
    }

    async respondToNewPasswordChallenge(email, newPassword, session) {
        const response = await fetch(COGNITO_IDP_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-amz-json-1.1',
                'X-Amz-Target': 'AWSCognitoIdentityProviderService.RespondToAuthChallenge'
            },
            body: JSON.stringify({
                ChallengeName: 'NEW_PASSWORD_REQUIRED',
                ClientId: CONFIG.COGNITO_CLIENT_ID,
                ChallengeResponses: { USERNAME: email, NEW_PASSWORD: newPassword },
                Session: session
            })
        });
        const data = await response.json();
        if (data.AuthenticationResult) {
            this._storeTokens(data.AuthenticationResult);
            return { success: true };
        }
        throw new Error(data.message || 'Challenge failed');
    }

    async refreshSession() {
        const refreshToken = localStorage.getItem('refreshToken');
        if (!refreshToken) return false;
        try {
            const response = await fetch(COGNITO_IDP_URL, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-amz-json-1.1',
                    'X-Amz-Target': 'AWSCognitoIdentityProviderService.InitiateAuth'
                },
                body: JSON.stringify({
                    AuthFlow: 'REFRESH_TOKEN_AUTH',
                    ClientId: CONFIG.COGNITO_CLIENT_ID,
                    AuthParameters: { REFRESH_TOKEN: refreshToken }
                })
            });
            const data = await response.json();
            if (data.AuthenticationResult) {
                localStorage.setItem('idToken', data.AuthenticationResult.IdToken);
                if (data.AuthenticationResult.AccessToken)
                    localStorage.setItem('accessToken', data.AuthenticationResult.AccessToken);
                return true;
            }
        } catch (e) { console.error('Refresh failed:', e.message); }
        return false;
    }

    async logout() {
        try {
            const accessToken = localStorage.getItem('accessToken');
            if (accessToken) {
                await fetch(COGNITO_IDP_URL, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-amz-json-1.1',
                        'X-Amz-Target': 'AWSCognitoIdentityProviderService.GlobalSignOut'
                    },
                    body: JSON.stringify({ AccessToken: accessToken })
                });
            }
        } catch (e) {
            console.error('Server-side logout failed:', e.message);
        }
        localStorage.removeItem('idToken');
        localStorage.removeItem('accessToken');
        localStorage.removeItem('refreshToken');
        window.location.reload();
    }
}

window.auth = new Auth();
