<script lang="ts">
	import { getActivityLogs } from '$lib/api';
	import { auth } from '$lib/auth.svelte';
	import { WORKSPACES } from '$lib/types';

	let logs = $state<Awaited<ReturnType<typeof getActivityLogs>>>([]);
	let loading = $state(true);

	$effect(() => {
		if (!auth.token) return;
		getActivityLogs(auth.token, 200).then((data) => {
			logs = data;
			loading = false;
		});
	});

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
</script>

<div class="flex flex-col h-full">
	<header class="flex items-center gap-3 px-6 py-4 shrink-0" style="border-bottom: 1px solid var(--color-border)">
		<span style="color: var(--color-hive)" class="text-xl">◈</span>
		<span class="font-semibold" style="color: var(--color-text)">Activity</span>
		<span class="text-xs ml-1" style="color: var(--color-text-muted)">— what's been happening</span>
	</header>

	<div class="flex-1 overflow-y-auto px-6 py-6">
		{#if loading}
			<p class="text-sm" style="color: var(--color-text-muted)">Loading…</p>
		{:else if logs.length === 0}
			<p class="text-sm" style="color: var(--color-text-muted)">No activity yet. Start chatting!</p>
		{:else}
			<div class="flex flex-col gap-1">
				{#each logs as log (log.id)}
					{@const ws = workspaceMeta(log.workspace)}
					<div class="flex items-start gap-3 px-3 py-2.5 rounded-lg"
						style="border: 1px solid var(--color-border); background: var(--color-surface-2)">

						<span class="text-sm shrink-0 mt-0.5" style="color: {ws.color}">{ws.icon}</span>

						<div class="flex-1 min-w-0">
							<div class="flex items-center gap-2 flex-wrap">
								<span class="text-xs font-medium" style="color: var(--color-text)">{ws.label}</span>

								<span class="px-1.5 py-0.5 rounded text-xs"
									style="background: {log.source === 'telegram' ? 'var(--color-hive)20' : 'var(--color-surface-3)'}; color: {log.source === 'telegram' ? 'var(--color-hive)' : 'var(--color-text-muted)'}">
									{SOURCE_LABELS[log.source] ?? log.source}
								</span>

								{#if log.action_type === 'tool_call'}
									<span class="px-1.5 py-0.5 rounded text-xs"
										style="background: var(--color-business)15; color: var(--color-business)">
										write
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
