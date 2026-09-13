<script lang="ts">
  import { onDestroy } from 'svelte';

  import {
    advanceReplay,
    cancelReplay,
    fetchSignal,
    fetchSignalActions,
    fetchReplayStatus,
    filterSignals,
    recordedSignalId,
    replayMetadata,
    startReplay,
    type ReplayStatus,
    type ReplayTiming,
    type SignalHistoryEntry
  } from '$lib/echo-client';

  export let apiBase: string;
  export let history: SignalHistoryEntry[] = [];
  export let liveSignals: SignalHistoryEntry[] = [];
  export let historyLoading = false;
  export let historyError = '';
  export let paused = false;
  export let pausedCount = 0;
  export let onTogglePause: () => void;
  export let onRefresh: () => void;

  let typeFilter = '';
  let sourceFilter = '';
  let selectedSignal: SignalHistoryEntry | null = null;
  let relatedActionIds: string[] = [];
  let detailLoading = false;
  let detailError = '';
  let recordingPath = '';
  let replayTiming: ReplayTiming = 'immediate';
  let replayMultiplier = 4;
  let replay: ReplayStatus | null = null;
  let replayBusy = false;
  let replayError = '';
  let replayPollTimer: number | undefined;

  $: filters = { type: typeFilter, source: sourceFilter };
  $: filteredLive = filterSignals(liveSignals, filters);
  $: filteredHistory = filterSignals(history, filters);
  $: selectedReplay = selectedSignal ? replayMetadata(selectedSignal) : null;
  $: if (!selectedSignal && history.length > 0) {
    void selectSignal(history[0]);
  }
  $: replayActive = replay?.state === 'ready' || replay?.state === 'running';
  $: replayPercent = replay && replay.total_signals > 0
    ? Math.round((replay.next_index / replay.total_signals) * 100)
    : 0;
  $: replayInputValid = recordingPath.trim().length > 0 &&
    (replayTiming !== 'accelerated' || Number.isFinite(replayMultiplier) && replayMultiplier > 0);

  onDestroy(() => {
    if (replayPollTimer !== undefined) window.clearTimeout(replayPollTimer);
  });

  function scheduleReplayPoll() {
    if (replayPollTimer !== undefined) window.clearTimeout(replayPollTimer);
    if (!replay || replay.state !== 'running') return;
    replayPollTimer = window.setTimeout(() => {
      replayPollTimer = undefined;
      void refreshReplayStatus();
    }, 300);
  }

  function acceptReplay(next: ReplayStatus) {
    const previousIndex = replay?.next_index;
    const previousState = replay?.state;
    replay = next;
    if (
      next.next_index !== previousIndex ||
      (next.state !== previousState && ['completed', 'cancelled', 'failed'].includes(next.state))
    ) {
      onRefresh();
    }
    scheduleReplayPoll();
  }

  async function refreshReplayStatus() {
    if (!replay) return;
    try {
      acceptReplay(
        await fetchReplayStatus(window.fetch.bind(window), replay.replay_id, apiBase)
      );
    } catch (error) {
      replayError = error instanceof Error ? error.message : 'Replay status could not be loaded.';
    }
  }

  async function startSessionReplay() {
    if (!replayInputValid || replayActive) return;
    replayBusy = true;
    replayError = '';
    try {
      acceptReplay(await startReplay(window.fetch.bind(window), {
        path: recordingPath.trim(),
        mode: 'sequential',
        timing: replayTiming,
        multiplier: replayTiming === 'accelerated' ? replayMultiplier : undefined
      }, apiBase));
    } catch (error) {
      replayError = error instanceof Error ? error.message : 'Replay could not be started.';
    } finally {
      replayBusy = false;
    }
  }

  async function replaySelectedSignal() {
    if (!selectedSignal || !recordingPath.trim() || replayActive) return;
    replayBusy = true;
    replayError = '';
    try {
      acceptReplay(await startReplay(window.fetch.bind(window), {
        path: recordingPath.trim(),
        mode: 'signal',
        signalId: recordedSignalId(selectedSignal),
        timing: 'immediate'
      }, apiBase));
    } catch (error) {
      replayError = error instanceof Error ? error.message : 'Signal could not be replayed.';
    } finally {
      replayBusy = false;
    }
  }

  async function nextReplaySignal() {
    if (!replay || replay.timing !== 'manual_step' || !replayActive) return;
    replayBusy = true;
    replayError = '';
    try {
      acceptReplay(
        await advanceReplay(window.fetch.bind(window), replay.replay_id, apiBase)
      );
    } catch (error) {
      replayError = error instanceof Error ? error.message : 'Replay step failed.';
    } finally {
      replayBusy = false;
    }
  }

  async function stopReplay() {
    if (!replay || !replayActive) return;
    replayBusy = true;
    replayError = '';
    try {
      acceptReplay(
        await cancelReplay(window.fetch.bind(window), replay.replay_id, apiBase)
      );
    } catch (error) {
      replayError = error instanceof Error ? error.message : 'Replay could not be stopped.';
    } finally {
      replayBusy = false;
    }
  }

  async function selectSignal(signal: SignalHistoryEntry) {
    selectedSignal = signal;
    relatedActionIds = [];
    detailError = '';
    detailLoading = true;
    try {
      const [detail, actions] = await Promise.all([
        fetchSignal(window.fetch.bind(window), signal.id, apiBase),
        fetchSignalActions(window.fetch.bind(window), signal.id, apiBase)
      ]);
      selectedSignal = detail;
      relatedActionIds = actions.map((action) => action.id);
    } catch {
      detailError = 'The latest Signal details could not be loaded.';
    } finally {
      detailLoading = false;
    }
  }

  function pretty(value: unknown): string {
    return JSON.stringify(value, null, 2);
  }

  function displayTime(timestamp: string, full = false): string {
    const value = new Date(timestamp);
    if (Number.isNaN(value.getTime())) return timestamp;
    return full
      ? value.toLocaleString([], { dateStyle: 'medium', timeStyle: 'medium' })
      : value.toLocaleTimeString([], {
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit'
        });
  }
