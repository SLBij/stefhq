<script lang="ts">
	import '../app.css';
	let { children } = $props();
</script>

<svelte:head>
	<title>Stef HQ</title>
</svelte:head>

{@render children()}
