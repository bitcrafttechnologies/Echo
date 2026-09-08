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

export type SignalRoutingResult = {
  status: string;
  entity_ids: string[];
  handler_count: number;
  task_ids: string[];
  task_statuses: Record<string, string>;
  error: Record<string, string> | null;
};

export type SignalHistoryEntry = {
  id: string;
  type: string;
  source: string;
  timestamp: string;
  payload: Record<string, unknown>;
  metadata: Record<string, unknown>;
  routing_result: SignalRoutingResult;
};

export type ActionHistoryEntry = {
  id: string;
  type: string;
  signal_id: string | null;
  task_id: string | null;
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

export async function fetchSignals(
  fetcher: typeof fetch,
  apiBase = '/api',
  filters: { type?: string; source?: string; limit?: number } = {}
): Promise<SignalHistoryEntry[]> {
  const parameters = new URLSearchParams();
  parameters.set('limit', String(filters.limit ?? 100));
  if (filters.type) parameters.set('signal_type', filters.type);
  if (filters.source) parameters.set('source', filters.source);
  const response = await fetcher(
    `${apiBase.replace(/\/$/, '')}/signals?${parameters.toString()}`,
    { headers: { accept: 'application/json' } }
  );
  if (!response.ok) {
    throw new Error(`Signal history request failed (${response.status})`);
  }
  return (await response.json()) as SignalHistoryEntry[];
}

export async function fetchSignal(
  fetcher: typeof fetch,
  signalId: string,
  apiBase = '/api'
): Promise<SignalHistoryEntry> {
  const response = await fetcher(
    `${apiBase.replace(/\/$/, '')}/signals/${encodeURIComponent(signalId)}`,
    { headers: { accept: 'application/json' } }
  );
  if (!response.ok) {
    throw new Error(`Signal inspection request failed (${response.status})`);
  }
  return (await response.json()) as SignalHistoryEntry;
}

export async function fetchSignalActions(
  fetcher: typeof fetch,
  signalId: string,
  apiBase = '/api'
): Promise<ActionHistoryEntry[]> {
  const parameters = new URLSearchParams({ signal_id: signalId, limit: '100' });
  const response = await fetcher(
    `${apiBase.replace(/\/$/, '')}/actions?${parameters.toString()}`,
    { headers: { accept: 'application/json' } }
  );
  if (!response.ok) {
    throw new Error(`Related Actions request failed (${response.status})`);
  }
  return (await response.json()) as ActionHistoryEntry[];
}

export function filterSignals(
  signals: SignalHistoryEntry[],
  filters: { type: string; source: string }
): SignalHistoryEntry[] {
  const type = filters.type.trim().toLocaleLowerCase();
  const source = filters.source.trim().toLocaleLowerCase();
  return signals.filter(
    (signal) =>
      (!type || signal.type.toLocaleLowerCase().includes(type)) &&
      (!source || signal.source.toLocaleLowerCase().includes(source))
  );
}

export function signalFromEvent(
  envelope: RuntimeEventEnvelope
): SignalHistoryEntry | null {
  if (
    envelope.event.event_type !== 'signal.received' ||
    !envelope.event.signal_id
  ) {
    return null;
  }
  return {
    id: envelope.event.signal_id,
    type: String(envelope.event.metadata.signal_type ?? 'unknown'),
    source: 'pending',
    timestamp: envelope.event.timestamp,
    payload: {},
    metadata: {},
    routing_result: {
      status: 'pending',
      entity_ids: [],
      handler_count: 0,
      task_ids: [],
      task_statuses: {},
      error: null
    }
  };
}

export function mergeSignals(
  signals: SignalHistoryEntry[],
  incoming: SignalHistoryEntry,
  limit = 100
): SignalHistoryEntry[] {
  return [incoming, ...signals.filter((signal) => signal.id !== incoming.id)].slice(
    0,
    limit
  );
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
