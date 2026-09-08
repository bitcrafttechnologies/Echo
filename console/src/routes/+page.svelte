<script lang="ts">
  import { env } from '$env/dynamic/public';
  import { onMount } from 'svelte';

  import SignalInspector from '$lib/SignalInspector.svelte';
  import TaskInspector from '$lib/TaskInspector.svelte';
  import EntityInspector from '$lib/EntityInspector.svelte';
  import LogsView from '$lib/LogsView.svelte';
  import ChatView from '$lib/ChatView.svelte';

  import {
    eventStreamUrl,
    fetchSignals,
    fetchRuntimeStatus,
    formatUptime,
    mergeSignals,
    signalFromEvent,
    type RuntimeEventEnvelope,
    type RuntimeStatus,
    type SignalHistoryEntry
  } from '$lib/echo-client';

  const navigation = ['Overview', 'Signals', 'Tasks', 'Entity', 'Logs', 'Chat'] as const;
  type Section = (typeof navigation)[number];
  type ConnectionState = 'connecting' | 'connected' | 'disconnected';

  let activeSection: Section = 'Overview';
  let runtime: RuntimeStatus | null = null;
  let apiState: ConnectionState = 'connecting';
  let socketState: ConnectionState = 'connecting';
  let events: RuntimeEventEnvelope[] = [];
  let signalHistory: SignalHistoryEntry[] = [];
  let liveSignals: SignalHistoryEntry[] = [];
  let signalHistoryLoading = false;
  let signalHistoryError = '';
  let signalStreamPaused = false;
  let pausedSignalCount = 0;
  let eventRevision = 0;
  let lastError = '';
  let reconnect = () => {};
  let refreshSignals = () => {};

  const apiBase = env.PUBLIC_ECHO_API_URL || '/api';

  $: connectionLabel =
    apiState === 'connected' && socketState === 'connected'
      ? 'Live'
      : apiState === 'connected'
        ? 'API only'
        : apiState === 'connecting' || socketState === 'connecting'
          ? 'Connecting'
          : 'Offline';
  $: connectionTone = connectionLabel === 'Live' ? 'healthy' : connectionLabel === 'Offline' ? 'offline' : 'waiting';

  onMount(() => {
    let active = true;
    let socket: WebSocket | null = null;
    let retryTimer: number | undefined;
    let statusTimer: number | undefined;
    let signalRefreshTimer: number | undefined;
    let retryDelay = 1000;

    async function refreshStatus() {
      try {
        const next = await fetchRuntimeStatus(window.fetch.bind(window), apiBase);
        if (!active) return;
        runtime = next;
        apiState = 'connected';
        lastError = '';
      } catch {
        if (!active) return;
        runtime = null;
        apiState = 'disconnected';
        lastError = 'Echo Runtime is unavailable. The console will keep trying.';
      }
    }

    async function loadSignalHistory() {
      signalHistoryLoading = true;
      try {
        const next = await fetchSignals(window.fetch.bind(window), apiBase);
        if (!active) return;
        signalHistory = next;
        liveSignals = liveSignals.map(
          (liveSignal) =>
            next.find((retained) => retained.id === liveSignal.id) ?? liveSignal
        );
        signalHistoryError = '';
      } catch {
        if (!active) return;
        signalHistoryError = 'Recent Signal history is unavailable.';
      } finally {
        if (active) signalHistoryLoading = false;
      }
    }

    function scheduleReconnect() {
      if (!active || retryTimer !== undefined) return;
      retryTimer = window.setTimeout(() => {
        retryTimer = undefined;
        connectEvents();
      }, retryDelay);
      retryDelay = Math.min(retryDelay * 2, 10_000);
    }

    function connectEvents() {
      if (!active || socket?.readyState === WebSocket.OPEN) return;
      socketState = 'connecting';
      socket = new WebSocket(
        eventStreamUrl(env.PUBLIC_ECHO_WS_URL, window.location)
      );
      socket.onopen = () => {
        socketState = 'connected';
        retryDelay = 1000;
      };
      socket.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data) as RuntimeEventEnvelope;
          events = [event, ...events].slice(0, 30);
          eventRevision += 1;
          const liveSignal = signalFromEvent(event);
          if (liveSignal) {
            if (signalStreamPaused) {
              pausedSignalCount += 1;
            } else {
              liveSignals = mergeSignals(liveSignals, liveSignal, 50);
            }
            if (signalRefreshTimer === undefined) {
              signalRefreshTimer = window.setTimeout(() => {
                signalRefreshTimer = undefined;
                void loadSignalHistory();
              }, 100);
            }
          }
        } catch {
          lastError = 'A runtime event could not be read.';
        }
      };
      socket.onerror = () => {
        socketState = 'disconnected';
      };
      socket.onclose = () => {
        socketState = 'disconnected';
        scheduleReconnect();
      };
    }

    reconnect = () => {
      if (retryTimer !== undefined) {
        window.clearTimeout(retryTimer);
        retryTimer = undefined;
      }
      socket?.close();
      void refreshStatus();
      connectEvents();
    };
    refreshSignals = () => void loadSignalHistory();

    void refreshStatus();
    void loadSignalHistory();
    connectEvents();
    statusTimer = window.setInterval(refreshStatus, 10_000);

    return () => {
      active = false;
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
      if (statusTimer !== undefined) window.clearInterval(statusTimer);
      if (signalRefreshTimer !== undefined) window.clearTimeout(signalRefreshTimer);
      socket?.close(1000, 'Console closed');
    };
  });

  function selectSection(section: Section) {
    activeSection = section;
    if (section === 'Signals') refreshSignals();
  }

  function toggleSignalStream() {
    signalStreamPaused = !signalStreamPaused;
    if (!signalStreamPaused) pausedSignalCount = 0;
  }

  function eventTime(timestamp: string): string {
    const value = new Date(timestamp);
    return Number.isNaN(value.getTime())
      ? '—'
      : value.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }
