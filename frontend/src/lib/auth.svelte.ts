const TOKEN_KEY = 'stefhq_token';

export const auth = $state({
	token: typeof localStorage !== 'undefined' ? localStorage.getItem(TOKEN_KEY) : null,
});

export function setToken(token: string) {
	auth.token = token;
	localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
	auth.token = null;
	localStorage.removeItem(TOKEN_KEY);
}

export function isAuthenticated() {
	return !!auth.token;
}
