<script lang="ts">
	import { goto } from '$app/navigation';
	import { isAuthenticated } from '$lib/auth.svelte';

	if (isAuthenticated()) {
		goto('/hive_mind');
	} else {
		goto('/login');
	}
</script>
