<script lang="ts">
	import { page } from '$app/state';
	import { getGoogleAuthUrl, getGoogleStatus } from '$lib/api';
	import { auth } from '$lib/auth.svelte';

	let connected = $state<boolean | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);

	const statusParam = $derived(page.url.searchParams.get('status'));

	$effect(() => {
		if (!auth.token) return;
		getGoogleStatus(auth.token)
			.then((s) => { connected = s.connected; })
			.catch(() => { connected = false; });
	});

	async function connect() {
		if (!auth.token) return;
		loading = true;
		error = null;
		try {
			const { url } = await getGoogleAuthUrl(auth.token);
			window.location.href = url;
		} catch (e) {
			error = 'Failed to start Google authorization. Is the backend running?';
			loading = false;
		}
	}
</script>

<div class="flex flex-col h-full">
	<header class="flex items-center gap-3 px-6 py-4 shrink-0" style="border-bottom: 1px solid var(--color-border)">
		<span style="color: var(--color-hive)" class="text-xl">⬡</span>
		<span class="font-semibold" style="color: var(--color-text)">Google Account</span>
	</header>

	<div class="flex-1 flex items-start justify-center px-6 py-12">
		<div class="w-full max-w-md space-y-6">

			{#if statusParam === 'success'}
				<div class="px-5 py-4 rounded-xl text-sm" style="background: var(--color-surface-3); border: 1px solid var(--color-border)">
					<p class="font-medium" style="color: var(--color-text)">Google account connected</p>
					<p class="mt-1" style="color: var(--color-text-muted)">Gmail and Calendar are now available in Business.</p>
				</div>
			{/if}

			<div class="px-5 py-5 rounded-xl space-y-4" style="background: var(--color-surface-2); border: 1px solid var(--color-border)">
				<div class="space-y-1">
					<h2 class="font-semibold text-sm" style="color: var(--color-text)">Gmail &amp; Google Calendar</h2>
					<p class="text-xs leading-relaxed" style="color: var(--color-text-muted)">
						Connects your Google account so the Business agent can draft and send client emails,
						and schedule install appointments on your calendar.
					</p>
				</div>

				<div class="space-y-2 text-xs" style="color: var(--color-text-muted)">
					<div class="flex items-start gap-2">
						<span style="color: var(--color-hive)">◈</span>
						<span><strong style="color: var(--color-text)">Email:</strong> agent composes a draft → shows it to you → you confirm before anything sends</span>
					</div>
					<div class="flex items-start gap-2">
						<span style="color: var(--color-hive)">◈</span>
						<span><strong style="color: var(--color-text)">Calendar:</strong> agent proposes an event → you confirm → created without client as attendee until you verify</span>
					</div>
				</div>

				<div class="flex items-center gap-3 pt-1">
					{#if connected === null}
						<div class="h-3 w-3 rounded-full animate-pulse" style="background: var(--color-text-muted)"></div>
						<span class="text-xs" style="color: var(--color-text-muted)">Checking status…</span>
					{:else if connected}
						<div class="h-3 w-3 rounded-full" style="background: #4ade80"></div>
						<span class="text-xs" style="color: var(--color-text-muted)">Connected</span>
						<button
							onclick={connect}
							disabled={loading}
							class="ml-auto text-xs px-3 py-1.5 rounded-lg transition-opacity disabled:opacity-50"
							style="background: var(--color-surface-3); border: 1px solid var(--color-border); color: var(--color-text-muted)"
						>
							{loading ? 'Redirecting…' : 'Reconnect'}
						</button>
					{:else}
						<div class="h-3 w-3 rounded-full" style="background: var(--color-text-muted); opacity: 0.4"></div>
						<span class="text-xs" style="color: var(--color-text-muted)">Not connected</span>
						<button
							onclick={connect}
							disabled={loading}
							class="ml-auto text-xs px-3 py-1.5 rounded-lg font-medium transition-opacity disabled:opacity-50"
							style="background: var(--color-hive); color: #0d0d0f"
						>
							{loading ? 'Redirecting…' : 'Connect Google'}
						</button>
					{/if}
				</div>

				{#if error}
					<p class="text-xs" style="color: #f87171">{error}</p>
				{/if}
			</div>

			<p class="text-xs text-center" style="color: var(--color-text-muted)">
				Scopes requested: <code>gmail.compose</code> · <code>calendar</code>
			</p>
		</div>
	</div>
</div>
