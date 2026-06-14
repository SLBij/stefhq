<script lang="ts">
	import { goto } from '$app/navigation';
	import { login } from '$lib/api';
	import { setToken } from '$lib/auth.svelte';

	let email = $state('');
	let password = $state('');
	let error = $state('');
	let loading = $state(false);

	async function handleLogin(e: SubmitEvent) {
		e.preventDefault();
		loading = true;
		error = '';
		try {
			const token = await login(email, password);
			setToken(token);
			goto('/hive_mind');
		} catch {
			error = 'Invalid credentials';
		} finally {
			loading = false;
		}
	}
</script>

<div class="min-h-screen flex items-center justify-center" style="background: var(--color-surface)">
	<div class="w-full max-w-sm px-8">
		<div class="mb-10 text-center">
			<div class="text-4xl mb-3">⬡</div>
			<h1 class="text-2xl font-semibold tracking-tight" style="color: var(--color-text)">Stef HQ</h1>
			<p class="text-sm mt-1" style="color: var(--color-text-muted)">Your personal command centre</p>
		</div>

		<form onsubmit={handleLogin} class="space-y-4">
			<input
				type="email"
				bind:value={email}
				placeholder="Email"
				required
				class="w-full px-4 py-3 rounded-xl text-sm outline-none transition-colors"
				style="background: var(--color-surface-3); border: 1px solid var(--color-border); color: var(--color-text);"
			/>
			<input
				type="password"
				bind:value={password}
				placeholder="Password"
				required
				class="w-full px-4 py-3 rounded-xl text-sm outline-none"
				style="background: var(--color-surface-3); border: 1px solid var(--color-border); color: var(--color-text);"
			/>

			{#if error}
				<p class="text-sm text-center" style="color: #f87171">{error}</p>
			{/if}

			<button
				type="submit"
				disabled={loading}
				class="w-full py-3 rounded-xl text-sm font-medium transition-opacity disabled:opacity-50"
				style="background: var(--color-hive); color: #0d0d0f;"
			>
				{loading ? 'Signing in…' : 'Sign in'}
			</button>
		</form>
	</div>
</div>
