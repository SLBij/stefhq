<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { marked } from 'marked';
	import { appendNote, getAgentName, getConversationMessages, getConversations, getRecentMemories, streamChat } from '$lib/api';
	import { auth } from '$lib/auth.svelte';
	import { historyRefresh } from '$lib/historyRefresh.svelte';
	import { type Attachment, WORKSPACES, type Message, type Workspace } from '$lib/types';

	let memoryOpen = $state<Record<string, boolean>>({});

	marked.setOptions({ breaks: true });

	let workspace = $derived(page.params.workspace as Workspace);
	let meta = $derived(WORKSPACES.find((w) => w.id === workspace) ?? WORKSPACES[0]);

	let messages = $state<Message[]>([]);
	let conversationId = $state<string | undefined>(undefined);
	let input = $state('');
	let isStreaming = $state(false);
	let isLoadingHistory = $state(false);
	let agentName = $state<string | null>(null);
	let pendingImages = $state<Attachment[]>([]);
	let fileInput: HTMLInputElement;
	let viewport: HTMLDivElement;

	$effect(() => {
		const _ws = workspace;
		agentName = null;
		if (auth.token) {
			getAgentName(auth.token, workspace).then((n) => { agentName = n; });
		}
	});

	$effect(() => {
		const _ws = workspace;
		const cid = page.url.searchParams.get('c');
		const isNewChat = page.url.searchParams.get('new') === '1';

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
		} else if (!cid && !isNewChat && auth.token) {
			// Auto-load the most recent conversation when switching workspace
			getConversations(auth.token, _ws).then((convs) => {
				if (convs.length > 0) {
					goto(`/${_ws}?c=${convs[0].id}`, { replaceState: true, keepFocus: true, noScroll: true });
				}
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

	function handleFileSelect(e: Event) {
		const files = (e.target as HTMLInputElement).files;
		if (!files) return;
		for (const file of files) {
			if (!file.type.startsWith('image/')) continue;
			const reader = new FileReader();
			reader.onload = () => {
				const dataUrl = reader.result as string;
				// dataUrl is "data:<media_type>;base64,<data>" — strip the prefix
				const commaIdx = dataUrl.indexOf(',');
				const data = dataUrl.slice(commaIdx + 1);
				pendingImages.push({ type: 'image', media_type: file.type, data, name: file.name });
			};
			reader.readAsDataURL(file);
		}
		// Reset so the same file can be re-selected
		(e.target as HTMLInputElement).value = '';
	}

	async function send(e: SubmitEvent) {
		e.preventDefault();
		const text = input.trim();
		if ((!text && pendingImages.length === 0) || isStreaming || !auth.token) return;

		// Intercept /note command — append to scratchpad without sending to agent
		if (text.startsWith('/note ')) {
			const noteText = text.slice(6).trim();
			if (noteText && auth.token) {
				input = '';
				await appendNote(auth.token, noteText);
				append({ key: crypto.randomUUID(), id: '', role: 'system', content: `📌 Note saved: ${noteText}` });
				scrollToBottom();
			}
			return;
		}

		const attachments = pendingImages.length > 0 ? [...pendingImages] : undefined;
		input = '';
		pendingImages = [];
		isStreaming = true;

		append({ key: crypto.randomUUID(), id: '', role: 'user', content: text, attachments });
		append({ key: crypto.randomUUID(), id: '', role: 'assistant', content: '', streaming: true });
		scrollToBottom();

		try {
			for await (const { event, data } of streamChat(auth.token, text, workspace, conversationId, attachments)) {
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

	function exportChat() {
		const date = new Date().toISOString().slice(0, 10);
		const lines = [`# ${meta.label} — ${date}`, ''];
		for (const msg of messages) {
			const speaker = msg.role === 'user' ? 'You' : meta.label;
			lines.push(`**${speaker}:** ${msg.content}`, '');
		}
		const blob = new Blob([lines.join('\n')], { type: 'text/markdown' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = `${workspace}-${date}.md`;
		a.click();
		URL.revokeObjectURL(url);
	}
</script>

<div class="flex flex-col h-full">
	<header class="flex items-center gap-3 px-6 py-4 shrink-0" style="border-bottom: 1px solid var(--color-border)">
		<span style="color: {meta.color}" class="text-xl">{meta.icon}</span>
		<div class="flex flex-col leading-tight">
			<span class="font-semibold" style="color: var(--color-text)">{meta.label}</span>
			{#if agentName}
				<span class="text-xs" style="color: {meta.color}; opacity: 0.8">{agentName}</span>
			{/if}
		</div>
		{#if messages.length > 0}
			<button
				onclick={exportChat}
				title="Export as Markdown"
				class="ml-auto text-xs px-2.5 py-1.5 rounded-lg transition-colors"
				style="background: var(--color-surface-3); color: var(--color-text-muted); border: 1px solid var(--color-border)"
			>↓ .md</button>
		{/if}
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
			{#if msg.role === 'system'}
				<div class="flex justify-center">
					<span class="text-xs px-3 py-1 rounded-full" style="background: var(--color-surface-3); color: var(--color-text-muted)">{msg.content}</span>
				</div>
			{:else if msg.role === 'user'}
				<div class="flex justify-end">
					<div class="max-w-lg flex flex-col gap-2 items-end">
						{#if msg.attachments?.length}
							<div class="flex flex-wrap gap-1.5 justify-end">
								{#each msg.attachments as att}
									<img
										src="data:{att.media_type};base64,{att.data}"
										alt={att.name ?? 'image'}
										class="rounded-xl object-cover"
										style="max-width: 200px; max-height: 200px;"
									/>
								{/each}
							</div>
						{/if}
						{#if msg.content}
							<div class="px-4 py-3 rounded-2xl text-sm leading-relaxed whitespace-pre-wrap"
								style="background: var(--color-surface-3); color: var(--color-text)">
								{msg.content}
							</div>
						{/if}
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
		<!-- Image previews -->
		{#if pendingImages.length > 0}
			<div class="flex flex-wrap gap-2 mb-2">
				{#each pendingImages as img, i}
					<div class="relative">
						<img
							src="data:{img.media_type};base64,{img.data}"
							alt={img.name ?? 'image'}
							class="rounded-lg object-cover"
							style="width: 56px; height: 56px;"
						/>
						<button
							type="button"
							onclick={() => pendingImages.splice(i, 1)}
							class="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full flex items-center justify-center text-xs leading-none"
							style="background: var(--color-surface); border: 1px solid var(--color-border); color: var(--color-text-muted)"
						>×</button>
					</div>
				{/each}
			</div>
		{/if}

		<div class="flex gap-3 items-end">
			<!-- Hidden file input -->
			<input
				bind:this={fileInput}
				type="file"
				accept="image/*"
				multiple
				class="hidden"
				onchange={handleFileSelect}
			/>
			<button
				type="button"
				onclick={() => fileInput.click()}
				disabled={isStreaming}
				class="px-3 py-3 rounded-xl text-sm transition-colors disabled:opacity-30 shrink-0"
				style="background: var(--color-surface-3); border: 1px solid var(--color-border); color: var(--color-text-muted)"
				title="Attach image"
			>
				<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
			</button>
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
				disabled={isStreaming || (!input.trim() && pendingImages.length === 0)}
				class="px-4 py-3 rounded-xl text-sm font-medium transition-opacity disabled:opacity-30 shrink-0"
				style="background: {meta.color}; color: #0d0d0f;"
			>
				{isStreaming ? '…' : '↑'}
			</button>
		</div>
		<p class="text-xs mt-2" style="color: var(--color-text-muted)">Enter to send · Shift+Enter for newline</p>
	</form>
</div>
