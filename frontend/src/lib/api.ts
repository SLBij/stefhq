import type { ActivityEntry, ConversationSummary, Message, SavedMemory, TaskItem, Workspace } from './types';

import { PUBLIC_API_BASE } from '$env/static/public';
const BASE = PUBLIC_API_BASE;

export async function login(email: string, password: string): Promise<string> {
	const res = await fetch(`${BASE}/api/auth/login`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ email, password }),
	});
	if (!res.ok) throw new Error('Invalid credentials');
	const data = await res.json();
	return data.access_token;
}

export async function* streamChat(
	token: string,
	message: string,
	workspace: Workspace,
	conversationId?: string,
): AsyncGenerator<{ event: string; data: Record<string, string> }> {
	const res = await fetch(`${BASE}/api/chat/`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`,
		},
		body: JSON.stringify({ message, workspace, conversation_id: conversationId ?? null }),
	});

	if (!res.ok) throw new Error(`HTTP ${res.status}`);

	const reader = res.body!.getReader();
	const decoder = new TextDecoder();
	let buffer = '';

	while (true) {
		const { done, value } = await reader.read();

		if (value) buffer += decoder.decode(value, { stream: true });

		// sse-starlette uses \r\n\r\n; plain SSE uses \n\n — handle both
		const blocks = done ? [...buffer.split(/\r\n\r\n|\n\n/)] : buffer.split(/\r\n\r\n|\n\n/);
		buffer = done ? '' : (blocks.pop() ?? '');

		for (const block of blocks) {
			if (!block.trim() || block.trimStart().startsWith(':')) continue;
			const lines = block.split(/\r\n|\n/);
			let event = 'message';
			let dataStr = '';
			for (const line of lines) {
				if (line.startsWith('event: ')) event = line.slice(7).trim();
				if (line.startsWith('data: ')) dataStr = line.slice(6).trim();
			}
			if (dataStr) {
				try {
					yield { event, data: JSON.parse(dataStr) };
				} catch {
					// skip malformed
				}
			}
		}

		if (done) break;
	}
}

export async function getConversations(token: string, workspace: string): Promise<ConversationSummary[]> {
	const res = await fetch(`${BASE}/api/conversations/?workspace=${workspace}`, {
		headers: { Authorization: `Bearer ${token}` },
	});
	if (!res.ok) return [];
	return res.json();
}

export async function getConversationMessages(
	token: string,
	id: string,
): Promise<{ id: string; role: string; content: string }[]> {
	const res = await fetch(`${BASE}/api/conversations/${id}/messages`, {
		headers: { Authorization: `Bearer ${token}` },
	});
	if (!res.ok) return [];
	return res.json();
}

export async function getAgentName(token: string, workspace: string): Promise<string | null> {
	const res = await fetch(`${BASE}/api/memory/agent-name?workspace=${workspace}`, {
		headers: { Authorization: `Bearer ${token}` },
	});
	if (!res.ok) return null;
	const data = await res.json();
	return data.name ?? null;
}

export async function getRecentMemories(token: string): Promise<SavedMemory[]> {
	const res = await fetch(`${BASE}/api/memory/recent`, {
		headers: { Authorization: `Bearer ${token}` },
	});
	if (!res.ok) return [];
	return res.json();
}

export async function getTasks(token: string, status = 'active'): Promise<TaskItem[]> {
	const res = await fetch(`${BASE}/api/tasks/?status=${status}`, {
		headers: { Authorization: `Bearer ${token}` },
	});
	if (!res.ok) return [];
	return res.json();
}

export async function completeTask(token: string, id: string): Promise<void> {
	await fetch(`${BASE}/api/tasks/${id}`, {
		method: 'PATCH',
		headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
		body: JSON.stringify({ status: 'done' }),
	});
}

export async function getReviewQueue(token: string) {
	const res = await fetch(`${BASE}/api/memory/review`, {
		headers: { Authorization: `Bearer ${token}` },
	});
	return res.json();
}

export async function getActivityLogs(token: string, limit = 100): Promise<ActivityEntry[]> {
	const res = await fetch(`${BASE}/api/activity/?limit=${limit}`, {
		headers: { Authorization: `Bearer ${token}` },
	});
	if (!res.ok) return [];
	return res.json();
}

export async function reviewMemory(token: string, id: string, action: 'approve' | 'discard') {
	await fetch(`${BASE}/api/memory/review/${id}`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
		body: JSON.stringify({ action }),
	});
}
