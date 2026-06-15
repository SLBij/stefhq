<script lang="ts">
	import { getReviewQueue, reviewMemory } from '$lib/api';
	import { auth } from '$lib/auth.svelte';
	import { reviewRefresh } from '$lib/reviewRefresh.svelte';

	interface ReviewItem {
		id: string;
		content: string;
		workspace: string;
		type: string;
		confidence: number;
		created_at: string;
	}

	const WS_COLOR: Record<string, string> = {
		hive_mind: 'var(--color-hive)',
		business: 'var(--color-business)',
		plant_atlas: 'var(--color-plant)',
		round_table: 'var(--color-round)',
		inbox: 'var(--color-inbox)',
	};

	const WS_LABEL: Record<string, string> = {
		hive_mind: 'Hive Mind',
		business: 'Business',
		plant_atlas: 'Plant Atlas',
		round_table: 'Round Table',
		inbox: 'Inbox',
		global: 'Global',
	};

	let items = $state<ReviewItem[]>([]);
	let acting = $state<string | null>(null);
	let loaded = $state(false);

	$effect(() => {
		if (!auth.token) return;
		getReviewQueue(auth.token).then((q) => {
			items = q;
			loaded = true;
		});
	});

	async function act(id: string, action: 'approve' | 'discard') {
		if (!auth.token || acting) return;
		acting = id;
		await reviewMemory(auth.token, id, action);
		items = items.filter((i) => i.id !== id);
		reviewRefresh.tick++;
		acting = null;
	}

	function confidenceColor(c: number): string {
		if (c >= 0.65) return '#4ade80';
		if (c >= 0.5) return '#fb923c';
		return '#f87171';
	}
</script>

<div class="flex flex-col h-full">
	<header class="flex items-center gap-3 px-6 py-4 shrink-0" style="border-bottom: 1px solid var(--color-border)">
		<span style="color: var(--color-hive)" class="text-xl">◈</span>
		<span class="font-semibold" style="color: var(--color-text)">Memory Review</span>
		{#if items.length > 0}
			<span class="text-xs px-2 py-0.5 rounded-full" style="background: var(--color-surface-3); color: var(--color-text-muted)">
				{items.length} pending
			</span>
		{/if}
	</header>

	<div class="flex-1 overflow-y-auto px-6 py-6">
		{#if !loaded}
			<p class="text-sm" style="color: var(--color-text-muted)">Loading…</p>

		{:else if items.length === 0}
			<div class="h-full flex items-center justify-center">
				<div class="text-center space-y-2">
					<div class="text-4xl">✓</div>
					<p class="text-sm" style="color: var(--color-text-muted)">All clear — nothing to review</p>
				</div>
			</div>

		{:else}
			<div class="max-w-2xl space-y-3">
				{#each items as item (item.id)}
					<div
						class="rounded-xl p-4 space-y-3"
						style="background: var(--color-surface-2); border: 1px solid var(--color-border); opacity: {acting === item.id ? 0.4 : 1}; transition: opacity 0.15s"
					>
						<!-- Content -->
						<p class="text-sm leading-relaxed" style="color: var(--color-text)">{item.content}</p>

						<!-- Metadata -->
						<div class="flex items-center gap-3 flex-wrap">
							<span
								class="text-xs px-2 py-0.5 rounded-full font-medium"
								style="background: {WS_COLOR[item.workspace] ?? 'var(--color-global)'}20; color: {WS_COLOR[item.workspace] ?? 'var(--color-global)'}"
							>
								{WS_LABEL[item.workspace] ?? item.workspace}
							</span>
							<span class="text-xs px-2 py-0.5 rounded-full" style="background: var(--color-surface-3); color: var(--color-text-muted)">
								{item.type}
							</span>
							<span class="text-xs font-mono" style="color: {confidenceColor(item.confidence)}">
								{Math.round(item.confidence * 100)}% confidence
							</span>
						</div>

						<!-- Actions -->
						<div class="flex gap-2">
							<button
								onclick={() => act(item.id, 'approve')}
								disabled={!!acting}
								class="px-3 py-1.5 rounded-lg text-xs font-medium transition-opacity disabled:opacity-30"
								style="background: #4ade8020; color: #4ade80; border: 1px solid #4ade8040"
							>
								✓ Save to memory
							</button>
							<button
								onclick={() => act(item.id, 'discard')}
								disabled={!!acting}
								class="px-3 py-1.5 rounded-lg text-xs font-medium transition-opacity disabled:opacity-30"
								style="background: #f8717120; color: #f87171; border: 1px solid #f8717140"
							>
								✕ Discard
							</button>
						</div>
					</div>
				{/each}
			</div>
		{/if}
	</div>
</div>
