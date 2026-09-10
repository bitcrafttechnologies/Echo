<script lang="ts">
  import { onMount } from 'svelte';
  import {
    fetchConfiguration,
    reloadConfiguration,
    updateConfiguration,
    type ConfigurationField,
    type ConfigurationInspection,
    type ConfigurationReloadResult
  } from '$lib/echo-client';

  export let apiBase: string;
  let inspection: ConfigurationInspection | null = null;
  let drafts: Record<string, string> = {};
  let loading = true;
  let error = '';
  let result: ConfigurationReloadResult | null = null;

  $: sections = Object.entries(
    (inspection?.fields ?? []).reduce<Record<string, ConfigurationField[]>>((groups, field) => {
      const section = field.path.split('.')[0];
      (groups[section] ??= []).push(field);
      return groups;
    }, {})
  );

  onMount(() => { void load(); });

  function displayValue(field: ConfigurationField): string {
    if (Array.isArray(field.value)) return field.value.join(', ');
    if (field.value === null) return '';
    return String(field.value);
  }

  function initializeDrafts(next: ConfigurationInspection) {
    drafts = Object.fromEntries(next.fields.map((field) => [field.path, displayValue(field)]));
  }

  async function load() {
    loading = true;
    try {
      inspection = await fetchConfiguration(window.fetch.bind(window), apiBase);
      initializeDrafts(inspection);
      error = '';
    } catch (reason) {
      error = reason instanceof Error ? reason.message : 'Configuration is unavailable.';
    } finally {
      loading = false;
    }
  }

  function parsedValue(field: ConfigurationField): unknown {
    const draft = drafts[field.path] ?? '';
    if (Array.isArray(field.value)) {
      return draft.split(',').map((item) => item.trim()).filter(Boolean);
    }
    if (typeof field.value === 'number') return Number(draft);
    if (typeof field.value === 'boolean') return draft === 'true';
    return draft;
  }

  async function apply(field: ConfigurationField) {
    loading = true;
    try {
      result = await updateConfiguration(
        window.fetch.bind(window),
        { [field.path]: parsedValue(field) },
        apiBase
      );
      await load();
      error = '';
    } catch (reason) {
      error = reason instanceof Error ? reason.message : 'Configuration update failed.';
      loading = false;
    }
  }

  async function reload() {
    loading = true;
    try {
      result = await reloadConfiguration(window.fetch.bind(window), apiBase);
      await load();
      error = '';
    } catch (reason) {
      error = reason instanceof Error ? reason.message : 'Configuration reload failed.';
      loading = false;
    }
  }
</script>

<section class="configuration" aria-labelledby="configuration-heading">
  <header>
    <div><p>Effective runtime settings</p><h2 id="configuration-heading">Configuration</h2></div>
    <div class="actions">
      <button type="button" onclick={reload} disabled={loading}>Reload file</button>
      <button type="button" onclick={load} disabled={loading}>{loading ? 'Loading…' : 'Refresh'}</button>
    </div>
  </header>
  <p class="source">Source: <code>{inspection?.source ?? 'defaults and environment'}</code></p>
  <div class="legend"><span class="live">Live editable</span><span class="restart">Restart required</span><span class="hidden">Secret / hidden</span></div>
  {#if error}<pre class="error" role="alert">{error}</pre>{/if}
  {#if result}
    <div class:warning={!result.applied} class="result" role="status">
      {result.status.replace('_', ' ')} · {result.changes.length} changed setting(s)
      {#if result.restart_required_changes.length} — nothing applied; restart Echo for the marked changes.{/if}
    </div>
  {/if}
  {#each sections as [section, fields]}
    <section class="panel">
      <h3>{section}</h3>
      <div class="fields">
        {#each fields as field}
          <article>
            <div class="field-heading">
              <code>{field.path}</code>
              {#if field.secret}<span class="hidden">Secret / hidden</span>
              {:else if field.editable}<span class="live">Live editable</span>
              {:else}<span class="restart">Restart required</span>{/if}
            </div>
            {#if field.secret}
              <strong>{field.configured ? 'Configured — value hidden' : 'Not configured — value hidden'}</strong>
            {:else if field.editable}
              <div class="editor">
                {#if field.path === 'providers.mode'}
                  <select bind:value={drafts[field.path]} aria-label={field.path}>
                    <option value="auto">auto</option><option value="remote">remote</option>
                    <option value="lan">lan</option><option value="offline">offline</option>
                  </select>
                {:else if typeof field.value === 'boolean'}
                  <select bind:value={drafts[field.path]} aria-label={field.path}>
                    <option value="true">true</option><option value="false">false</option>
                  </select>
                {:else}
                  <input
                    type={typeof field.value === 'number' ? 'number' : 'text'}
                    min={typeof field.value === 'number' ? '1' : undefined}
                    bind:value={drafts[field.path]}
                    aria-label={field.path}
                  />
                {/if}
                <button type="button" onclick={() => apply(field)} disabled={loading}>Apply</button>
              </div>
            {:else}<strong>{displayValue(field) || '—'}</strong>{/if}
            <small>{field.reason}</small>
          </article>
        {/each}
      </div>
    </section>
  {/each}
</section>

<style>
  .configuration{width:min(100%,90rem);margin:0 auto}header,.actions,.legend,.field-heading,.editor{display:flex;align-items:center;gap:.75rem}header{justify-content:space-between;margin-bottom:.6rem}.actions{justify-content:flex-end}header p{margin:0 0 .4rem;color:#6f8093;font:.75rem ui-monospace,monospace;letter-spacing:.08em;text-transform:uppercase}h2,h3{margin:0}h2{font-size:clamp(1.75rem,3vw,2.5rem)}h3{text-transform:capitalize}.source{color:#8999aa}.legend{margin:1rem 0;flex-wrap:wrap}.legend span,.field-heading span{border:1px solid;padding:.2rem .45rem;border-radius:999px;font-size:.68rem;text-transform:uppercase;letter-spacing:.04em}.live{color:#65e3da;border-color:#2f7774!important}.restart{color:#f4c96b;border-color:#765f2f!important}.hidden{color:#c6a9e8;border-color:#624c7d!important}.panel{margin-top:.8rem;padding:1rem;border:1px solid #1d2a38;border-radius:.5rem;background:#0d141d}.fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(22rem,1fr));gap:.7rem;margin-top:1rem}article{display:grid;gap:.65rem;padding:.8rem;border:1px solid #263342;border-radius:.4rem}.field-heading{justify-content:space-between}code{color:#8eb6bd;font:.78rem ui-monospace,monospace;overflow-wrap:anywhere}strong{font-size:.9rem;overflow-wrap:anywhere}small{color:#718196}.editor input,.editor select{min-width:0;flex:1;border:1px solid #314153;border-radius:.35rem;background:#09111a;color:#e7edf5;padding:.55rem}.editor button,header button{border:1px solid #314153;border-radius:.4rem;color:#dce6ef;background:#14202c;padding:.55rem .8rem;cursor:pointer}.error{white-space:pre-wrap;padding:.8rem;border:1px solid #5d3038;color:#efb1b6;background:#251218}.result{padding:.75rem;border:1px solid #2f7774;color:#8ae4dc;background:#10282d}.result.warning{border-color:#765f2f;color:#f4c96b;background:#292312}@media(max-width:700px){header{align-items:flex-start;flex-direction:column}.fields{grid-template-columns:1fr}.field-heading{align-items:flex-start;flex-direction:column}}
</style>
