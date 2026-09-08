import { describe, expect, it, vi } from 'vitest';

import { eventStreamUrl, fetchRuntimeStatus, formatUptime } from './echo-client';

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
});
