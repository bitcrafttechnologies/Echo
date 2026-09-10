import { describe, expect, it, vi } from 'vitest';

import {
  eventStreamUrl,
  cancelTask,
  fetchConfiguration,
  fetchLogs,
  fetchProviders,
  fetchRestartOperation,
  fetchRuntimeStatus,
  fetchSignals,
  filterSignals,
  formatUptime,
  mergeSignals,
  reloadConfiguration,
  requestRuntimeRestart,
  setProviderMode,
  sendUserMessage,
  signalFromEvent,
  taskDepth,
  updateConfiguration,
  type TaskHistoryEntry,
  type SignalHistoryEntry
} from './echo-client';

const signal = (overrides: Partial<SignalHistoryEntry> = {}): SignalHistoryEntry => ({
  id: 'signal-1',
  type: 'battery.low',
  source: 'sensor',
  timestamp: '2026-09-08T12:00:00+00:00',
  payload: { percent: 8 },
  metadata: {},
  routing_result: {
    status: 'completed',
    entity_ids: ['bit'],
    handler_count: 1,
    task_ids: ['task-1'],
    task_statuses: { 'task-1': 'completed' },
    error: null
  },
  ...overrides
});

describe('Echo API client', () => {
  it('requests runtime status from the configured HTTP API', async () => {
    const fetcher = vi.fn(async () =>
      new Response(
        JSON.stringify({
          runtime_id: 'runtime-1',
          status: 'idle',
          uptime_seconds: 65
        }),
        { status: 200, headers: { 'content-type': 'application/json' } }
      )
    );

    await expect(fetchRuntimeStatus(fetcher, 'http://echo.test/')).resolves.toEqual({
      runtime_id: 'runtime-1',
      status: 'idle',
      uptime_seconds: 65
    });
    expect(fetcher).toHaveBeenCalledWith('http://echo.test/runtime/status', {
      headers: { accept: 'application/json' }
    });
  });

  it('reports unavailable runtime responses cleanly', async () => {
    const fetcher = vi.fn(async () => new Response(null, { status: 503 }));
    await expect(fetchRuntimeStatus(fetcher)).rejects.toThrow(
      'Runtime status request failed (503)'
    );
  });

  it('uses an explicitly confirmed state-preserving restart operation', async () => {
    const operation = {
      operation_id: 'restart-1', reason: 'code changed', status: 'quiescing',
      task_policy: 'wait', state_preservation_enabled: true,
      previous_runtime_id: 'runtime-1', new_runtime_id: null,
      requested_at: '2026-09-09T12:00:00Z', completed_at: null, error: null
    } as const;
    const fetcher = vi.fn(async () => new Response(JSON.stringify(operation), {
      status: 200, headers: { 'content-type': 'application/json' }
    }));

    await expect(requestRuntimeRestart(fetcher, 'code changed', '/api/')).resolves.toEqual(operation);
    await expect(fetchRestartOperation(fetcher, 'restart-1', '/api/')).resolves.toEqual(operation);
    expect(fetcher).toHaveBeenNthCalledWith(1, '/api/runtime/restart', {
      method: 'POST',
      headers: { accept: 'application/json', 'content-type': 'application/json' },
      body: JSON.stringify({
        reason: 'code changed', confirmation: 'RESTART', preserve_state: true
      })
    });
    expect(fetcher).toHaveBeenNthCalledWith(2, '/api/runtime/restart/restart-1', {
      headers: { accept: 'application/json' }
    });
  });

  it('inspects providers and switches only through the management API', async () => {
    const providerStatus = {
      mode: 'auto', preference: ['remote', 'lan', 'offline'], configured_providers: {}, health: {}, active_provider: null,
      model: null, last_latency_ms: null, recent_failures: [], recent_inferences: []
    } as const;
    const fetcher = vi.fn(async () =>
      new Response(JSON.stringify(providerStatus), {
        status: 200, headers: { 'content-type': 'application/json' }
      })
    );
    await expect(fetchProviders(fetcher, '/api/')).resolves.toEqual(providerStatus);
    await setProviderMode(fetcher, 'offline', '/api/');
    expect(fetcher).toHaveBeenNthCalledWith(1, '/api/providers', {
      headers: { accept: 'application/json' }
    });
    expect(fetcher).toHaveBeenNthCalledWith(2, '/api/providers/mode', {
      method: 'PATCH',
      headers: { accept: 'application/json', 'content-type': 'application/json' },
      body: JSON.stringify({ mode: 'offline' })
    });
  });

  it('reloads configuration only through the explicit management operation', async () => {
    const result = {
      status: 'restart_required', applied: false, source: 'echo.toml', changes: [],
      live_safe_changes: [], restart_required_changes: [], completed_at: '2026-09-08T12:00:00Z'
    } as const;
    const fetcher = vi.fn(async () =>
      new Response(JSON.stringify(result), {
        status: 200, headers: { 'content-type': 'application/json' }
      })
    );

    await expect(reloadConfiguration(fetcher, '/api/')).resolves.toEqual(result);
    expect(fetcher).toHaveBeenCalledWith('/api/configuration/reload', {
      method: 'POST',
      headers: { accept: 'application/json' }
    });
  });

  it('inspects and updates configuration without exposing secret values', async () => {
    const inspection = {
      source: 'echo.toml',
      fields: [
        { path: 'providers.openrouter.api_key', value: null, classification: 'restart_required', editable: false, secret: true, configured: true, reason: 'secret value is hidden' },
        { path: 'providers.mode', value: 'auto', classification: 'live_editable', editable: true, secret: false, configured: true, reason: 'routing mode is selected per inference request' }
      ]
    } as const;
    const fetcher = vi.fn(async (_url: Parameters<typeof fetch>[0], init?: RequestInit) =>
      new Response(
        JSON.stringify(init?.method === 'PATCH'
          ? { status: 'applied', applied: true, changes: [] }
          : inspection),
        { status: 200, headers: { 'content-type': 'application/json' } }
      )
    );

    await expect(fetchConfiguration(fetcher, '/api/')).resolves.toEqual(inspection);
    await updateConfiguration(fetcher, { 'providers.mode': 'lan' }, '/api/');
    expect(fetcher).toHaveBeenNthCalledWith(1, '/api/configuration', {
      headers: { accept: 'application/json' }
    });
    expect(fetcher).toHaveBeenNthCalledWith(2, '/api/configuration', {
      method: 'PATCH',
      headers: { accept: 'application/json', 'content-type': 'application/json' },
      body: JSON.stringify({ values: { 'providers.mode': 'lan' } })
    });
    expect(JSON.stringify(inspection)).not.toContain('browser-must-never-see');
  });

  it('formats configuration validation issues cleanly', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({
      error: {
        message: 'configuration update validation failed',
        details: { issues: [{ path: 'history.signals', message: 'must be at least 1' }] }
      }
    }), { status: 422, headers: { 'content-type': 'application/json' } }));

    await expect(updateConfiguration(fetcher, { 'history.signals': 0 })).rejects.toThrow(
      'configuration update validation failed\nhistory.signals: must be at least 1'
    );
  });

  it('derives secure and local event stream URLs', () => {
    expect(eventStreamUrl(undefined, { protocol: 'https:', host: 'console.test' })).toBe(
      'wss://console.test/events'
    );
    expect(
      eventStreamUrl('ws://127.0.0.1:8000/events', {
        protocol: 'http:',
        host: 'localhost:5173'
      })
    ).toBe('ws://127.0.0.1:8000/events');
  });

  it('formats runtime uptime without leaking invalid values', () => {
    expect(formatUptime(3661.8)).toBe('01:01:01');
    expect(formatUptime(Number.NaN)).toBe('—');
  });

  it('loads retained Signal history with API filters', async () => {
    const history = [signal()];
    const fetcher = vi.fn(async () =>
      new Response(JSON.stringify(history), {
        status: 200,
        headers: { 'content-type': 'application/json' }
      })
    );

    await expect(
      fetchSignals(fetcher, '/api', {
        type: 'battery.low',
        source: 'sensor',
        limit: 25
      })
    ).resolves.toEqual(history);
    expect(fetcher).toHaveBeenCalledWith(
      '/api/signals?limit=25&signal_type=battery.low&source=sensor',
      { headers: { accept: 'application/json' } }
    );
  });

  it('turns live Signal events into visible stream entries', () => {
    const live = signalFromEvent({
      sequence: 12,
      category: 'signal.received',
      event: {
        event_type: 'signal.received',
        timestamp: '2026-09-08T12:00:00+00:00',
        entity_id: null,
        signal_id: 'live-signal',
        task_id: null,
        action_id: null,
        metadata: { signal_type: 'person.detected' }
      }
    });

    expect(live).toMatchObject({
      id: 'live-signal',
      type: 'person.detected',
      source: 'pending'
    });
    expect(mergeSignals([signal()], live!)).toHaveLength(2);
  });

  it('filters Signals by type and source without changing history', () => {
    const history = [
      signal(),
      signal({ id: 'signal-2', type: 'person.detected', source: 'camera' }),
      signal({ id: 'signal-3', type: 'battery.ok', source: 'sensor' })
    ];

    expect(filterSignals(history, { type: 'battery', source: 'sensor' })).toEqual([
      history[0],
      history[2]
    ]);
    expect(filterSignals(history, { type: 'PERSON', source: '' })).toEqual([
      history[1]
    ]);
    expect(history).toHaveLength(3);
  });

  it('calculates Task hierarchy depth and stops safely at cycles', () => {
    const task = (id: string, parent: string | null): TaskHistoryEntry => ({
      id, parent, name: id, owner: 'bit', status: 'completed', priority: 0,
      created_at: '2026-09-08T12:00:00Z', started_at: null, completed_at: null,
      children: [], result: null, error: null, signal_id: null
    });
    const tasks = [task('root', null), task('child', 'root'), task('leaf', 'child')];
    expect(taskDepth(tasks[2], tasks)).toBe(2);
    const cycle = [task('a', 'b'), task('b', 'a')];
    expect(taskDepth(cycle[0], cycle)).toBe(1);
  });

  it('cancels Tasks through the runtime HTTP operation', async () => {
    const cancelled = { id: 'task-1', status: 'cancelled' };
    const fetcher = vi.fn(async () => new Response(JSON.stringify(cancelled), { status: 200 }));
    await expect(cancelTask(fetcher, 'task-1', '/api')).resolves.toMatchObject(cancelled);
    expect(fetcher).toHaveBeenCalledWith('/api/tasks/task-1/cancel', {
      method: 'POST', headers: { accept: 'application/json' }
    });
  });

  it('requests structured logs with severity and event type filters', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ events: [] }), { status: 200 }));
    await expect(fetchLogs(fetcher, '/api', { severity: 'error', eventType: 'error', limit: 25 })).resolves.toEqual([]);
    expect(fetcher).toHaveBeenCalledWith('/api/logs?limit=25&severity=error&event_type=error', {
      headers: { accept: 'application/json' }
    });
  });

  it('injects chat text only as a UserMessage Signal', async () => {
    const fetcher = vi.fn(async (_url: string, init?: RequestInit) =>
      new Response(JSON.stringify(signal({ type: 'UserMessage', payload: { text: 'Hello' } })), { status: 201 })
    );
    await sendUserMessage(fetcher as typeof fetch, 'Hello', '/api');
    expect(fetcher).toHaveBeenCalledWith('/api/signals', {
      method: 'POST',
      headers: { accept: 'application/json', 'content-type': 'application/json' },
      body: JSON.stringify({
        type: 'UserMessage', source: 'console', payload: { text: 'Hello' }, metadata: { channel: 'console' }
      })
    });
  });
});
