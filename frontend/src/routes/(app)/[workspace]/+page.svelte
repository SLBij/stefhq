<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { marked } from 'marked';
	import { getConversationMessages, getRecentMemories, streamChat } from '$lib/api';
	import { auth } from '$lib/auth.svelte';
	import { historyRefresh } from '$lib/historyRefresh.svelte';
	import { WORKSPACES, type Message, type Workspace } from '$lib/types';

	let memoryOpen = $state<Record<string, boolean>>({});

	marked.setOptions({ breaks: true });

	let workspace = $derived(page.params.workspace as Workspace);
	let meta = $derived(WORKSPACES.find((w) => w.id === workspace) ?? WORKSPACES[0]);

	let messages = $state<Message[]>([]);
	let conversationId = $state<string | undefined>(undefined);
	let input = $state('');
	let isStreaming = $state(false);
	let isLoadingHistory = $state(false);
	let viewport: HTMLDivElement;

	$effect(() => {
		const _ws = workspace;
		const cid = page.url.searchParams.get('c');

		// Don't reset when goto() just added ?c= for a conversation we're already in
		if (cid !== null && cid === conversationId) return;

		messages = [];
		conversationId = undefined;

		if (cid && auth.token) {
			isLoadingHistory = true;
			getConversationMessages(auth.token, cid).then((msgs) => {
				conversationId = cid;
				messages = msgs.map((m) => ({
					key: crypto.randomUUID(),
					id: m.id,
					role: m.role as 'user' | 'assistant',
					content: m.content,
				}));
				isLoadingHistory = false;
				scrollToBottom();
			});
		}
	});

	function scrollToBottom() {
		setTimeout(() => viewport?.scrollTo({ top: viewport.scrollHeight, behavior: 'smooth' }), 10);
	}

	function append(msg: Message) {
		messages.push(msg);
	}

	function updateLast(updater: (m: Message) => Message) {
		if (messages.length === 0) return;
		messages[messages.length - 1] = updater(messages[messages.length - 1]);
	}

	async function send(e: SubmitEvent) {
		e.preventDefault();
		const text = input.trim();
		if (!text || isStreaming || !auth.token) return;

		input = '';
		isStreaming = true;

		append({ key: crypto.randomUUID(), id: '', role: 'user', content: text });
		append({ key: crypto.randomUUID(), id: '', role: 'assistant', content: '', streaming: true });
		scrollToBottom();

		try {
			for await (const { event, data } of streamChat(auth.token, text, workspace, conversationId)) {
				if (event === 'token') {
					updateLast((m) => ({ ...m, content: m.content + (data.content ?? '') }));
					scrollToBottom();
				} else if (event === 'status') {
					updateLast((m) => ({ ...m, status: data.message }));
				} else if (event === 'done') {
					updateLast((m) => ({ ...m, id: data.message_id, streaming: false, status: undefined, memoriesPending: true }));
					const isNew = !conversationId;
					conversationId = data.conversation_id;
					if (isNew) {
						historyRefresh.tick++;
						goto(`/${workspace}?c=${conversationId}`, { replaceState: true, keepFocus: true, noScroll: true });
					}
					// Poll for memories — Ollama embedding takes ~20s, so retry at 10s, 25s, 40s
					const msgKey = messages[messages.length - 1]?.key;
					if (auth.token && msgKey) {
						const delays = [10_000, 15_000, 15_000]; // cumulative: 10s, 25s, 40s
						const poll = async () => {
							const mems = await getRecentMemories(auth.token!);
							const idx = messages.findIndex((m) => m.key === msgKey);
							if (idx === -1) return;
							if (mems.length > 0) {
								messages[idx] = { ...messages[idx], memories: mems, memoriesPending: false };
							} else if (delays.length > 0) {
								setTimeout(poll, delays.shift()!);
							} else {
								messages[idx] = { ...messages[idx], memoriesPending: false };
							}
						};
						setTimeout(poll, delays.shift()!);
					}
				} else if (event === 'error') {
					updateLast((m) => ({ ...m, content: `Error: ${data.message}`, streaming: false }));
				}
			}
		} catch {
			updateLast((m) => ({ ...m, content: 'Connection error', streaming: false }));
		} finally {
			isStreaming = false;
		}
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			send(e as unknown as SubmitEvent);
		}
	}
</script>