</script>

<svelte:head>
  <title>Echo Console</title>
</svelte:head>

<div class="console-shell">
  <header class="topbar">
    <div class="brand-lockup">
      <span class="brand-mark" aria-hidden="true">E</span>
      <div>
        <p class="eyebrow">Development interface</p>
        <h1>Echo Console</h1>
      </div>
    </div>

    <div class="runtime-header">
      <div class="runtime-summary">
        <span class="label">Runtime</span>
        <strong>{runtime?.status ?? 'unavailable'}</strong>
        <span class="runtime-id">{runtime?.runtime_id ?? 'No runtime detected'}</span>
      </div>
      <button class="connection-pill {connectionTone}" onclick={reconnect} type="button">
        <span class="status-dot" aria-hidden="true"></span>
        {connectionLabel}
      </button>
    </div>
  </header>

  <aside class="sidebar" aria-label="Console navigation">
    <nav>
      {#each navigation as item, index}
        <button
          class:active={activeSection === item}
          aria-current={activeSection === item ? 'page' : undefined}
          onclick={() => selectSection(item)}
          type="button"
        >
          <span class="nav-index">{String(index + 1).padStart(2, '0')}</span>
          <span>{item}</span>
        </button>
      {/each}
    </nav>

    <div class="sidebar-footer">
      <span>API</span><strong class:online={apiState === 'connected'}>{apiState}</strong>
      <span>Events</span><strong class:online={socketState === 'connected'}>{socketState}</strong>
    </div>
  </aside>

  <main>
    {#if activeSection === 'Overview'}
      <section class="overview" aria-labelledby="overview-heading">
        <div class="section-heading">
          <div>
            <p class="eyebrow">Runtime overview</p>
            <h2 id="overview-heading">System pulse</h2>
          </div>
          <span class="updated">Refreshes every 10 seconds</span>
        </div>

        {#if lastError}
          <div class="offline-notice" role="status">
            <span>{lastError}</span>
            <button type="button" onclick={reconnect}>Try now</button>
          </div>
        {/if}

        <div class="metric-grid">
          <article>
            <span>Runtime state</span>
            <strong>{runtime?.status ?? 'Offline'}</strong>
          </article>
          <article>
            <span>Uptime</span>
            <strong>{runtime ? formatUptime(runtime.uptime_seconds) : '—'}</strong>
          </article>
          <article>
            <span>Event stream</span>
            <strong>{socketState === 'connected' ? 'Receiving' : 'Waiting'}</strong>
          </article>
        </div>

        <section class="event-panel" aria-labelledby="events-heading">
          <div class="panel-heading">
            <div>
              <p class="eyebrow">WebSocket</p>
              <h3 id="events-heading">Live activity</h3>
            </div>
            <span>{events.length} recent</span>
          </div>

          {#if events.length > 0}
            <ol class="event-list" aria-live="polite">
              {#each events as envelope (envelope.sequence)}
                <li>
                  <time datetime={envelope.event.timestamp}>{eventTime(envelope.event.timestamp)}</time>
                  <span class="event-category">{envelope.category}</span>
                  <strong>{envelope.event.event_type}</strong>
                  <span class="event-sequence">#{envelope.sequence}</span>
                </li>
              {/each}
            </ol>
          {:else}
            <div class="empty-state">
              <span class="pulse-ring" aria-hidden="true"></span>
              <p>{socketState === 'connected' ? 'Listening for runtime activity' : 'Events will appear when Echo reconnects'}</p>
            </div>
          {/if}
        </section>
      </section>
    {:else if activeSection === 'Signals'}
      <SignalInspector
        {apiBase}
        history={signalHistory}
        {liveSignals}
        historyLoading={signalHistoryLoading}
        historyError={signalHistoryError}
        paused={signalStreamPaused}
        pausedCount={pausedSignalCount}
        onTogglePause={toggleSignalStream}
        onRefresh={refreshSignals}
      />
    {:else if activeSection === 'Tasks'}
      <TaskInspector {apiBase} {eventRevision} />
    {:else if activeSection === 'Entity'}
      <EntityInspector {apiBase} {eventRevision} />
    {:else if activeSection === 'Logs'}
      <LogsView {apiBase} {eventRevision} />
    {:else if activeSection === 'Chat'}
      <ChatView {apiBase} {eventRevision} connected={apiState === 'connected'} />
    {/if}
  </main>
</div>

<style>
  :global(*) {
    box-sizing: border-box;
  }

  :global(:root) {
    font-family:
      Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    color: #e7edf5;
    background: #080c12;
    font-synthesis: none;
  }

  :global(body) {
    margin: 0;
    min-width: 320px;
    min-height: 100vh;
    background:
      radial-gradient(circle at 80% 0%, rgba(27, 119, 122, 0.12), transparent 30rem),
      #080c12;
  }

  :global(button) {
    font: inherit;
  }

  .console-shell {
    display: grid;
    grid-template: 5.25rem 1fr / 15rem 1fr;
    min-height: 100vh;
  }

  .topbar {
    grid-column: 1 / -1;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 2rem;
    padding: 0 2rem;
    border-bottom: 1px solid #1d2734;
    background: rgba(8, 12, 18, 0.9);
    backdrop-filter: blur(16px);
  }

  .brand-lockup,
  .runtime-header,
  .runtime-summary {
    display: flex;
    align-items: center;
  }

  .brand-lockup {
    gap: 0.85rem;
  }

  .brand-mark {
    display: grid;
    width: 2.55rem;
    height: 2.55rem;
    place-items: center;
    border: 1px solid #2cc8c0;
    border-radius: 0.45rem;
    color: #66e4da;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-weight: 700;
    box-shadow: inset 0 0 1.25rem rgba(44, 200, 192, 0.12);
  }

  h1,
  h2,
  h3,
  p {
    margin: 0;
  }

  h1 {
    font-size: 1.05rem;
    letter-spacing: 0.01em;
  }

  h2 {
    font-size: clamp(1.75rem, 3vw, 2.5rem);
    line-height: 1.1;
  }

  h3 {
    font-size: 1.15rem;
  }

  .eyebrow,
  .label,
  .updated,
  .panel-heading > span {
    color: #7f8b9a;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.75rem;
    letter-spacing: 0.09em;
    text-transform: uppercase;
  }

  .runtime-header {
    gap: 1.25rem;
  }

  .runtime-summary {
    gap: 0.65rem;
  }

  .runtime-summary strong {
    color: #c8d3df;
    font-size: 0.88rem;
    text-transform: capitalize;
  }

  .runtime-id {
    max-width: 14rem;
    overflow: hidden;
    color: #647181;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.72rem;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .connection-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.55rem;
    min-width: 6.4rem;
    justify-content: center;
    padding: 0.55rem 0.8rem;
    border: 1px solid #2a3543;
    border-radius: 999px;
    color: #adb9c7;
    background: #101720;
    cursor: pointer;
  }

  .connection-pill:hover,
  .connection-pill:focus-visible {
    border-color: #526276;
    outline: none;
  }

  .status-dot {
    width: 0.5rem;
    height: 0.5rem;
    border-radius: 50%;
    background: #f4b860;
    box-shadow: 0 0 0.75rem rgba(244, 184, 96, 0.45);
  }

  .connection-pill.healthy .status-dot {
    background: #58d68d;
    box-shadow: 0 0 0.75rem rgba(88, 214, 141, 0.55);
  }

  .connection-pill.offline .status-dot {
    background: #ef6a73;
    box-shadow: 0 0 0.75rem rgba(239, 106, 115, 0.45);
  }

  .sidebar {
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    padding: 1.5rem 1rem 1.25rem;
    border-right: 1px solid #1d2734;
    background: rgba(11, 16, 24, 0.72);
  }

  nav {
    display: grid;
    gap: 0.3rem;
  }

  nav button {
    display: grid;
    grid-template-columns: 2rem 1fr;
    align-items: center;
    width: 100%;
    padding: 0.8rem 0.75rem;
    border: 1px solid transparent;
    border-radius: 0.4rem;
    color: #8f9cac;
    text-align: left;
    background: transparent;
    cursor: pointer;
  }

  nav button:hover,
  nav button:focus-visible {
    color: #e7edf5;
    background: #111a25;
    outline: none;
  }

  nav button.active {
    border-color: #213945;
    color: #e7edf5;
    background: linear-gradient(90deg, rgba(36, 159, 158, 0.14), rgba(17, 26, 37, 0.75));
  }

  .nav-index {
    color: #506071;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.7rem;
  }

  nav button.active .nav-index {
    color: #4bd5cd;
  }

  .sidebar-footer {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 0.5rem 1rem;
    padding: 0.85rem 0.75rem 0;
    border-top: 1px solid #1d2734;
    color: #637182;
    font-size: 0.75rem;
  }

  .sidebar-footer strong {
    color: #ef6a73;
    font-weight: 500;
    text-transform: capitalize;
  }

  .sidebar-footer strong.online {
    color: #58d68d;
  }

  main {
    min-width: 0;
    padding: clamp(1.5rem, 4vw, 3.5rem);
  }

  .overview {
    width: min(100%, 74rem);
    margin: 0 auto;
  }

  .section-heading,
  .panel-heading {
    display: flex;
    align-items: end;
    justify-content: space-between;
    gap: 1rem;
  }

  .section-heading {
    margin-bottom: 2rem;
  }

  .section-heading .eyebrow,
  .panel-heading .eyebrow {
    margin-bottom: 0.45rem;
  }

  .offline-notice {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 1rem;
    padding: 0.85rem 1rem;
    border: 1px solid #563038;
    border-radius: 0.4rem;
    color: #efb1b6;
    background: rgba(113, 36, 47, 0.16);
  }

  .offline-notice button {
    padding: 0.55rem 0.8rem;
    border: 1px solid #3a4b5e;
    border-radius: 0.35rem;
    color: #d9e3ed;
    background: #15202c;
    cursor: pointer;
  }

  .metric-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 1px;
    margin-bottom: 1.25rem;
    overflow: hidden;
    border: 1px solid #1d2a38;
    border-radius: 0.55rem;
    background: #1d2a38;
  }

  .metric-grid article {
    display: grid;
    gap: 0.9rem;
    min-height: 8.5rem;
    padding: 1.25rem;
    background: #0e151e;
  }

  .metric-grid span {
    color: #7e8c9d;
    font-size: 0.82rem;
  }

  .metric-grid strong {
    align-self: end;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 1.45rem;
    font-weight: 500;
    text-transform: capitalize;
  }

  .event-panel {
    min-height: 22rem;
    padding: 1.25rem;
    border: 1px solid #1d2a38;
    border-radius: 0.55rem;
    background: rgba(14, 21, 30, 0.8);
  }

  .panel-heading {
    padding-bottom: 1rem;
    border-bottom: 1px solid #1d2a38;
  }

  .event-list {
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .event-list li {
    display: grid;
    grid-template-columns: 6.5rem 9.5rem 1fr auto;
    gap: 1rem;
    align-items: center;
    padding: 0.85rem 0.2rem;
    border-bottom: 1px solid #17212d;
    font-size: 0.84rem;
  }

  .event-list time,
  .event-sequence {
    color: #617083;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }

  .event-category {
    color: #55d3cc;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }

  .event-list strong {
    overflow: hidden;
    font-weight: 500;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .empty-state {
    display: grid;
    min-height: 16rem;
    place-items: center;
    align-content: center;
    gap: 1rem;
    color: #687789;
    text-align: center;
  }

  .pulse-ring {
    width: 2.5rem;
    height: 2.5rem;
    border: 1px solid #2e615f;
    border-radius: 50%;
    box-shadow: 0 0 0 0.6rem rgba(44, 200, 192, 0.04);
  }

  @media (max-width: 760px) {
    .console-shell {
      grid-template: auto auto 1fr / 1fr;
    }

    .topbar {
      flex-wrap: wrap;
      padding: 1rem;
    }

    .runtime-id,
    .runtime-summary .label {
      display: none;
    }

    .sidebar {
      padding: 0.65rem 1rem;
      overflow-x: auto;
      border-right: 0;
      border-bottom: 1px solid #1d2734;
    }

    nav {
      display: flex;
      min-width: max-content;
    }

    nav button {
      display: flex;
      width: auto;
      gap: 0.45rem;
    }

    .sidebar-footer {
      display: none;
    }

    main {
      padding: 1.5rem 1rem;
    }

    .metric-grid {
      grid-template-columns: 1fr;
    }

    .metric-grid article {
      min-height: 6.5rem;
    }

    .event-list li {
      grid-template-columns: 5.8rem 1fr auto;
      gap: 0.6rem;
    }

    .event-category {
      display: none;
    }

    .updated {
      display: none;
    }
  }
</style>
