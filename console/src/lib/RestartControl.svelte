<script lang="ts">
  import { onDestroy } from 'svelte';

  import {
    fetchRestartOperation,
    requestRuntimeRestart,
    type RestartOperation
  } from './echo-client';

  export let apiBase: string;
  export let onReconnect: () => void;

  const terminal = new Set(['completed', 'failed']);
  let expanded = false;
  let reason = '';
  let confirmation = '';
  let operation: RestartOperation | null = null;
  let error = '';
  let polling: number | undefined;
  let reconnectTriggered = false;

  $: running = operation !== null && !terminal.has(operation.status);
  $: ready = reason.trim().length > 0 && confirmation === 'RESTART' && !running;

  onDestroy(() => {
    if (polling !== undefined) window.clearTimeout(polling);
  });

  async function requestRestart() {
    if (!ready) return;
    error = '';
    reconnectTriggered = false;
    try {
      operation = await requestRuntimeRestart(
        window.fetch.bind(window),
        reason.trim(),
        apiBase
      );
      confirmation = '';
      if (operation.status === 'completed') {
        reconnectTriggered = true;
        onReconnect();
        return;
      }
      await poll();
    } catch (value) {
      error = value instanceof Error ? value.message : 'Runtime restart request failed.';
    }
  }

  async function poll() {
    if (!operation || terminal.has(operation.status)) return;
    try {
      operation = await fetchRestartOperation(
        window.fetch.bind(window),
        operation.operation_id,
        apiBase
      );
      if (operation.status === 'completed' && !reconnectTriggered) {
        reconnectTriggered = true;
        onReconnect();
        return;
      }
      if (operation.status === 'failed') return;
      polling = window.setTimeout(() => void poll(), 250);
    } catch {
      // The host may briefly be unavailable. Keep the operation ID and retry.
      polling = window.setTimeout(() => void poll(), 500);
    }
  }
</script>

<section class="restart-control" aria-labelledby="restart-heading">
  <div class="heading">
    <div>
      <p>Controlled operation</p>
      <h3 id="restart-heading">Runtime restart</h3>
    </div>
    <span class="preservation">State preservation enabled</span>
  </div>

  {#if !expanded}
    <div class="summary">
      <span>Creates a fresh Runtime generation and reconnects this Console.</span>
      <button type="button" onclick={() => (expanded = true)}>Prepare restart</button>
    </div>
  {:else}
    <div class="confirmation">
      <label>
        Reason
        <input bind:value={reason} disabled={running} placeholder="Why is a restart needed?" />
      </label>
      <label>
        Type <strong>RESTART</strong> to confirm
        <input bind:value={confirmation} disabled={running} autocomplete="off" />
      </label>
      <div class="actions">
        <button type="button" class="quiet" disabled={running} onclick={() => (expanded = false)}>Cancel</button>
        <button type="button" class="danger" disabled={!ready} onclick={requestRestart}>Restart Runtime</button>
      </div>
    </div>
  {/if}

  {#if operation}
    <div class="progress" aria-live="polite">
      <span class="status">{operation.status.replaceAll('_', ' ')}</span>
      <span>Task policy: {operation.task_policy}</span>
      <span>Preserve state: {operation.state_preservation_enabled ? 'enabled' : 'disabled'}</span>
      {#if operation.new_runtime_id}<span>New Runtime: {operation.new_runtime_id}</span>{/if}
      {#if operation.error}<strong class="error">{operation.error}</strong>{/if}
    </div>
  {/if}
  {#if error}<p class="error" role="alert">{error}</p>{/if}
</section>

<style>
  .restart-control{margin-top:1rem;padding:1rem;border:1px solid #263342;border-radius:.55rem;background:#0d141d}.heading,.summary,.actions,.progress{display:flex;align-items:center;gap:.75rem}.heading,.summary{justify-content:space-between}.heading p{margin:0 0 .3rem;color:#718196;font:.7rem ui-monospace,monospace;text-transform:uppercase;letter-spacing:.08em}.heading h3{margin:0}.preservation{padding:.25rem .5rem;border:1px solid #2f7774;border-radius:999px;color:#7de0d8;font-size:.72rem}.summary{margin-top:1rem;color:#8999aa}.confirmation{display:grid;grid-template-columns:1fr 1fr auto;align-items:end;gap:.75rem;margin-top:1rem}.confirmation label{display:grid;gap:.35rem;color:#aab7c5;font-size:.8rem}.confirmation input{min-width:0;padding:.6rem;border:1px solid #354556;border-radius:.35rem;background:#09111a;color:#edf3f8}.actions{justify-content:flex-end}button{padding:.55rem .75rem;border:1px solid #354556;border-radius:.4rem;background:#14202c;color:#dce6ef;cursor:pointer}button:disabled{cursor:not-allowed;opacity:.45}.danger{border-color:#8a474d;background:#3a1c22;color:#ffc3c7}.quiet{background:transparent}.progress{flex-wrap:wrap;margin-top:1rem;padding:.7rem;border-left:3px solid #4abdb5;background:#101b24;color:#91a1b1;font-size:.78rem}.status{color:#7de0d8;font-weight:700;text-transform:capitalize}.error{color:#f2a6ac;white-space:pre-wrap}@media(max-width:850px){.confirmation{grid-template-columns:1fr}.heading,.summary{align-items:flex-start;flex-direction:column}}
</style>
