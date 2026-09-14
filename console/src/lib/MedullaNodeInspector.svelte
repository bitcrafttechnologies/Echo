<script lang="ts">
  import { onMount } from 'svelte';
  import { authorizeMedullaNode, decideMedullaNode, fetchMedullaNodes, type MedullaNodeInspection } from './echo-client';

  let { apiBase, eventRevision = 0 }: { apiBase: string; eventRevision?: number } = $props();
  let nodes: MedullaNodeInspection[] = $state([]);
  let selectedId = $state<string | null>(null);
  let loading = $state(true);
  let busy = $state(false);
  let error = $state('');
  let reason = $state('');
  let authorization = $state('{}');
  let lastRevision = $state(-1);
  let selected = $derived(nodes.find((node) => node.node_id === selectedId) ?? nodes[0] ?? null);

  async function refresh() {
    try {
      nodes = await fetchMedullaNodes(window.fetch.bind(window), apiBase);
      if (!selectedId && nodes[0]) selectedId = nodes[0].node_id;
      error = '';
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
    } finally {
      loading = false;
    }
  }

  async function decide(decision: 'approve' | 'decline' | 'block' | 'reconsider' | 'unblock') {
    if (!selected) return;
    busy = true;
    try {
      await decideMedullaNode(window.fetch.bind(window), selected.node_id, decision, reason || null, apiBase);
      await refresh();
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
    } finally { busy = false; }
  }

  async function authorize() {
    if (!selected) return;
    busy = true;
    try {
      const parsed = JSON.parse(authorization) as Record<string, unknown>;
      await authorizeMedullaNode(window.fetch.bind(window), selected.node_id, parsed, apiBase);
      await refresh();
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
    } finally { busy = false; }
  }

  onMount(refresh);
  $effect(() => {
    if (eventRevision !== lastRevision) {
      lastRevision = eventRevision;
      if (!loading) void refresh();
    }
  });
</script>

<section aria-labelledby="medulla-heading">
  <header>
    <div><p>External embodiment</p><h2 id="medulla-heading">Medulla Nodes</h2></div>
    <button onclick={refresh} disabled={busy}>Refresh</button>
  </header>
  {#if error}<div class="error" role="alert">{error}</div>{/if}
  {#if loading}<p class="empty">Loading node candidates…</p>
  {:else if nodes.length === 0}<p class="empty">No standalone nodes have announced themselves.</p>
  {:else}
    <div class="layout">
      <nav aria-label="Observed Medulla Nodes">
        {#each nodes as node}
          <button class:active={selected?.node_id === node.node_id} onclick={() => selectedId = node.node_id}>
            <strong>{node.advertised_manifest?.node?.display_name ?? node.node_id}</strong>
            <span>{node.negotiation_state ?? 'unknown'} · {node.reachability}</span>
          </button>
        {/each}
      </nav>
      {#if selected}
        <article>
          <div class="status"><span>{selected.approval_mode} approval</span><strong>{selected.negotiation_state}</strong></div>
          <h3>{selected.advertised_manifest?.node?.display_name ?? selected.node_id}</h3>
          <code>{selected.node_id}</code>
          <h4>Provides</h4>
          <ul>{#each selected.approval_request?.provides ?? [] as item}<li>{item}</li>{/each}</ul>
          <h4>Requires</h4>
          <ul>{#each selected.approval_request?.requires ?? [] as item}<li>{item}</li>{/each}</ul>
          <label>Decision reason <input bind:value={reason} placeholder="Why this node is useful or declined" /></label>
          <div class="actions">
            <button onclick={() => decide('approve')} disabled={busy}>Approve</button>
            <button onclick={() => decide('decline')} disabled={busy}>Decline</button>
            <button onclick={() => decide('block')} disabled={busy}>Block</button>
            <button onclick={() => decide('reconsider')} disabled={busy}>Reconsider</button>
            <button onclick={() => decide('unblock')} disabled={busy}>Unblock</button>
          </div>
          <label>Separate authorization JSON<textarea bind:value={authorization} rows="7"></textarea></label>
          <button class="authorize" onclick={authorize} disabled={busy || selected.negotiation_state !== 'approved'}>Authorize requirements</button>
          <p class="note">Approval never grants requested identity, credentials, or permissions. Supply only explicitly permitted values above.</p>
        </article>
      {/if}
    </div>
  {/if}
</section>

<style>
  section { padding: 2rem; color: #dce5ef; }
  header, .status, .actions { display: flex; align-items: center; justify-content: space-between; gap: .75rem; }
  header p, h4, .note { color: #8190a1; font-size: .78rem; text-transform: uppercase; letter-spacing: .08em; }
  h2, h3 { margin: .25rem 0; } .layout { display: grid; grid-template-columns: minmax(15rem, 22rem) 1fr; gap: 1rem; margin-top: 1.5rem; }
  nav { display: grid; align-content: start; gap: .5rem; } nav button { text-align: left; }
  button, input, textarea { border: 1px solid #2a394a; border-radius: .4rem; color: #dce5ef; background: #101822; padding: .65rem .8rem; }
  button { cursor: pointer; } button:hover, button.active { border-color: #37c9be; } button:disabled { opacity: .45; cursor: not-allowed; }
  nav span { display: block; color: #8190a1; font-size: .78rem; margin-top: .25rem; }
  article { border: 1px solid #243142; border-radius: .6rem; background: #0d141d; padding: 1.25rem; }
  .status strong { color: #64ddd3; } code { color: #91a0b2; } h4 { margin: 1.25rem 0 .4rem; }
  ul { margin: .35rem 0; padding-left: 1.25rem; } label { display: grid; gap: .4rem; margin-top: 1.25rem; }
  .actions { justify-content: flex-start; flex-wrap: wrap; margin-top: .75rem; } textarea { resize: vertical; font-family: ui-monospace, monospace; }
  .authorize { margin-top: .75rem; border-color: #258c86; } .note { text-transform: none; letter-spacing: 0; line-height: 1.5; }
  .error { margin: 1rem 0; color: #ff9da5; } .empty { padding: 3rem; color: #8190a1; }
  @media (max-width: 800px) { .layout { grid-template-columns: 1fr; } }
</style>
