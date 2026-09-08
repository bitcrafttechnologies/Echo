<script lang="ts">
  import { onMount } from 'svelte';
  import {
    ACTIVE_TASK_STATUSES,
    cancelTask,
    fetchTask,
    fetchTasks,
    taskDepth,
    type TaskHistoryEntry
  } from '$lib/echo-client';

  export let apiBase: string;
  export let eventRevision = 0;

  let tasks: TaskHistoryEntry[] = [];
  let selected: TaskHistoryEntry | null = null;
  let loading = true;
  let error = '';
  let cancelling = false;
  let confirmingCancel = false;
  let mounted = false;
  let seenRevision = -1;

  $: activeTasks = tasks.filter((task) => ACTIVE_TASK_STATUSES.has(task.status));
  $: historicalTasks = tasks.filter((task) => !ACTIVE_TASK_STATUSES.has(task.status));
  $: if (mounted && eventRevision !== seenRevision) {
    seenRevision = eventRevision;
    void loadTasks(false);
  }

  onMount(() => {
    mounted = true;
    void loadTasks();
  });

  async function loadTasks(showLoading = true) {
    if (showLoading) loading = true;
    try {
      tasks = await fetchTasks(window.fetch.bind(window), apiBase, { limit: 200 });
      if (selected) selected = tasks.find((task) => task.id === selected?.id) ?? selected;
      else selected = activeTasks[0] ?? tasks[0] ?? null;
      error = '';
    } catch {
      error = 'Task history is unavailable.';
    } finally {
      loading = false;
    }
  }

  async function selectTask(task: TaskHistoryEntry) {
    confirmingCancel = false;
    selected = task;
    try {
      selected = await fetchTask(window.fetch.bind(window), task.id, apiBase);
    } catch {
      error = 'The latest Task detail could not be loaded.';
    }
  }

  async function confirmCancellation() {
    if (!selected) return;
    cancelling = true;
    try {
      selected = await cancelTask(window.fetch.bind(window), selected.id, apiBase);
      confirmingCancel = false;
      await loadTasks(false);
    } catch (cause) {
      error = cause instanceof Error ? cause.message : 'Task cancellation failed.';
    } finally {
      cancelling = false;
    }
  }

  function displayTime(value: string | null): string {
    if (!value) return '—';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
  }

  function pretty(value: unknown): string {
    return JSON.stringify(value, null, 2);
  }
</script>

