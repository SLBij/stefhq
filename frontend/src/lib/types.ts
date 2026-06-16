export type Workspace = 'hive_mind' | 'business' | 'plant_atlas' | 'round_table' | 'inbox';

export interface WorkspaceMeta {
	id: Workspace;
	label: string;
	icon: string;
	color: string;
}

export const WORKSPACES: WorkspaceMeta[] = [
	{ id: 'hive_mind',    label: 'Hive Mind',    icon: '◈', color: 'var(--color-hive)'     },
	{ id: 'business',     label: 'Business',     icon: '◉', color: 'var(--color-business)'  },
	{ id: 'plant_atlas',  label: 'Plant Atlas',  icon: '◍', color: 'var(--color-plant)'     },
	{ id: 'round_table',  label: 'Round Table',  icon: '◎', color: 'var(--color-round)'     },
	{ id: 'inbox',        label: 'Inbox',        icon: '◌', color: 'var(--color-inbox)'     },
];

export interface SavedMemory {
	content: string;
	workspace: string;
	type: string;
	confidence: number;
	tags: string[];
}

export interface Message {
	key: string;       // stable client-side key for #each
	id: string;        // server message ID (set after done event)
	role: 'user' | 'assistant';
	content: string;
	streaming?: boolean;
	status?: string;
	memories?: SavedMemory[];      // populated after worker runs
	memoriesPending?: boolean;     // waiting for worker
}

export interface SSEEvent {
	event: string;
	data: Record<string, string>;
}

export interface TaskItem {
	id: string;
	title: string;
	description: string | null;
	status: string;
	priority: 'low' | 'medium' | 'high';
	due_date: string | null;
	tags: string[];
	source: string;
	created_at: string;
	updated_at: string;
}

export interface ConversationSummary {
	id: string;
	workspace: string;
	title: string | null;
	preview: string;
	updated_at: string;
	message_count: number;
}
