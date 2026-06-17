<script lang="ts">
	import { getNotes, getPinnedMemories, saveNotes } from '$lib/api';
	import { auth } from '$lib/auth.svelte';
	import { WORKSPACES, type SavedMemory } from '$lib/types';

	let content = $state('');
	let savedAt = $state('');
	let saveStatus = $state<'saved' | 'saving' | 'unsaved'>('saved');
	let pinned = $state<SavedMemory[]>([]);
	let debounceTimer: ReturnType<typeof setTimeout>;

	$effect(() => {
		if (!auth.token) return;
		getNotes(auth.token).then((n) => {
			content = n.content;
			savedAt = n.updated_at;
		});
		getPinnedMemories(auth.token).then((p) => {
			pinned = p;
		});
	});

	function onInput() {
		saveStatus = 'unsaved';
		clearTimeout(debounceTimer);
		debounceTimer = setTimeout(async () => {
			if (!auth.token) return;
			saveStatus = 'saving';
			await saveNotes(auth.token, content);
			savedAt = new Date().toISOString();
			saveStatus = 'saved';
		}, 1000);
	}

	function workspaceMeta(id: string) {
		return WORKSPACES.find((w) => w.id === id) ?? { icon: '◈', color: 'var(--color-text-muted)', label: id };
	}

	const SLASH_COMMANDS = [
		{ cmd: '/note <text>', desc: 'Append text to this scratchpad from any chat' },
		{ cmd: '/newjob', desc: 'Start a new job in Business — prompts for all fields' },
	];
</script>

<div class="flex h-full">
	<!-- Scratchpad -->
	<div class="flex flex-col flex-1 min-w-0">
		<header class="flex items-center gap-3 px-6 py-4 shrink-0" style="border-bottom: 1px solid var(--color-border)">
			<span style="color: var(--color-hive)" class="text-xl">◇</span>
			<span class="font-semibold" style="color: var(--color-text)">Notes</span>
			<span class="ml-auto text-xs" style="color: var(--color-text-muted)">
				{#if saveStatus === 'saving'}
					Saving…
				{:else if saveStatus === 'unsaved'}
					Unsaved
				{:else if savedAt}
					Saved {new Date(savedAt).toLocaleTimeString('en-ZA', { hour: '2-digit', minute: '2-digit' })}
				{/if}
			</span>
		</header>

		<div class="flex-1 px-6 py-4 overflow-y-auto">
			<textarea
				bind:value={content}
				oninput={onInput}
				placeholder="Start typing… supports markdown. Use /note in any chat to append quickly."
				class="w-full h-full resize-none text-sm outline-none leading-relaxed"
				style="background: transparent; color: var(--color-text); min-height: 400px; field-sizing: content;"
			></textarea>
		</div>
	</div>

	<!-- Right panel -->
	<aside class="w-64 shrink-0 flex flex-col py-6 px-4 gap-6 overflow-y-auto" style="border-left: 1px solid var(--color-border)">

		<!-- Pinned memories -->
		<section>
			<h2 class="text-xs font-semibold mb-3 uppercase tracking-wide" style="color: var(--color-text-muted)">Pinned</h2>
			{#if pinned.length === 0}
				<p class="text-xs leading-relaxed" style="color: var(--color-text-muted)">
					Tag a memory as <span class="px-1 py-0.5 rounded text-xs" style="background: var(--color-surface-3); color: var(--color-hive)">pinned</span> in memory review to surface it here.
				</p>
			{:else}
				<div class="flex flex-col gap-2">
					{#each pinned as mem}
						{@const ws = workspaceMeta(mem.workspace)}
						<div class="px-3 py-2.5 rounded-lg text-xs" style="background: var(--color-surface-2); border: 1px solid var(--color-border)">
							<p class="leading-relaxed mb-1.5" style="color: var(--color-text)">{mem.content}</p>
							<span style="color: {ws.color}">{ws.icon} {ws.label}</span>
						</div>
					{/each}
				</div>
			{/if}
		</section>

		<!-- Slash command reference -->
		<section>
			<h2 class="text-xs font-semibold mb-3 uppercase tracking-wide" style="color: var(--color-text-muted)">Commands</h2>
			<div class="flex flex-col gap-2">
				{#each SLASH_COMMANDS as { cmd, desc }}
					<div class="text-xs">
						<code class="px-1.5 py-0.5 rounded text-xs" style="background: var(--color-surface-3); color: var(--color-hive)">{cmd}</code>
						<p class="mt-1 leading-relaxed" style="color: var(--color-text-muted)">{desc}</p>
					</div>
				{/each}
			</div>
		</section>
	</aside>
</div>