<div class="flex flex-col h-full">
	<header class="flex items-center gap-3 px-6 py-4 shrink-0" style="border-bottom: 1px solid var(--color-border)">
		<span style="color: {meta.color}" class="text-xl">{meta.icon}</span>
		<span class="font-semibold" style="color: var(--color-text)">{meta.label}</span>
	</header>

	<div bind:this={viewport} class="flex-1 overflow-y-auto px-6 py-6 space-y-6">
		{#if isLoadingHistory}
			<div class="h-full flex items-center justify-center">
				<p class="text-sm" style="color: var(--color-text-muted)">Loading…</p>
			</div>
		{:else if messages.length === 0}
			<div class="h-full flex items-center justify-center">
				<div class="text-center space-y-2">
					<div class="text-5xl" style="color: {meta.color}">{meta.icon}</div>
					<p class="text-sm" style="color: var(--color-text-muted)">{meta.label}</p>
				</div>
			</div>
		{/if}

		{#each messages as msg (msg.key)}
			{#if msg.role === 'user'}
				<div class="flex justify-end">
					<div class="max-w-lg px-4 py-3 rounded-2xl text-sm leading-relaxed whitespace-pre-wrap"
						style="background: var(--color-surface-3); color: var(--color-text)">
						{msg.content}
					</div>
				</div>
			{:else}
				<div class="flex gap-3 max-w-2xl">
					<div class="shrink-0 w-7 h-7 rounded-full flex items-center justify-center text-xs mt-0.5"
						style="background: {meta.color}20; color: {meta.color}">
						{meta.icon}
					</div>
					<div class="flex-1 min-w-0">
						{#if msg.streaming && !msg.content}
							<div class="flex gap-1 py-2">
								<span class="w-1.5 h-1.5 rounded-full animate-bounce" style="background: var(--color-text-muted); animation-delay: 0ms"></span>
								<span class="w-1.5 h-1.5 rounded-full animate-bounce" style="background: var(--color-text-muted); animation-delay: 150ms"></span>
								<span class="w-1.5 h-1.5 rounded-full animate-bounce" style="background: var(--color-text-muted); animation-delay: 300ms"></span>
							</div>
						{:else}
							<div class="prose">
								{@html marked(msg.content)}{#if msg.streaming}<span class="inline-block w-0.5 h-4 ml-0.5 animate-pulse" style="background: {meta.color}; vertical-align: middle"></span>{/if}
							</div>
						{/if}
						{#if msg.status}
							<p class="text-xs mt-1" style="color: var(--color-text-muted)">{msg.status}</p>
						{/if}

						{#if msg.memoriesPending}
							<div class="mt-3 flex items-center gap-1.5 text-xs" style="color: var(--color-text-muted)">
								<span class="w-1.5 h-1.5 rounded-full animate-pulse" style="background: var(--color-text-muted)"></span>
								Saving memories…
							</div>
						{:else if msg.memories && msg.memories.length > 0}
							<div class="mt-3">
								<button
									onclick={() => memoryOpen[msg.key] = !memoryOpen[msg.key]}
									class="flex items-center gap-2 text-xs px-2.5 py-1.5 rounded-lg transition-colors"
									style="background: var(--color-surface-3); color: var(--color-text-muted); border: 1px solid var(--color-border)"
								>
									<span style="color: var(--color-hive)">◈</span>
									{msg.memories.length} {msg.memories.length === 1 ? 'memory' : 'memories'} saved
									<span class="ml-auto">{memoryOpen[msg.key] ? '▾' : '▸'}</span>
								</button>

								{#if memoryOpen[msg.key]}
									<div class="mt-1.5 space-y-1.5 pl-1">
										{#each msg.memories as mem}
											<div class="px-3 py-2.5 rounded-lg text-xs space-y-1" style="background: var(--color-surface-2); border: 1px solid var(--color-border)">
												<p style="color: var(--color-text)" class="leading-relaxed">{mem.content}</p>
												<div class="flex items-center gap-2 flex-wrap pt-0.5">
													<span class="px-1.5 py-0.5 rounded text-xs" style="background: var(--color-surface-3); color: var(--color-text-muted)">{mem.type}</span>
													<span style="color: var(--color-text-muted)">{(mem.confidence * 100).toFixed(0)}% confidence</span>
													{#if mem.tags?.length}
														{#each mem.tags as tag}
															<span class="px-1.5 py-0.5 rounded text-xs" style="background: var(--color-hive)15; color: var(--color-hive)">{tag}</span>
														{/each}
													{/if}
												</div>
											</div>
										{/each}
									</div>
								{/if}
							</div>
						{/if}
					</div>
				</div>
			{/if}
		{/each}
	</div>

	<form onsubmit={send} class="px-6 py-4 shrink-0" style="border-top: 1px solid var(--color-border)">
		<div class="flex gap-3 items-end">
			<textarea
				bind:value={input}
				onkeydown={handleKeydown}
				placeholder="Message {meta.label}…"
				disabled={isStreaming}
				rows="1"
				class="flex-1 resize-none px-4 py-3 rounded-xl text-sm outline-none disabled:opacity-50 transition-colors leading-relaxed"
				style="background: var(--color-surface-3); border: 1px solid var(--color-border); color: var(--color-text); max-height: 160px; field-sizing: content;"
			></textarea>
			<button
				type="submit"
				disabled={isStreaming || !input.trim()}
				class="px-4 py-3 rounded-xl text-sm font-medium transition-opacity disabled:opacity-30 shrink-0"
				style="background: {meta.color}; color: #0d0d0f;"
			>
				{isStreaming ? '…' : '↑'}
			</button>
		</div>
		<p class="text-xs mt-2" style="color: var(--color-text-muted)">Enter to send · Shift+Enter for newline</p>
	</form>
</div>