<section class="inspector" aria-labelledby="tasks-heading">
  <header>
    <div><p>Task inspector</p><h2 id="tasks-heading">Runtime work</h2></div>
    <button type="button" onclick={() => loadTasks()} disabled={loading}>{loading ? 'Loading…' : 'Refresh'}</button>
  </header>
  {#if error}<div class="error" role="status">{error}</div>{/if}

  <div class="task-grid">
    <section class="pane" aria-labelledby="active-tasks-heading">
      <div class="pane-title"><h3 id="active-tasks-heading">Active Tasks</h3><span>{activeTasks.length}</span></div>
      {#if activeTasks.length}
        <ul class="task-list">
          {#each activeTasks as task (task.id)}
            <li><button class:selected={selected?.id === task.id} onclick={() => selectTask(task)} type="button">
              <strong>{task.name}</strong><span>{task.owner} · {task.status}</span>
            </button></li>
          {/each}
        </ul>
      {:else}<p class="empty">No Tasks are active.</p>{/if}
    </section>

    <section class="pane history" aria-labelledby="task-history-heading">
      <div class="pane-title"><h3 id="task-history-heading">Task history</h3><span>{historicalTasks.length}</span></div>
      {#if historicalTasks.length}
        <ul class="task-list hierarchy">
          {#each historicalTasks as task (task.id)}
            <li style={`--depth:${taskDepth(task, tasks)}`}>
              <button class:selected={selected?.id === task.id} onclick={() => selectTask(task)} type="button">
                <strong>{task.name}</strong><span>{task.status} · {displayTime(task.completed_at)}</span>
              </button>
            </li>
          {/each}
        </ul>
      {:else}<p class="empty">No completed Tasks are retained.</p>{/if}
    </section>

    <aside class="pane detail" aria-labelledby="task-detail-heading">
      <div class="pane-title"><h3 id="task-detail-heading">Task detail</h3></div>
      {#if selected}
        <dl>
          <div class="wide"><dt>ID</dt><dd>{selected.id}</dd></div>
          <div><dt>Status</dt><dd><span class="status">{selected.status}</span></dd></div>
          <div><dt>Owner</dt><dd>{selected.owner}</dd></div>
          <div><dt>Priority</dt><dd>{selected.priority}</dd></div>
          <div><dt>Signal</dt><dd>{selected.signal_id ?? '—'}</dd></div>
          <div><dt>Parent</dt><dd>{selected.parent ?? 'Root Task'}</dd></div>
          <div class="wide"><dt>Children</dt><dd>{selected.children.length ? selected.children.join(', ') : 'None'}</dd></div>
          <div><dt>Created</dt><dd>{displayTime(selected.created_at)}</dd></div>
          <div><dt>Started</dt><dd>{displayTime(selected.started_at)}</dd></div>
          <div class="wide"><dt>Completed</dt><dd>{displayTime(selected.completed_at)}</dd></div>
        </dl>
        {#if selected.error}<div class="error">{selected.error}</div>{/if}
        {#if selected.result !== null}<section class="data"><h4>Result</h4><pre>{pretty(selected.result)}</pre></section>{/if}
        {#if ACTIVE_TASK_STATUSES.has(selected.status)}
          <div class="cancel-zone">
            {#if confirmingCancel}
              <span>Cancel this live Task?</span>
              <button class="danger" onclick={confirmCancellation} disabled={cancelling} type="button">{cancelling ? 'Cancelling…' : 'Confirm cancel'}</button>
              <button onclick={() => (confirmingCancel = false)} type="button">Keep running</button>
            {:else}
              <button class="danger" onclick={() => (confirmingCancel = true)} type="button">Cancel Task</button>
            {/if}
          </div>
        {/if}
      {:else}<p class="empty">Select a Task to inspect it.</p>{/if}
    </aside>
  </div>
</section>

<style>
  .inspector { width:min(100%,86rem); margin:0 auto; }
  header,.pane-title,.cancel-zone { display:flex; align-items:center; justify-content:space-between; gap:1rem; }
  header { margin-bottom:1.4rem; }
  h2,h3,h4,p { margin:0; } h2{font-size:clamp(1.75rem,3vw,2.5rem)} h3{font-size:1rem} h4{font-size:.82rem;color:#aab8c8}
  header p { color:#6f8093;font: .75rem ui-monospace,monospace; letter-spacing:.09em;text-transform:uppercase;margin-bottom:.4rem }
  button { border:1px solid #314153;border-radius:.4rem;color:#dce6ef;background:#14202c;padding:.6rem .85rem;cursor:pointer }
  button:hover,button:focus-visible{border-color:#4ccbc5;outline:none} button:disabled{opacity:.55;cursor:wait}
  .error{margin:.7rem 0;padding:.75rem;border:1px solid #5d3038;border-radius:.4rem;color:#efb1b6;background:#251218}
  .task-grid{display:grid;grid-template-columns:minmax(15rem,.8fr) minmax(18rem,1fr) minmax(22rem,1.25fr);gap:1px;border:1px solid #1d2a38;border-radius:.6rem;overflow:hidden;background:#1d2a38;min-height:36rem}
  .pane{min-width:0;background:#0d141d}.pane-title{padding:1rem 1.1rem;border-bottom:1px solid #1d2a38}.pane-title span{color:#657487;font: .75rem ui-monospace,monospace}
  .task-list{list-style:none;margin:0;padding:.4rem;display:grid;gap:.2rem}.task-list button{display:grid;gap:.35rem;width:100%;padding:.8rem;text-align:left;border-color:transparent;background:transparent}.task-list button.selected{border-color:#24676b;background:#10252c}.task-list strong{font-size:.9rem}.task-list span{color:#7f8d9d;font-size:.76rem}.hierarchy li{margin-left:min(calc(var(--depth) * 1rem),3rem)}
  .detail{padding-bottom:1rem}.detail dl{display:grid;grid-template-columns:1fr 1fr;gap:1px;margin:0;background:#1a2633}.detail dl div{min-width:0;padding:.8rem 1rem;background:#101821}.detail dl .wide{grid-column:1/-1}dt{color:#657487;font-size:.72rem;text-transform:uppercase}dd{margin:.3rem 0 0;overflow-wrap:anywhere;color:#cbd6e1;font: .78rem ui-monospace,monospace}.status{color:#57d7ce}.data{margin:1rem}.data h4{margin-bottom:.5rem}pre{overflow:auto;margin:0;padding:.8rem;border:1px solid #223142;border-radius:.35rem;color:#a9c2c8;background:#091019;font: .75rem/1.55 ui-monospace,monospace}.cancel-zone{justify-content:flex-start;flex-wrap:wrap;margin:1rem;padding-top:1rem;border-top:1px solid #263342}.cancel-zone span{color:#efb1b6}.danger{border-color:#63323a;color:#ffbdc2;background:#2b141a}.empty{padding:1.5rem;color:#657487;font-size:.88rem}
  @media(max-width:1050px){.task-grid{grid-template-columns:1fr 1fr}.detail{grid-column:1/-1}} @media(max-width:700px){.task-grid{display:block}.pane{min-height:12rem;border-bottom:1px solid #1d2a38}}
</style>
