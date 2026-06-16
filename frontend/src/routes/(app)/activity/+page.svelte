<script lang="ts">
	import { getActivityLogs } from '$lib/api';
	import { auth } from '$lib/auth.svelte';
	import { WORKSPACES } from '$lib/types';

	let logs = $state<Awaited<ReturnType<typeof getActivityLogs>>>([]);
	let loading = $state(true);
	let filter = $state<'all' | 'write' | string>('all'); // 'all' | 'write' | workspace id

	$effect(() => {
		if (!auth.token) return;
		getActivityLogs(auth.token, 500).then((data) => {
			logs = data;
			loading = false;
		});
	});

	let filtered = $derived(
		filter === 'all'
			? logs
			: filter === 'write'
				? logs.filter((l) => l.action_type === 'tool_call')
				: logs.filter((l) => l.workspace === filter)
	);

	function formatTime(dateStr: string) {
		const d = new Date(dateStr);
		const now = new Date();
		const diffMins = Math.floor((now.getTime() - d.getTime()) / 60000);
		if (diffMins < 1) return 'just now';
		if (diffMins < 60) return `${diffMins}m ago`;
		const diffHours = Math.floor(diffMins / 60);
		if (diffHours < 24) return `${diffHours}h ago`;
		return d.toLocaleDateString('en-ZA', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
	}

	function workspaceMeta(id: string) {
		return WORKSPACES.find((w) => w.id === id) ?? { icon: '◈', color: 'var(--color-text-muted)', label: id };
	}

	const SOURCE_LABELS: Record<string, string> = {
		web: 'Web',
		telegram: 'Telegram',
	};

	let writeCount = $derived(logs.filter((l) => l.action_type === 'tool_call').length);
</script>

<div class="flex flex-col h-full">
	<header class="px-6 py-4 shrink-0" style="border-bottom: 1px solid var(--color-border)">
		<div class="flex items-center gap-3 mb-3">
			<span style="color: var(--color-hive)" class="text-xl">◈</span>
			<span class="font-semibold" style="color: var(--color-text)">Activity</span>
			<span class="text-xs ml-1" style="color: var(--color-text-muted)">— what's been happening</span>
			{#if !loading}
				<span class="ml-auto text-xs" style="color: var(--color-text-muted)">{logs.length} entries</span>
			{/if}
		</div>

		<!-- Filter bar -->
		<div class="flex items-center gap-1.5 flex-wrap">
			<button
				onclick={() => filter = 'all'}
				class="px-2.5 py-1 rounded-lg text-xs transition-colors"
				style:background={filter === 'all' ? 'var(--color-surface-3)' : 'transparent'}
				style:color={filter === 'all' ? 'var(--color-text)' : 'var(--color-text-muted)'}
				style:border={filter === 'all' ? '1px solid var(--color-border)' : '1px solid transparent'}
			>
				All
			</button>

			<button
				onclick={() => filter = 'write'}
				class="px-2.5 py-1 rounded-lg text-xs transition-colors flex items-center gap-1.5"
				style:background={filter === 'write' ? '#fb923c18' : 'transparent'}
				style:color={filter === 'write' ? '#fb923c' : 'var(--color-text-muted)'}
				style:border={filter === 'write' ? '1px solid #fb923c40' : '1px solid transparent'}
			>
				Writes
				{#if writeCount > 0}
					<span class="px-1.5 py-0.5 rounded-full text-xs font-medium"
						style="background: #fb923c20; color: #fb923c">
						{writeCount}
					</span>
				{/if}
			</button>

			<div class="w-px h-4 mx-1" style="background: var(--color-border)"></div>

			{#each WORKSPACES as ws}
				<button
					onclick={() => filter = filter === ws.id ? 'all' : ws.id}
					class="px-2.5 py-1 rounded-lg text-xs transition-colors"
					style:background={filter === ws.id ? `${ws.color}18` : 'transparent'}
					style:color={filter === ws.id ? ws.color : 'var(--color-text-muted)'}
					style:border={filter === ws.id ? `1px solid ${ws.color}40` : '1px solid transparent'}
				>
					{ws.icon} {ws.label}
				</button>
			{/each}
		</div>
	</header>

	<div class="flex-1 overflow-y-auto px-6 py-4">
		{#if loading}
			<p class="text-sm" style="color: var(--color-text-muted)">Loading…</p>
		{:else if filtered.length === 0}
			<p class="text-sm" style="color: var(--color-text-muted)">
				{filter === 'all' ? 'No activity yet. Start chatting!' : 'No entries match this filter.'}
			</p>
		{:else}
			<div class="flex flex-col gap-1">
				{#each filtered as log (log.id)}
					{@const ws = workspaceMeta(log.workspace)}
					{@const isWrite = log.action_type === 'tool_call'}
					<div
						class="flex items-start gap-3 px-3 py-2.5 rounded-lg"
						style:background={isWrite ? '#fb923c08' : 'var(--color-surface-2)'}
						style:border={isWrite ? '1px solid #fb923c25' : '1px solid var(--color-border)'}
					>
						<span class="text-sm shrink-0 mt-0.5" style="color: {ws.color}">{ws.icon}</span>

						<div class="flex-1 min-w-0">
							<div class="flex items-center gap-2 flex-wrap">
								<span class="text-xs font-medium" style="color: var(--color-text)">{ws.label}</span>

								<span class="px-1.5 py-0.5 rounded text-xs"
									style="background: {log.source === 'telegram' ? 'var(--color-hive)20' : 'var(--color-surface-3)'}; color: {log.source === 'telegram' ? 'var(--color-hive)' : 'var(--color-text-muted)'}">
									{SOURCE_LABELS[log.source] ?? log.source}
								</span>

								{#if isWrite}
									<span class="px-1.5 py-0.5 rounded text-xs font-medium"
										style="background: #fb923c20; color: #fb923c">
										✎ write
									</span>
								{/if}

								<span class="ml-auto text-xs shrink-0" style="color: var(--color-text-muted)">{formatTime(log.created_at)}</span>
							</div>

							<p class="text-xs mt-0.5 truncate" style="color: var(--color-text-muted)">{log.summary}</p>
						</div>
					</div>
				{/each}
			</div>
		{/if}
	</div>
</div>
