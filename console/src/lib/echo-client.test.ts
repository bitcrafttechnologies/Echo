import { describe, expect, it, vi } from 'vitest';

import {
  eventStreamUrl,
  cancelTask,
  fetchLogs,
  fetchRuntimeStatus,
  fetchSignals,
  filterSignals,
  formatUptime,
  mergeSignals,
  sendUserMessage,
  signalFromEvent,
  taskDepth,
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
