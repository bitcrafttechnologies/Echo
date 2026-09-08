export type RuntimeStatus = {
  runtime_id: string;
  status: 'idle' | 'active' | 'stopped' | string;
  uptime_seconds: number;
};

export type RuntimeEvent = {
  event_type: string;
  timestamp: string;
  entity_id: string | null;
  signal_id: string | null;
  task_id: string | null;
  action_id: string | null;
  metadata: Record<string, unknown>;
};

export type RuntimeEventEnvelope = {
  sequence: number;
  category: string;
  event: RuntimeEvent;
};

export async function fetchRuntimeStatus(
  fetcher: typeof fetch,
  apiBase = '/api'
): Promise<RuntimeStatus> {
  const response = await fetcher(`${apiBase.replace(/\/$/, '')}/runtime/status`, {
    headers: { accept: 'application/json' }
  });
  if (!response.ok) {
    throw new Error(`Runtime status request failed (${response.status})`);
  }
  return (await response.json()) as RuntimeStatus;
}

export function eventStreamUrl(
  configuredUrl: string | undefined,
  location: Pick<Location, 'protocol' | 'host'>
): string {
  if (configuredUrl) return configuredUrl;
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${location.host}/events`;
}

export function formatUptime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '—';
  const wholeSeconds = Math.floor(seconds);
  const hours = Math.floor(wholeSeconds / 3600);
  const minutes = Math.floor((wholeSeconds % 3600) / 60);
  const remainder = wholeSeconds % 60;
  return [hours, minutes, remainder]
    .map((value) => value.toString().padStart(2, '0'))
    .join(':');
}
