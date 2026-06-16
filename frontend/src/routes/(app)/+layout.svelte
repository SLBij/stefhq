<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { getConversations, getReviewQueue } from '$lib/api';
	import { auth, clearToken, isAuthenticated } from '$lib/auth.svelte';
	import { historyRefresh } from '$lib/historyRefresh.svelte';
	import { reviewRefresh } from '$lib/reviewRefresh.svelte';
	import { WORKSPACES, type ConversationSummary } from '$lib/types';

	let { children } = $props();
	let reviewCount = $state(0);
	let conversations = $state<ConversationSummary[]>([]);

	$effect(() => {
		if (!isAuthenticated()) goto('/login');
	});

	$effect(() => {
		const _tick = reviewRefresh.tick;
		if (!auth.token) return;
		getReviewQueue(auth.token).then((items) => {
			reviewCount = items?.length ?? 0;
		});
	});

	// Reload conversation list when workspace or historyRefresh.tick changes
	$effect(() => {
		const ws = activeWorkspace;
		const _tick = historyRefresh.tick;
		if (!auth.token) return;
		getConversations(auth.token, ws).then((list) => {
			conversations = list;
		});
	});

	function signOut() {
		clearToken();
		goto('/login');
	}

	function newConversation() {
		goto(`/${activeWorkspace}`);
	}

	let activeWorkspace = $derived(
		page.params.workspace ?? page.url.pathname.split('/').filter(Boolean)[0] ?? 'hive_mind'
	);
	let activeConversationId = $derived(page.url.searchParams.get('c'));

	function formatTime(dateStr: string): string {
		const d = new Date(dateStr);
		const now = new Date();
		const diffMins = Math.floor((now.getTime() - d.getTime()) / 60000);
		if (diffMins < 1) return 'now';
		if (diffMins < 60) return `${diffMins}m`;
		const diffHours = Math.floor(diffMins / 60);
		if (diffHours < 24) return `${diffHours}h`;
		const diffDays = Math.floor(diffHours / 24);
		if (diffDays < 7) return ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getDay()];
		return `${d.getMonth() + 1}/${d.getDate()}`;
	}
</script>

<div class="flex h-screen overflow-hidden" style="background: var(--color-surface)">
	<!-- Sidebar -->
	<aside class="w-56 flex flex-col py-6 px-3 shrink-0" style="border-right: 1px solid var(--color-border)">
		<div class="px-3 mb-6">
			<div class="text-lg font-semibold tracking-tight flex items-center gap-2">
				<span style="color: var(--color-hive)">⬡</span>
				<span style="color: var(--color-text)">Stef HQ</span>
			</div>
		</div>

		<!-- Workspace nav -->
		<nav class="flex flex-col gap-1">
			{#each WORKSPACES as ws}
				{@const active = activeWorkspace === ws.id}
				<a
					href="/{ws.id}"
					class="flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors"
					style:background={active ? `${ws.color}18` : 'transparent'}
					style:color={active ? ws.color : 'var(--color-text-muted)'}
				>
					<span class="text-base">{ws.icon}</span>
					<span class="font-medium">{ws.label}</span>
				</a>
			{/each}
		</nav>

		<!-- History section -->
		<div class="mt-4 flex-1 flex flex-col min-h-0">
			<div class="flex items-center justify-between px-3 mb-2">
				<span class="text-xs font-medium" style="color: var(--color-text-muted)">History</span>
				<button
					onclick={newConversation}
					class="text-xs px-2 py-0.5 rounded transition-colors"
					style="color: var(--color-text-muted); border: 1px solid var(--color-border)"
					title="New conversation"
				>
					+ New
				</button>
			</div>

			<div class="flex-1 overflow-y-auto flex flex-col gap-0.5 min-h-0">
				{#if conversations.length === 0}
					<p class="px-3 text-xs" style="color: var(--color-text-muted)">No history yet</p>
				{:else}
					{#each conversations as conv (conv.id)}
						{@const active = activeConversationId === conv.id}
						<button
							onclick={() => goto(`/${activeWorkspace}?c=${conv.id}`)}
							class="w-full text-left px-3 py-2 rounded-lg transition-colors"
							style:background={active ? 'var(--color-surface-3)' : 'transparent'}
						>
							<p
								class="text-xs leading-snug truncate"
								style="color: {active ? 'var(--color-text)' : 'var(--color-text-muted)'}"
							>
								{conv.title ?? conv.preview}
							</p>
							<p class="text-xs mt-0.5" style="color: var(--color-text-muted); opacity: 0.6">
								{formatTime(conv.updated_at)}
							</p>
						</button>
					{/each}
				{/if}
			</div>
		</div>

		<!-- Footer -->
		<div class="mt-4 flex flex-col gap-1">
			<a
				href="/review"
				class="px-3 py-2 rounded-lg text-xs flex items-center justify-between transition-colors"
				style="color: var(--color-text-muted)"
			>
				<span>Memory review</span>
				{#if reviewCount > 0}
					<span class="px-1.5 py-0.5 rounded-full text-xs font-medium" style="background: {`var(--color-hive)`}20; color: var(--color-hive)">
						{reviewCount}
					</span>
				{/if}
			</a>
			<button
				onclick={signOut}
				class="px-3 py-2 rounded-lg text-xs text-left transition-colors"
				style="color: var(--color-text-muted)"
			>
				Sign out
			</button>
		</div>
	</aside>

	<!-- Main -->
	<main class="flex-1 min-w-0">
		{@render children()}
	</main>
</div>
