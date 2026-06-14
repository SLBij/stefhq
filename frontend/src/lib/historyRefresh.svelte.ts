// Shared signal — page bumps this after saving a new conversation; layout re-fetches.
export const historyRefresh = $state({ tick: 0 });