</script>

<section class="signal-inspector" aria-labelledby="signals-heading">
  <header class="inspector-heading">
    <div>
      <p class="eyebrow">Signal inspector</p>
      <h2 id="signals-heading">Runtime traffic</h2>
    </div>
    <div class="stream-controls">
      {#if paused && pausedCount > 0}
        <span>{pausedCount} arrived while paused</span>
      {/if}
      <button class:paused type="button" onclick={onTogglePause}>
        <span class="control-icon" aria-hidden="true">{paused ? '▶' : 'Ⅱ'}</span>
        {paused ? 'Resume stream' : 'Pause stream'}
      </button>
      <button class="secondary" type="button" onclick={onRefresh} disabled={historyLoading}>
        {historyLoading ? 'Loading…' : 'Refresh history'}
      </button>
    </div>
  </header>

  <div class="filter-bar" aria-label="Signal filters">
    <label>
      <span>Type</span>
      <input bind:value={typeFilter} placeholder="e.g. battery.low" />
    </label>
    <label>
      <span>Source</span>
      <input bind:value={sourceFilter} placeholder="e.g. sensor" />
    </label>
    <div class="filter-result">
      <strong>{filteredHistory.length}</strong>
      <span>matching retained</span>
    </div>
  </div>

  <section class="replay-console" aria-labelledby="replay-heading">
    <div class="replay-copy">
      <p class="eyebrow">Session replay</p>
      <h3 id="replay-heading">Reproduce captured behavior</h3>
      <p>Use a recording path available to the Echo host.</p>
    </div>
    <div class="replay-fields">
      <label class="path-field">
        <span>Recording path</span>
        <input
          bind:value={recordingPath}
          placeholder=".echo/session.jsonl"
          disabled={replayActive}
        />
      </label>
      <label>
        <span>Timing</span>
        <select bind:value={replayTiming} disabled={replayActive}>
          <option value="immediate">Immediate</option>
          <option value="realtime">Realtime · 1×</option>
          <option value="accelerated">Accelerated</option>
          <option value="manual_step">Manual step</option>
        </select>
      </label>
      {#if replayTiming === 'accelerated'}
        <label>
          <span>Multiplier</span>
          <input
            class="multiplier-input"
            type="number"
            min="0.1"
            step="0.1"
            bind:value={replayMultiplier}
            disabled={replayActive}
          />
        </label>
      {/if}
      <button
        class="primary-action"
        type="button"
        onclick={startSessionReplay}
        disabled={!replayInputValid || replayActive || replayBusy}
      >
        {replayBusy ? 'Starting…' : 'Start replay'}
      </button>
    </div>

    {#if replay}
      <div class="replay-status" aria-live="polite">
        <div class="progress-heading">
          <div>
            <span class="state-dot state-{replay.state}" aria-hidden="true"></span>
            <strong>{replay.state}</strong>
            <span>{replay.recording_session_id}</span>
          </div>
          <span>{replay.next_index} / {replay.total_signals} · {replayPercent}%</span>
        </div>
        <progress value={replay.next_index} max={Math.max(replay.total_signals, 1)}>
          {replayPercent}%
        </progress>
        <div class="replay-status-meta">
          <span>{replay.timing.replace('_', ' ')}</span>
          {#if replay.multiplier}<span>{replay.multiplier}× speed</span>{/if}
          <span>{replay.remaining_signals} remaining</span>
          <span>Actions: record only</span>
        </div>
        <div class="replay-actions">
          {#if replay.timing === 'manual_step' && replayActive}
            <button type="button" onclick={nextReplaySignal} disabled={replayBusy}>
              Replay next Signal
            </button>
          {/if}
          {#if replayActive}
            <button class="stop-action" type="button" onclick={stopReplay} disabled={replayBusy}>
              Stop replay
            </button>
          {/if}
        </div>
      </div>
    {/if}
    {#if replayError}<p class="replay-error" role="status">{replayError}</p>{/if}
  </section>

  {#if historyError}
    <div class="error-banner" role="status">{historyError}</div>
  {/if}

  <div class="inspector-grid">
    <section class="signal-column live-column" aria-labelledby="live-heading">
      <div class="column-heading">
        <div>
          <span class="live-dot" class:paused aria-hidden="true"></span>
          <h3 id="live-heading">Live stream</h3>
        </div>
        <span>{paused ? 'Paused' : `${filteredLive.length} visible`}</span>
      </div>

      {#if filteredLive.length > 0}
        <ol class="signal-list" aria-live={paused ? 'off' : 'polite'}>
          {#each filteredLive as signal (signal.id)}
            <li>
              <button
                class:selected={selectedSignal?.id === signal.id}
                type="button"
                onclick={() => selectSignal(signal)}
              >
                <span class="signal-title-row">
                  <span class="signal-type">{signal.type}</span>
                  {#if replayMetadata(signal)}<span class="replay-label">Replay</span>{/if}
                </span>
                <span class="signal-meta">
                  <time datetime={signal.timestamp}>{displayTime(signal.timestamp)}</time>
                  <span>{signal.source}</span>
                </span>
              </button>
            </li>
          {/each}
        </ol>
      {:else}
        <div class="column-empty">
          {#if paused}
            Live arrivals are paused.
          {:else if typeFilter || sourceFilter}
            No live Signals match these filters.
          {:else}
            Waiting for the next Signal.
          {/if}
        </div>
      {/if}
    </section>

    <section class="signal-column history-column" aria-labelledby="history-heading">
      <div class="column-heading">
        <div><h3 id="history-heading">Recent history</h3></div>
        <span>Newest first</span>
      </div>

      {#if historyLoading && history.length === 0}
        <div class="column-empty">Loading retained Signals…</div>
      {:else if filteredHistory.length > 0}
        <ol class="signal-list">
          {#each filteredHistory as signal (signal.id)}
            <li>
              <button
                class:selected={selectedSignal?.id === signal.id}
                type="button"
                onclick={() => selectSignal(signal)}
              >
                <span class="signal-title-row">
                  <span class="signal-type">{signal.type}</span>
                  {#if replayMetadata(signal)}<span class="replay-label">Replay</span>{/if}
                </span>
                <span class="signal-source">{signal.source}</span>
                <span class="signal-meta">
                  <time datetime={signal.timestamp}>{displayTime(signal.timestamp)}</time>
                  <span class="route-status">{signal.routing_result.status}</span>
                </span>
              </button>
            </li>
          {/each}
        </ol>
      {:else}
        <div class="column-empty">
          {typeFilter || sourceFilter
            ? 'No retained Signals match these filters.'
            : 'No retained Signals are available.'}
        </div>
      {/if}
    </section>

    <aside class="detail-panel" aria-labelledby="detail-heading">
      <div class="column-heading detail-heading">
        <div><h3 id="detail-heading">Signal detail</h3></div>
        {#if detailLoading}
          <span>Refreshing…</span>
        {:else if selectedSignal && replayMetadata(selectedSignal)}
          <span class="replay-label">Replayed activity</span>
        {/if}
      </div>

      {#if selectedSignal}
        <div class="detail-content">
          {#if detailError}<p class="detail-error">{detailError}</p>{/if}

          {#if selectedReplay}
            <div class="replay-origin">
              <strong>Replayed Signal</strong>
              <span>Original ID <code>{String(selectedReplay.original_signal_id ?? 'unknown')}</code></span>
              <span>Originally received {displayTime(String(selectedReplay.original_timestamp ?? ''), true)}</span>
            </div>
          {/if}

          <dl class="identity-grid">
            <div class="wide">
              <dt>ID</dt>
              <dd>{selectedSignal.id}</dd>
            </div>
            <div>
              <dt>Type</dt>
              <dd>{selectedSignal.type}</dd>
            </div>
            <div>
              <dt>Source</dt>
              <dd>{selectedSignal.source}</dd>
            </div>
            <div class="wide">
              <dt>Timestamp</dt>
              <dd>{displayTime(selectedSignal.timestamp, true)}</dd>
            </div>
          </dl>

          <button
            class="replay-signal-action"
            type="button"
            onclick={replaySelectedSignal}
            disabled={!recordingPath.trim() || replayActive || replayBusy}
            title={!recordingPath.trim() ? 'Enter the recording path above first' : undefined}
          >
            Replay this Signal
          </button>

          <section class="route-card" aria-labelledby="routing-heading">
            <div class="route-title">
              <h4 id="routing-heading">Routing result</h4>
              <span class="route-badge">{selectedSignal.routing_result.status}</span>
            </div>
            <dl class="route-summary">
              <div>
                <dt>Handlers</dt>
                <dd>{selectedSignal.routing_result.handler_count}</dd>
              </div>
              <div>
                <dt>Entities</dt>
                <dd>{selectedSignal.routing_result.entity_ids.length}</dd>
              </div>
            </dl>

            <div class="related-group">
              <span>Related Tasks</span>
              {#if selectedSignal.routing_result.task_ids.length > 0}
                {#each selectedSignal.routing_result.task_ids as taskId}
                  <code>{taskId}</code>
                {/each}
              {:else}<small>None</small>{/if}
            </div>
            <div class="related-group">
              <span>Related Actions</span>
              {#if relatedActionIds.length > 0}
                {#each relatedActionIds as actionId}<code>{actionId}</code>{/each}
              {:else}<small>None</small>{/if}
            </div>

            {#if selectedSignal.routing_result.error}
              <pre class="json error-json">{pretty(selectedSignal.routing_result.error)}</pre>
            {/if}
          </section>

          <section class="json-section">
            <h4>Payload</h4>
            <pre class="json">{pretty(selectedSignal.payload)}</pre>
          </section>
          <section class="json-section">
            <h4>Metadata</h4>
            <pre class="json">{pretty(selectedSignal.metadata)}</pre>
          </section>
          <section class="json-section">
            <h4>Task statuses</h4>
            <pre class="json">{pretty(selectedSignal.routing_result.task_statuses)}</pre>
          </section>
        </div>
      {:else}
        <div class="detail-empty">
          <span aria-hidden="true">↳</span>
          <p>Select a live or retained Signal to inspect it.</p>
        </div>
      {/if}
    </aside>
  </div>
</section>

<style>
  .signal-inspector {
    width: min(100%, 92rem);
    margin: 0 auto;
  }

  h2,
  h3,
  h4,
  p {
    margin: 0;
  }

  h2 {
    font-size: clamp(1.75rem, 3vw, 2.5rem);
    line-height: 1.1;
  }

  h3 {
    font-size: 0.95rem;
  }

  h4 {
    font-size: 0.82rem;
    font-weight: 600;
    letter-spacing: 0.03em;
  }

  .eyebrow {
    margin-bottom: 0.45rem;
    color: #7f8b9a;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.75rem;
    letter-spacing: 0.09em;
    text-transform: uppercase;
  }

  .inspector-heading,
  .stream-controls,
  .column-heading,
  .column-heading > div,
  .route-title {
    display: flex;
    align-items: center;
  }

  .inspector-heading {
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 1.5rem;
  }

  .stream-controls {
    justify-content: flex-end;
    gap: 0.65rem;
  }

  .stream-controls > span {
    color: #f4b860;
    font-size: 0.78rem;
  }

  button,
  input,
  select {
    font: inherit;
  }

  .stream-controls button {
    min-width: 8rem;
    padding: 0.58rem 0.8rem;
    border: 1px solid #286760;
    border-radius: 0.38rem;
    color: #b8f0e9;
    background: #102824;
    cursor: pointer;
  }

  .stream-controls button.paused {
    border-color: #74582d;
    color: #f6cf8e;
    background: #2a2113;
  }

  .stream-controls button.secondary {
    border-color: #344354;
    color: #b7c3d1;
    background: #131c27;
  }

  .stream-controls button:disabled {
    cursor: wait;
    opacity: 0.55;
  }

  .control-icon {
    margin-right: 0.35rem;
    font-size: 0.72rem;
  }

  .filter-bar {
    display: grid;
    grid-template-columns: minmax(12rem, 1fr) minmax(12rem, 1fr) auto;
    gap: 0.75rem;
    align-items: end;
    padding: 0.9rem;
    border: 1px solid #1d2a38;
    border-radius: 0.5rem 0.5rem 0 0;
    background: #0d141d;
  }

  .filter-bar label {
    display: grid;
    gap: 0.4rem;
  }

  .filter-bar label span,
  .filter-result span,
  .related-group > span,
  dt {
    color: #758496;
    font-size: 0.72rem;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }

  .filter-bar input {
    width: 100%;
    padding: 0.62rem 0.7rem;
    border: 1px solid #2b3949;
    border-radius: 0.35rem;
    outline: none;
    color: #dfe8f2;
    background: #090f16;
  }

  .filter-bar input:focus {
    border-color: #2cc8c0;
    box-shadow: 0 0 0 2px rgba(44, 200, 192, 0.12);
  }

  .filter-result {
    display: grid;
    grid-template-columns: auto auto;
    gap: 0.4rem;
    align-items: baseline;
    min-width: 9.5rem;
    padding-bottom: 0.55rem;
  }

  .filter-result strong {
    color: #62ded5;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }

  .replay-console {
    display: grid;
    grid-template-columns: minmax(13rem, 0.7fr) minmax(28rem, 1.8fr);
    gap: 1rem 1.5rem;
    padding: 1rem;
    border-inline: 1px solid #1d2a38;
    border-bottom: 1px solid #1d2a38;
    background: linear-gradient(110deg, #0b151d, #0d121a 70%);
  }

  .replay-copy {
    display: grid;
    align-content: center;
  }

  .replay-copy .eyebrow {
    margin-bottom: 0.3rem;
    color: #47cfc7;
  }

  .replay-copy > p:last-child {
    margin-top: 0.35rem;
    color: #718094;
    font-size: 0.8rem;
  }

  .replay-fields {
    display: grid;
    grid-template-columns: minmax(13rem, 1fr) minmax(9rem, auto) auto auto;
    gap: 0.7rem;
    align-items: end;
  }

  .replay-fields label {
    display: grid;
    gap: 0.4rem;
  }

  .replay-fields label > span {
    color: #7f8d9f;
    font-size: 0.75rem;
  }

  .replay-fields input,
  .replay-fields select {
    min-height: 2.45rem;
    padding: 0.55rem 0.65rem;
    border: 1px solid #2b3949;
    border-radius: 0.35rem;
    outline: none;
    color: #dfe8f2;
    background: #080e15;
  }

  .replay-fields input:focus,
  .replay-fields select:focus {
    border-color: #2cc8c0;
    box-shadow: 0 0 0 2px rgba(44, 200, 192, 0.12);
  }

  .multiplier-input {
    width: 6rem;
  }

  .primary-action,
  .replay-actions button,
  .replay-signal-action {
    min-height: 2.45rem;
    padding: 0.55rem 0.8rem;
    border: 1px solid #28716b;
    border-radius: 0.35rem;
    color: #d6fffb;
    background: #12302d;
    cursor: pointer;
  }

  .primary-action:disabled,
  .replay-actions button:disabled,
  .replay-signal-action:disabled {
    cursor: not-allowed;
    opacity: 0.45;
  }

  .replay-status {
    display: grid;
    grid-column: 1 / -1;
    gap: 0.65rem;
    padding-top: 0.85rem;
    border-top: 1px solid #1d2a38;
  }

  .progress-heading,
  .progress-heading > div,
  .replay-status-meta,
  .replay-actions,
  .signal-title-row {
    display: flex;
    align-items: center;
  }

  .progress-heading {
    justify-content: space-between;
    gap: 1rem;
    color: #8190a2;
    font-size: 0.78rem;
  }

  .progress-heading > div {
    gap: 0.55rem;
    min-width: 0;
  }

  .progress-heading strong {
    color: #d8e2ec;
    text-transform: capitalize;
  }

  .progress-heading > div > span:last-child {
    overflow: hidden;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .state-dot {
    width: 0.5rem;
    height: 0.5rem;
    flex: 0 0 auto;
    border-radius: 50%;
    background: #5f7082;
  }

  .state-running,
  .state-ready {
    background: #f4b860;
    box-shadow: 0 0 0.55rem rgba(244, 184, 96, 0.4);
  }

  .state-completed {
    background: #58d68d;
  }

  .state-failed,
  .state-cancelled {
    background: #ef6a73;
  }

  .replay-status progress {
    width: 100%;
    height: 0.42rem;
    overflow: hidden;
    border: 0;
    border-radius: 999px;
    color: #42cec6;
    background: #182431;
  }

  .replay-status progress::-webkit-progress-bar {
    background: #182431;
  }

  .replay-status progress::-webkit-progress-value {
    background: #42cec6;
  }

  .replay-status progress::-moz-progress-bar {
    background: #42cec6;
  }

  .replay-status-meta {
    flex-wrap: wrap;
    gap: 0.5rem 1rem;
    color: #718094;
    font-size: 0.75rem;
    text-transform: capitalize;
  }

  .replay-actions {
    gap: 0.6rem;
  }

  .replay-actions .stop-action {
    border-color: #643943;
    color: #f0b7bb;
    background: #28161c;
  }

  .replay-error {
    grid-column: 1 / -1;
    color: #efb1b6;
    font-size: 0.82rem;
  }

  .error-banner {
    padding: 0.7rem 0.9rem;
    border-inline: 1px solid #563038;
    color: #efb1b6;
    background: rgba(113, 36, 47, 0.16);
    font-size: 0.86rem;
  }

  .inspector-grid {
    display: grid;
    grid-template-columns: minmax(13rem, 0.8fr) minmax(15rem, 1fr) minmax(22rem, 1.55fr);
    min-height: 38rem;
    overflow: hidden;
    border: 1px solid #1d2a38;
    border-top: 0;
    border-radius: 0 0 0.5rem 0.5rem;
    background: #0b1119;
  }

  .signal-column,
  .detail-panel {
    min-width: 0;
  }

  .signal-column {
    border-right: 1px solid #1d2a38;
  }

  .column-heading {
    min-height: 3.4rem;
    justify-content: space-between;
    gap: 0.5rem;
    padding: 0.8rem 0.9rem;
    border-bottom: 1px solid #1d2a38;
    background: #0e1620;
  }

  .column-heading > div {
    gap: 0.55rem;
  }

  .column-heading > span {
    color: #667588;
    font-size: 0.7rem;
    text-transform: uppercase;
  }

  .live-dot {
    width: 0.46rem;
    height: 0.46rem;
    border-radius: 50%;
    background: #58d68d;
    box-shadow: 0 0 0.65rem rgba(88, 214, 141, 0.5);
  }

  .live-dot.paused {
    background: #f4b860;
    box-shadow: none;
  }

  .signal-list {
    max-height: calc(100vh - 19rem);
    margin: 0;
    padding: 0;
    overflow-y: auto;
    list-style: none;
  }

  .signal-list li {
    border-bottom: 1px solid #17212d;
  }

  .signal-list button {
    display: grid;
    gap: 0.35rem;
    width: 100%;
    padding: 0.85rem 0.9rem;
    border: 0;
    border-left: 2px solid transparent;
    color: #a8b5c4;
    text-align: left;
    background: transparent;
    cursor: pointer;
  }

  .signal-list button:hover,
  .signal-list button:focus-visible {
    background: #111b26;
    outline: none;
  }

  .signal-list button.selected {
    border-left-color: #3fd2ca;
    color: #edf5fc;
    background: rgba(37, 135, 137, 0.13);
  }

  .signal-type {
    overflow: hidden;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.82rem;
    font-weight: 600;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .signal-title-row {
    min-width: 0;
    justify-content: space-between;
    gap: 0.5rem;
  }

  .replay-label {
    flex: 0 0 auto;
    padding: 0.18rem 0.4rem;
    border: 1px solid #765a2c;
    border-radius: 999px;
    color: #f4c976;
    background: rgba(98, 70, 25, 0.2);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.65rem;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }

  .column-heading > .replay-label {
    color: #f4c976;
  }

  .replay-origin {
    display: grid;
    gap: 0.3rem;
    padding: 0.75rem;
    border: 1px solid #765a2c;
    border-radius: 0.4rem;
    color: #c9a963;
    background: rgba(98, 70, 25, 0.14);
    font-size: 0.72rem;
  }

  .replay-origin strong {
    color: #f4c976;
    text-transform: uppercase;
  }

  .replay-origin code {
    color: #f5ddad;
  }

  .replay-signal-action {
    width: 100%;
  }

  .signal-source {
    overflow: hidden;
    color: #778699;
    font-size: 0.75rem;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .signal-meta {
    display: flex;
    justify-content: space-between;
    gap: 0.5rem;
    color: #637286;
    font-size: 0.7rem;
  }

  .route-status {
    color: #5fcfc7;
    text-transform: capitalize;
  }

  .column-empty,
  .detail-empty {
    display: grid;
    min-height: 12rem;
    place-content: center;
    padding: 1.25rem;
    color: #657486;
    font-size: 0.85rem;
    text-align: center;
  }

  .detail-panel {
    background: #0a1017;
  }

  .detail-heading {
    position: sticky;
    top: 0;
    z-index: 1;
  }

  .detail-content {
    display: grid;
    gap: 1rem;
    max-height: calc(100vh - 19rem);
    padding: 1rem;
    overflow-y: auto;
  }

  .detail-error {
    color: #efb1b6;
    font-size: 0.82rem;
  }

  .identity-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.8rem;
    margin: 0;
  }

  .identity-grid div {
    min-width: 0;
  }

  .identity-grid .wide {
    grid-column: 1 / -1;
  }

  dd {
    margin: 0.25rem 0 0;
    overflow-wrap: anywhere;
    color: #c7d2df;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.78rem;
  }

  .route-card,
  .json-section {
    padding: 0.85rem;
    border: 1px solid #1c2a38;
    border-radius: 0.4rem;
    background: #0d151e;
  }

  .route-title {
    justify-content: space-between;
    margin-bottom: 0.85rem;
  }

  .route-badge {
    padding: 0.22rem 0.48rem;
    border: 1px solid #285a59;
    border-radius: 999px;
    color: #6bded7;
    font-size: 0.7rem;
    text-transform: capitalize;
  }

  .route-summary {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1rem;
    margin: 0 0 0.9rem;
  }

  .route-summary dd {
    font-size: 1.05rem;
  }

  .related-group {
    display: grid;
    gap: 0.4rem;
    margin-top: 0.75rem;
  }

  .related-group code {
    overflow-wrap: anywhere;
    color: #9eacbc;
    font-size: 0.7rem;
  }

  .related-group small {
    color: #5e6d7f;
  }

  .json-section h4 {
    margin-bottom: 0.65rem;
  }

  .json {
    margin: 0;
    overflow-x: auto;
    color: #9cc9c6;
    font: 0.75rem/1.55 ui-monospace, SFMono-Regular, Menlo, monospace;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .error-json {
    margin-top: 0.75rem;
    color: #efb1b6;
  }

  .detail-empty {
    min-height: 28rem;
    gap: 0.6rem;
  }

  .detail-empty span {
    color: #3bbeb8;
    font-size: 1.5rem;
  }

  @media (max-width: 1100px) {
    .inspector-grid {
      grid-template-columns: 1fr 1fr;
    }

    .detail-panel {
      grid-column: 1 / -1;
      border-top: 1px solid #1d2a38;
    }

    .detail-content,
    .signal-list {
      max-height: none;
    }
  }

  @media (max-width: 760px) {
    .inspector-heading,
    .stream-controls {
      align-items: flex-start;
      flex-direction: column;
    }

    .stream-controls {
      width: 100%;
    }

    .stream-controls button {
      width: 100%;
    }

    .filter-bar,
    .replay-console,
    .inspector-grid {
      grid-template-columns: 1fr;
    }

    .replay-fields {
      grid-template-columns: 1fr;
    }

    .multiplier-input {
      width: 100%;
    }

    .filter-result {
      padding: 0;
    }

    .signal-column {
      border-right: 0;
      border-bottom: 1px solid #1d2a38;
    }

    .detail-panel {
      grid-column: auto;
    }

    .signal-list {
      max-height: 18rem;
    }
  }
</style>
