<script lang="ts">
  import { onMount } from 'svelte';
  import {
    fetchProviders,
    reloadConfiguration,
    setProviderMode,
    type ConfigurationReloadResult,
    type ProviderInspection
  } from '$lib/echo-client';

  export let apiBase: string;
  let status: ProviderInspection | null = null;
  let loading = true;
  let error = '';
  let reloadResult: ConfigurationReloadResult | null = null;
  const modes = ['auto', 'remote', 'lan', 'offline'] as const;

  onMount(() => { void load(); });

  async function load() {
    loading = true;
    try {
      status = await fetchProviders(window.fetch.bind(window), apiBase);
      error = '';
    } catch {
      error = 'Provider routing status is unavailable.';
    } finally {
      loading = false;
    }
  }

  async function switchMode(mode: ProviderInspection['mode']) {
    loading = true;
    try {
      status = await setProviderMode(window.fetch.bind(window), mode, apiBase);
      error = '';
    } catch {
      error = `Could not switch inference mode to ${mode}.`;
    } finally {
      loading = false;
    }
  }

  async function reloadConfig() {
    loading = true;
    try {
      reloadResult = await reloadConfiguration(window.fetch.bind(window), apiBase);
      status = await fetchProviders(window.fetch.bind(window), apiBase);
      error = '';
    } catch {
      error = 'Configuration reload failed. Active settings were preserved.';
    } finally {
      loading = false;
    }
  }
</script>

<section class="providers" aria-labelledby="provider-heading">
  <header>
    <div><p>Intelligence routing</p><h2 id="provider-heading">Provider status</h2></div>
    <div class="header-actions">
      <button onclick={reloadConfig} disabled={loading} type="button">Reload config</button>
      <button onclick={load} disabled={loading} type="button">{loading ? 'Loading…' : 'Refresh'}</button>
    </div>
  </header>
  {#if error}<div class="error" role="status">{error}</div>{/if}
  <div class="mode" aria-label="Inference mode">
    {#each modes as mode}
      <button class:active={status?.mode === mode} onclick={() => switchMode(mode)} disabled={loading} type="button">{mode}</button>
    {/each}
  </div>
  <div class="metrics">
    <article><span>Inference mode</span><strong>{status?.mode ?? '—'}</strong></article>
    <article><span>Active provider</span><strong>{status?.active_provider?.name ?? 'None'}</strong></article>
    <article><span>Model</span><strong>{status?.model ?? '—'}</strong></article>
    <article><span>Last latency</span><strong>{status?.last_latency_ms == null ? '—' : `${status.last_latency_ms.toFixed(2)} ms`}</strong></article>
  </div>
  <section class="panel">
    <h3>Routing preference</h3>
    <p>{status?.preference?.join(' → ') ?? '—'}</p>
  </section>
  {#if reloadResult}
    <section class="panel reload" aria-live="polite">
      <h3>Configuration reload: {reloadResult.status.replace('_', ' ')}</h3>
      {#if reloadResult.restart_required_changes.length}
        <p>Nothing was applied. Restart Echo to change:</p>
        <ul>
          {#each reloadResult.restart_required_changes as change}
            <li><code>{change.path}</code><span>{change.reason}</span></li>
          {/each}
        </ul>
      {:else}
        <p>{reloadResult.changes.length} live-safe setting(s) applied.</p>
      {/if}
    </section>
  {/if}
  <section class="panel">
    <h3>Configured providers & health</h3>
    {#if status && Object.keys(status.configured_providers).length}
      <div class="provider-grid">
        {#each Object.entries(status.configured_providers) as [slot, provider]}
          <article><div><b>{slot}</b><span class:healthy={status.health[slot]?.status === 'healthy'}>{status.health[slot]?.status ?? 'unknown'}</span></div><strong>{provider.name}</strong><code>{provider.model ?? 'No model configured'}</code></article>
        {/each}
      </div>
    {:else}<p class="empty">No intelligence providers are configured. Echo Core remains available.</p>{/if}
  </section>
  <section class="panel">
    <h3>Recent failures</h3>
    {#if status?.recent_failures.length}
      <ol>{#each status.recent_failures as failure}<li><b>{failure.slot}</b><code>{failure.request_id}</code><span>{failure.error_reason}</span></li>{/each}</ol>
    {:else}<p class="empty">No recent provider failures.</p>{/if}
  </section>
  <section class="panel">
    <h3>Inference history</h3>
    {#if status?.recent_inferences.length}
      <ol>{#each status.recent_inferences as item}<li><b>{item.served_by?.provider_id ?? 'unavailable'}</b><code>{item.request_id}</code><span>{item.mode} · {item.latency_ms.toFixed(2)} ms</span></li>{/each}</ol>
    {:else}<p class="empty">No inference requests recorded.</p>{/if}
  </section>
</section>

<style>
  .providers{width:min(100%,86rem);margin:0 auto}header,.header-actions,.mode,.metrics,.provider-grid article>div,li{display:flex;align-items:center;justify-content:space-between;gap:1rem}header{margin-bottom:1.2rem}.header-actions{justify-content:flex-end}header p{margin:0 0 .4rem;color:#6f8093;font:.75rem ui-monospace,monospace;letter-spacing:.08em;text-transform:uppercase}h2,h3{margin:0}h2{font-size:clamp(1.75rem,3vw,2.5rem)}h3{font-size:.9rem;margin-bottom:.9rem}.mode{justify-content:flex-start;margin-bottom:1rem}.mode button,header button{border:1px solid #314153;border-radius:.4rem;color:#dce6ef;background:#14202c;padding:.6rem .85rem;cursor:pointer;text-transform:capitalize}.mode button.active{border-color:#4ccbc5;color:#65e3da;background:#10282d}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));margin-bottom:1rem}.metrics article,.panel{padding:1rem;border:1px solid #1d2a38;background:#0d141d}.metrics article{display:grid;gap:.45rem}.metrics span{color:#718196;font-size:.75rem}.metrics strong{overflow-wrap:anywhere}.panel{margin-top:.8rem;border-radius:.5rem}.panel p{color:#8999aa}.reload h3{text-transform:capitalize}.reload ul{margin:0;padding-left:1.2rem}.reload li{display:grid;justify-content:start}.provider-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));gap:.7rem}.provider-grid article{display:grid;gap:.5rem;padding:.85rem;border:1px solid #263342;border-radius:.4rem}.provider-grid b{text-transform:uppercase}.provider-grid span{color:#ef7b84;font-size:.75rem}.provider-grid span.healthy{color:#58d68d}code{color:#8eb6bd;font:.73rem ui-monospace,monospace;overflow-wrap:anywhere}ol{list-style:none;margin:0;padding:0}li{justify-content:flex-start;border-top:1px solid #1d2a38;padding:.7rem 0}li b{width:7rem}li code{width:16rem}li span{color:#8999aa;font-size:.8rem}.empty{color:#6f8093}.error{margin-bottom:1rem;padding:.8rem;border:1px solid #5d3038;color:#efb1b6;background:#251218}@media(max-width:800px){.metrics{grid-template-columns:1fr 1fr}li{align-items:flex-start;flex-direction:column;gap:.3rem}li b,li code{width:auto}}
</style>
