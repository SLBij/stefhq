<script lang="ts">
	import { listNotes, getNote, saveNote, deleteNote, getPinnedMemories } from '$lib/api';
	import { auth } from '$lib/auth.svelte';
	import { WORKSPACES, type SavedMemory } from '$lib/types';

	let notes = $state<{ title: string; updated_at: string }[]>([]);
	let activeTitle = $state('Notes');
	let content = $state('');
	let savedAt = $state('');
	let saveStatus = $state<'saved' | 'saving' | 'unsaved'>('saved');
	let pinned = $state<SavedMemory[]>([]);
	let newNoteTitle = $state('');
	let showNewInput = $state(false);
	let debounceTimer: ReturnType<typeof setTimeout>;

	async function loadNotesList() {
		notes = await listNotes(auth.token!);
		if (notes.length === 0) {
			notes = [{ title: 'Notes', updated_at: new Date().toISOString() }];
		}
	}

	async function loadNote(title: string) {
		activeTitle = title;
		const n = await getNote(auth.token!, title);
		content = n.content;
		savedAt = n.updated_at;
		saveStatus = 'saved';
	}

	$effect(() => {
		if (!auth.token) return;
		loadNotesList().then(() => loadNote(activeTitle));
		getPinnedMemories(auth.token).then((p) => { pinned = p; });
	});

	function onInput() {
		saveStatus = 'unsaved';
		clearTimeout(debounceTimer);
		debounceTimer = setTimeout(async () => {
			if (!auth.token) return;
			saveStatus = 'saving';
			await saveNote(auth.token, activeTitle, content);
			savedAt = new Date().toISOString();
			saveStatus = 'saved';
			await loadNotesList();
		}, 1000);
	}

	async function createNote() {
		const title = newNoteTitle.trim();
		if (!title) return;
		newNoteTitle = '';
		showNewInput = false;
		await loadNotesList();
		await loadNote(title);
	}

	async function handleDeleteNote(title: string) {
		if (title === 'Notes') return;
		if (!confirm(`Delete "${title}"?`)) return;
		await deleteNote(auth.token!, title);
		await loadNotesList();
		await loadNote('Notes');
	}

	function workspaceMeta(id: string) {
		return WORKSPACES.find((w) => w.id === id) ?? { icon: '◈', color: 'var(--color-text-muted)', label: id };
	}

	const SLASH_COMMANDS = [
		{ cmd: '/note <text>', desc: 'Append text to the Notes scratchpad from any chat' },
		{ cmd: '/newjob', desc: 'Start a new job in Business — prompts for all fields' },
	];
</script>

<div class="flex h-full">
	<!-- Note list sidebar -->
	<aside class="w-52 shrink-0 flex flex-col py-4 gap-1 overflow-y-auto" style="border-right: 1px solid var(--color-border)">
		<div class="flex items-center justify-between px-4 mb-2">
			<span class="text-xs font-semibold uppercase tracking-wide" style="color: var(--color-text-muted)">Notes</span>
			<button
				onclick={() => { showNewInput = !showNewInput; newNoteTitle = ''; }}
				class="text-xs px-1.5 py-0.5 rounded hover:opacity-80 transition-opacity"
				style="color: var(--color-hive); background: var(--color-surface-2)"
				title="New note"
			>+ New</button>
		</div>

		{#if showNewInput}
			<form onsubmit={(e) => { e.preventDefault(); createNote(); }} class="px-3 mb-1">
				<input
					bind:value={newNoteTitle}
					placeholder="Note title…"
					autofocus
					class="w-full text-xs px-2 py-1.5 rounded outline-none"
					style="background: var(--color-surface-2); color: var(--color-text); border: 1px solid var(--color-hive)"
					onkeydown={(e) => e.key === 'Escape' && (showNewInput = false)}
				/>
			</form>
		{/if}

		{#each notes as note}
			<div class="group flex items-center gap-1 px-3">
				<button
					onclick={() => loadNote(note.title)}
					class="flex-1 text-left text-xs py-1.5 px-2 rounded truncate transition-colors"
					style={activeTitle === note.title
						? 'background: var(--color-surface-3); color: var(--color-text); font-weight: 500'
						: 'color: var(--color-text-muted)'}
				>{note.title}</button>
				{#if note.title !== 'Notes'}
					<button
						onclick={() => handleDeleteNote(note.title)}
						class="opacity-0 group-hover:opacity-60 hover:!opacity-100 text-xs transition-opacity"
						style="color: var(--color-text-muted)"
						title="Delete"
					>✕</button>
				{/if}
			</div>
		{/each}
	</aside>

	<!-- Editor -->
	<div class="flex flex-col flex-1 min-w-0">
		<header class="flex items-center gap-3 px-6 py-4 shrink-0" style="border-bottom: 1px solid var(--color-border)">
			<span style="color: var(--color-hive)" class="text-xl">◇</span>
			<span class="font-semibold" style="color: var(--color-text)">{activeTitle}</span>
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
				placeholder="Start typing… supports markdown."
				class="w-full h-full resize-none text-sm outline-none leading-relaxed"
				style="background: transparent; color: var(--color-text); min-height: 400px; field-sizing: content;"
			></textarea>
		</div>
	</div>

	<!-- Right panel -->
	<aside class="w-56 shrink-0 flex flex-col py-6 px-4 gap-6 overflow-y-auto" style="border-left: 1px solid var(--color-border)">
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
