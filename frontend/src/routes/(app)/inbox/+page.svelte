<script lang="ts">
	import { goto } from '$app/navigation';
	import { marked } from 'marked';
	import { completeTask, getConversationMessages, getRecentMemories, getTasks, streamChat } from '$lib/api';
	import { auth } from '$lib/auth.svelte';
	import { historyRefresh } from '$lib/historyRefresh.svelte';
	import { taskRefresh } from '$lib/taskRefresh.svelte';
	import { WORKSPACES, type Message, type TaskItem } from '$lib/types';
	import { page } from '$app/state';

	marked.setOptions({ breaks: true });

	const meta = WORKSPACES.find((w) => w.id === 'inbox')!;
	const workspace = 'inbox';

	// ── Chat state ──────────────────────────────────────────────────────────────
	let messages = $state<Message[]>([]);
	let conversationId = $state<string | undefined>(undefined);
	let input = $state('');
	let isStreaming = $state(false);
	let isLoadingHistory = $state(false);
	let memoryOpen = $state<Record<string, boolean>>({});
	let viewport: HTMLDivElement;

	$effect(() => {
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
		messages.push({ key: crypto.randomUUID(), id: '', role: 'user', content: text });
		messages.push({ key: crypto.randomUUID(), id: '', role: 'assistant', content: '', streaming: true });
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
						goto(`/inbox?c=${conversationId}`, { replaceState: true, keepFocus: true, noScroll: true });
					}
					// Refresh task panel after agent responds
					taskRefresh.tick++;

					// Poll for memory panel
					const msgKey = messages[messages.length - 1]?.key;
					if (auth.token && msgKey) {
						const delays = [10_000, 15_000, 15_000];
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

	// ── Task panel state ─────────────────────────────────────────────────────────
	let tasks = $state<TaskItem[]>([]);
	let doneCount = $state(0);
	let showDone = $state(false);
	let doneTasks = $state<TaskItem[]>([]);
	let completing = $state<Set<string>>(new Set());

	async function loadTasks() {
		if (!auth.token) return;
		tasks = await getTasks(auth.token, 'active');
	}

	async function loadDone() {
		if (!auth.token) return;
		doneTasks = await getTasks(auth.token, 'done');
	}

	$effect(() => {
		taskRefresh.tick; // subscribe
		loadTasks();
	});

	// Background poll so Telegram-added tasks appear without a web action
	$effect(() => {
		const interval = setInterval(loadTasks, 30_000);
		return () => clearInterval(interval);
	});

	$effect(() => {
		if (showDone) loadDone();
	});

	async function markDone(id: string) {
		if (!auth.token) return;
		completing = new Set([...completing, id]);
		await completeTask(auth.token, id);
		tasks = tasks.filter((t) => t.id !== id);
		doneCount++;
		completing = new Set([...completing].filter((x) => x !== id));
		if (showDone) loadDone();
	}

	const PRIORITY_ORDER = { high: 0, medium: 1, low: 2 };
	const PRIORITY_COLOR: Record<string, string> = {
		high: '#ef4444',
		medium: '#f59e0b',
		low: 'var(--color-text-muted)',
	};
	const PRIORITY_LABEL: Record<string, string> = {
		high: 'High',
		medium: 'Medium',
		low: 'Low',
	};

	let grouped = $derived(
		(['high', 'medium', 'low'] as const)
			.map((p) => ({ priority: p, items: tasks.filter((t) => t.priority === p) }))
			.filter((g) => g.items.length > 0)
	);
</script>

<div class="flex flex-col h-full">
	<!-- Header -->
	<header class="flex items-center gap-3 px-6 py-4 shrink-0" style="border-bottom: 1px solid var(--color-border)">
		<span style="color: {meta.color}" class="text-xl">{meta.icon}</span>
		<span class="font-semibold" style="color: var(--color-text)">{meta.label}</span>
	</header>

	<!-- Split body -->
	<div class="flex flex-row flex-1 min-h-0">

		<!-- ── Task panel ─────────────────────────────────────────────────────── -->
		<div class="w-64 lg:w-80 shrink-0 flex flex-col min-h-0 overflow-y-auto"
			style="border-right: 1px solid var(--color-border)">

			<div class="px-4 pt-4 pb-2 shrink-0">
				<p class="text-xs font-medium uppercase tracking-wider" style="color: var(--color-text-muted)">
					Tasks · {tasks.length} open
				</p>
			</div>

			<div class="flex-1 overflow-y-auto px-3 pb-3 space-y-4">
				{#if tasks.length === 0}
					<p class="text-xs px-1 pt-2" style="color: var(--color-text-muted)">No open tasks. Tell Inbox what needs doing.</p>
				{/if}

				{#each grouped as group}
					<div>
						<p class="text-xs font-medium px-1 mb-1.5 flex items-center gap-1.5" style="color: {PRIORITY_COLOR[group.priority]}">
							<span class="w-1.5 h-1.5 rounded-full inline-block" style="background: {PRIORITY_COLOR[group.priority]}"></span>
							{PRIORITY_LABEL[group.priority]}
						</p>
						<div class="space-y-1">
							{#each group.items as task (task.id)}
								<div class="flex items-start gap-2 px-2 py-2 rounded-lg group/task transition-colors"
									style="background: var(--color-surface-2)">
									<button
										onclick={() => markDone(task.id)}
										disabled={completing.has(task.id)}
										class="shrink-0 mt-0.5 w-4 h-4 rounded-full flex items-center justify-center transition-all hover:opacity-80 disabled:opacity-40"
										style="border: 2px solid {PRIORITY_COLOR[task.priority]}; color: {PRIORITY_COLOR[task.priority]}"
										title="Mark done"
									>
										{#if completing.has(task.id)}
											<span class="text-xs leading-none">…</span>
										{:else}
											<span class="w-2 h-2 rounded-full opacity-0 group-hover/task:opacity-30 transition-opacity" style="background: {PRIORITY_COLOR[task.priority]}"></span>
										{/if}
									</button>
									<div class="flex-1 min-w-0">
										<p class="text-xs leading-relaxed" style="color: var(--color-text)">{task.title}</p>
										{#if task.due_date}
											<p class="text-xs mt-0.5" style="color: var(--color-text-muted)">{task.due_date}</p>
										{/if}
										{#if task.tags?.length}
											<div class="flex flex-wrap gap-1 mt-1">
												{#each task.tags as tag}
													<span class="text-xs px-1 py-0.5 rounded" style="background: var(--color-surface-3); color: var(--color-text-muted)">{tag}</span>
												{/each}
											</div>
										{/if}
									</div>
								</div>
							{/each}
						</div>
					</div>
				{/each}

				<!-- Done section -->
				<div>
					<button
						onclick={() => { showDone = !showDone; }}
						class="text-xs px-1 flex items-center gap-1"
						style="color: var(--color-text-muted)"
					>
						{showDone ? '▾' : '▸'} Done {doneCount > 0 ? `(${doneCount})` : ''}
					</button>
					{#if showDone}
						<div class="mt-1.5 space-y-1">
							{#each doneTasks as task (task.id)}
								<div class="flex items-start gap-2 px-2 py-2 rounded-lg"
									style="background: var(--color-surface-2); opacity: 0.5">
									<span class="shrink-0 mt-0.5 w-4 h-4 rounded border flex items-center justify-center text-xs"
										style="border-color: var(--color-text-muted); color: var(--color-text-muted)">✓</span>
									<p class="text-xs line-through" style="color: var(--color-text-muted)">{task.title}</p>
								</div>
							{/each}
						</div>
					{/if}
				</div>
			</div>
		</div>

		<!-- ── Chat ──────────────────────────────────────────────────────────── -->
		<div class="flex flex-col flex-1 min-h-0">
			<div bind:this={viewport} class="flex-1 overflow-y-auto px-6 py-6 space-y-6">
				{#if isLoadingHistory}
					<div class="h-full flex items-center justify-center">
						<p class="text-sm" style="color: var(--color-text-muted)">Loading…</p>
					</div>
				{:else if messages.length === 0}
					<div class="h-full flex items-center justify-center">
						<div class="text-center space-y-2">
							<div class="text-5xl" style="color: {meta.color}">{meta.icon}</div>
							<p class="text-sm" style="color: var(--color-text-muted)">What needs doing?</p>
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
						placeholder="What needs doing?"
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
	</div>
</div>
