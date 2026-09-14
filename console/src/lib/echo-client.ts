export type RuntimeStatus = {
  runtime_id: string;
  status: 'idle' | 'active' | 'stopped' | string;
  uptime_seconds: number;
  accepting_work: boolean;
  restart: {
    reason: string;
    status: string;
    previous_runtime_id: string;
    runtime_id: string;
  } | null;
};

export type RestartOperation = {
  operation_id: string;
  reason: string;
  status: string;
  task_policy: 'wait' | 'cancel';
  state_preservation_enabled: boolean;
  previous_runtime_id: string;
  new_runtime_id: string | null;
  requested_at: string;
  completed_at: string | null;
  error: string | null;
};

export type ProviderMetadata = {
  provider_id: string;
  name: string;
  model: string | null;
  capabilities: string[];
  version: string | null;
  attributes: Record<string, unknown>;
};

export type ProviderInspection = {
  mode: 'auto' | 'remote' | 'lan' | 'offline';
  preference: Array<'remote' | 'lan' | 'offline'>;
  configured_providers: Record<string, ProviderMetadata>;
  health: Record<string, { status: string; message: string | null; provider: ProviderMetadata }>;
  active_provider: ProviderMetadata | null;
  model: string | null;
  last_latency_ms: number | null;
  recent_failures: Array<{ slot: string; request_id: string; error_reason: string; completed_at: string }>;
  recent_inferences: Array<{ request_id: string; mode: string; served_by: ProviderMetadata | null; latency_ms: number; completed_at: string }>;
};

export type ConfigurationChange = {
  path: string;
  classification: 'live_safe' | 'restart_required';
  previous: unknown;
  requested: unknown;
  reason: string;
};

export type ConfigurationReloadResult = {
  status: 'applied' | 'no_change' | 'restart_required';
  applied: boolean;
  source: string | null;
  changes: ConfigurationChange[];
  live_safe_changes: ConfigurationChange[];
  restart_required_changes: ConfigurationChange[];
  completed_at: string;
};

export type ConfigurationField = {
  path: string;
  value: unknown;
  classification: 'live_editable' | 'restart_required';
  editable: boolean;
  secret: boolean;
  configured: boolean;
  reason: string;
};

export type ConfigurationInspection = {
  source: string | null;
  fields: ConfigurationField[];
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

export type ReplayTiming = 'realtime' | 'accelerated' | 'immediate' | 'manual_step';

export type ReplayStatus = {
  replay_id: string;
  runtime_id: string;
  recording_path: string;
  recording_session_id: string;
  mode: 'signal' | 'sequential';
  timing: ReplayTiming;
  multiplier: number | null;
  safety_policy: 'record_only';
  state: 'ready' | 'running' | 'completed' | 'cancelled' | 'failed';
  total_signals: number;
  next_index: number;
  remaining_signals: number;
  started_at: string | null;
  completed_at: string | null;
  cancellation_requested: boolean;
  replayed_signals: Array<{
    original_signal_id: string;
    replayed_signal_id: string;
    original_timestamp: string;
    received_at: string;
  }>;
  error: { type: string; message: string } | null;
};

export type StartReplayInput = {
  path: string;
  mode: 'signal' | 'sequential';
  signalId?: string;
  timing: ReplayTiming;
  multiplier?: number;
};

export type ActionHistoryEntry = {
  id: string;
  type: string;
  created_at: string;
  execution_time: string | null;
  status: string;
  signal_id: string | null;
  task_id: string | null;
  entity_id: string | null;
  parameters: Record<string, unknown>;
  result: unknown;
  error: string | null;
};

export type TaskHistoryEntry = {
  id: string;
  name: string;
  owner: string;
  status: string;
  priority: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  parent: string | null;
  children: string[];
  result: unknown;
  error: string | null;
  signal_id: string | null;
};

export type RelationshipState = {
  subject_id: string;
  familiarity: number;
  trust: number;
  interaction_count: number;
  communication_preferences: Record<string, unknown>;
  known_interests: string[];
  boundaries: string[];
  important_memory_ids: string[];
  current_context: Record<string, unknown>;
};

export type EntityInspection = {
  id: string;
  state: Record<string, unknown>;
  character: Record<string, unknown> & {
    identity?: Record<string, unknown>;
    traits?: Record<string, unknown>;
    self_model?: Record<string, unknown>;
    internal_state?: Record<string, unknown>;
    drives?: Record<string, unknown>;
    attention_candidates?: unknown[];
    relationships?: Record<string, RelationshipState>;
  };
  active_task_ids: string[];
  handlers: {
    count: number;
    registrations: Array<{
      signal_kind: string;
      signal: string;
      handler: string;
    }>;
  };
};

export type RuntimeLogEntry = RuntimeEvent & {
  severity: 'info' | 'warning' | 'error' | string;
};

export type MedullaNodeInspection = {
  node_id: string;
  first_seen_at: string | null;
  last_seen_at: string | null;
  active_at: string | null;
  disconnected_at: string | null;
  reachability: string;
  negotiation_state: string | null;
  approval_mode: 'manual' | 'autonomous';
  decision_reason: string | null;
  granted_scopes: Record<string, unknown> | null;
  advertised_manifest: {
    node?: { id?: string; display_name?: string; type?: string };
    capabilities?: Array<{ name: string; description?: string }>;
    signals?: Array<{ name: string }>;
    resources?: Array<{ id: string; description?: string }>;
  } | null;
  approval_request: { provides: string[]; requires: string[] } | null;
  events: Array<{ type: string; timestamp: string; details: Record<string, unknown> }>;
};

async function responseError(response: Response, fallback: string): Promise<Error> {
  try {
    const body = (await response.json()) as {
      error?: {
        message?: string;
        details?: { issues?: Array<{ path: string; message: string }> };
      };
    };
    const issues = body.error?.details?.issues ?? [];
    const issueText = issues.map((issue) => `${issue.path}: ${issue.message}`).join('\n');
    const message = body.error?.message || `${fallback} (${response.status})`;
    return new Error(issueText ? `${message}\n${issueText}` : message);
  } catch {
    return new Error(`${fallback} (${response.status})`);
  }
}

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

export async function requestRuntimeRestart(
  fetcher: typeof fetch,
  reason: string,
  apiBase = '/api'
): Promise<RestartOperation> {
  const response = await fetcher(`${apiBase.replace(/\/$/, '')}/runtime/restart`, {
    method: 'POST',
    headers: { accept: 'application/json', 'content-type': 'application/json' },
    body: JSON.stringify({
      reason,
      confirmation: 'RESTART',
      preserve_state: true
    })
  });
  if (!response.ok) throw await responseError(response, 'Runtime restart request failed');
  return (await response.json()) as RestartOperation;
}

export async function fetchRestartOperation(
  fetcher: typeof fetch,
  operationId: string,
  apiBase = '/api'
): Promise<RestartOperation> {
  const response = await fetcher(
    `${apiBase.replace(/\/$/, '')}/runtime/restart/${encodeURIComponent(operationId)}`,
    { headers: { accept: 'application/json' } }
  );
  if (!response.ok) throw await responseError(response, 'Restart progress request failed');
  return (await response.json()) as RestartOperation;
}

export async function fetchProviders(
  fetcher: typeof fetch,
  apiBase = '/api'
): Promise<ProviderInspection> {
  const response = await fetcher(`${apiBase.replace(/\/$/, '')}/providers`, {
    headers: { accept: 'application/json' }
  });
  if (!response.ok) throw await responseError(response, 'Provider inspection failed');
  return (await response.json()) as ProviderInspection;
}

export async function setProviderMode(
  fetcher: typeof fetch,
  mode: ProviderInspection['mode'],
  apiBase = '/api'
): Promise<ProviderInspection> {
  const response = await fetcher(`${apiBase.replace(/\/$/, '')}/providers/mode`, {
    method: 'PATCH',
    headers: { accept: 'application/json', 'content-type': 'application/json' },
    body: JSON.stringify({ mode })
  });
  if (!response.ok) throw await responseError(response, 'Provider mode update failed');
  return (await response.json()) as ProviderInspection;
}

export async function fetchMedullaNodes(
  fetcher: typeof fetch,
  apiBase = '/api'
): Promise<MedullaNodeInspection[]> {
  const response = await fetcher(`${apiBase.replace(/\/$/, '')}/medulla/nodes`, {
    headers: { accept: 'application/json' }
  });
  if (!response.ok) throw await responseError(response, 'Medulla Node inspection failed');
  return (await response.json()) as MedullaNodeInspection[];
}

export async function decideMedullaNode(
  fetcher: typeof fetch,
  nodeId: string,
  decision: 'approve' | 'decline' | 'block' | 'reconsider' | 'unblock',
  reason: string | null,
  apiBase = '/api'
): Promise<MedullaNodeInspection> {
  const response = await fetcher(
    `${apiBase.replace(/\/$/, '')}/medulla/nodes/${encodeURIComponent(nodeId)}/decision`,
    {
      method: 'POST',
      headers: { accept: 'application/json', 'content-type': 'application/json' },
      body: JSON.stringify({ decision, ...(reason ? { reason } : {}) })
    }
  );
  if (!response.ok) throw await responseError(response, 'Medulla Node decision failed');
  return (await response.json()) as MedullaNodeInspection;
}

export async function authorizeMedullaNode(
  fetcher: typeof fetch,
  nodeId: string,
  authorization: Record<string, unknown>,
  apiBase = '/api'
): Promise<MedullaNodeInspection> {
  const response = await fetcher(
    `${apiBase.replace(/\/$/, '')}/medulla/nodes/${encodeURIComponent(nodeId)}/authorization`,
    {
      method: 'POST',
      headers: { accept: 'application/json', 'content-type': 'application/json' },
      body: JSON.stringify({ authorization })
    }
  );
  if (!response.ok) throw await responseError(response, 'Medulla authorization failed');
  return (await response.json()) as MedullaNodeInspection;
}

export async function reloadConfiguration(
  fetcher: typeof fetch,
  apiBase = '/api'
): Promise<ConfigurationReloadResult> {
  const response = await fetcher(
    `${apiBase.replace(/\/$/, '')}/configuration/reload`,
    {
      method: 'POST',
      headers: { accept: 'application/json' }
    }
  );
  if (!response.ok) throw await responseError(response, 'Configuration reload failed');
  return (await response.json()) as ConfigurationReloadResult;
}

export async function fetchConfiguration(
  fetcher: typeof fetch,
  apiBase = '/api'
): Promise<ConfigurationInspection> {
  const response = await fetcher(`${apiBase.replace(/\/$/, '')}/configuration`, {
    headers: { accept: 'application/json' }
  });
  if (!response.ok) throw await responseError(response, 'Configuration inspection failed');
  return (await response.json()) as ConfigurationInspection;
}

export async function updateConfiguration(
  fetcher: typeof fetch,
  values: Record<string, unknown>,
  apiBase = '/api'
): Promise<ConfigurationReloadResult> {
  const response = await fetcher(`${apiBase.replace(/\/$/, '')}/configuration`, {
    method: 'PATCH',
    headers: { accept: 'application/json', 'content-type': 'application/json' },
    body: JSON.stringify({ values })
  });
  if (!response.ok) throw await responseError(response, 'Configuration update failed');
  return (await response.json()) as ConfigurationReloadResult;
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

export async function startReplay(
  fetcher: typeof fetch,
  input: StartReplayInput,
  apiBase = '/api'
): Promise<ReplayStatus> {
  const response = await fetcher(`${apiBase.replace(/\/$/, '')}/runtime/replays`, {
    method: 'POST',
    headers: { accept: 'application/json', 'content-type': 'application/json' },
    body: JSON.stringify({
      path: input.path,
      mode: input.mode,
      signal_id: input.signalId,
      timing: input.timing,
      multiplier: input.timing === 'accelerated' ? input.multiplier : undefined,
      safety_policy: 'record_only'
    })
  });
  if (!response.ok) throw await responseError(response, 'Replay could not be started');
  return (await response.json()) as ReplayStatus;
}

export async function fetchReplayStatus(
  fetcher: typeof fetch,
  replayId: string,
  apiBase = '/api'
): Promise<ReplayStatus> {
  const response = await fetcher(
    `${apiBase.replace(/\/$/, '')}/runtime/replays/${encodeURIComponent(replayId)}`,
    { headers: { accept: 'application/json' } }
  );
  if (!response.ok) throw await responseError(response, 'Replay status could not be loaded');
  return (await response.json()) as ReplayStatus;
}

export async function advanceReplay(
  fetcher: typeof fetch,
  replayId: string,
  apiBase = '/api'
): Promise<ReplayStatus> {
  const response = await fetcher(
    `${apiBase.replace(/\/$/, '')}/runtime/replays/${encodeURIComponent(replayId)}/step`,
    { method: 'POST', headers: { accept: 'application/json' } }
  );
  if (!response.ok) throw await responseError(response, 'Replay step failed');
  return (await response.json()) as ReplayStatus;
}

export async function cancelReplay(
  fetcher: typeof fetch,
  replayId: string,
  apiBase = '/api'
): Promise<ReplayStatus> {
  const response = await fetcher(
    `${apiBase.replace(/\/$/, '')}/runtime/replays/${encodeURIComponent(replayId)}`,
    { method: 'DELETE', headers: { accept: 'application/json' } }
  );
  if (!response.ok) throw await responseError(response, 'Replay cancellation failed');
  return (await response.json()) as ReplayStatus;
}

export function replayMetadata(
  signal: SignalHistoryEntry
): Record<string, unknown> | null {
  const value = signal.metadata.echo_replay;
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const marker = value as Record<string, unknown>;
  return marker.replayed === true ? marker : null;
}

export function recordedSignalId(signal: SignalHistoryEntry): string {
  const original = replayMetadata(signal)?.original_signal_id;
  return typeof original === 'string' && original ? original : signal.id;
}

export async function fetchActions(
  fetcher: typeof fetch,
  apiBase = '/api',
  filters: { type?: string; status?: string; taskId?: string; signalId?: string; limit?: number } = {}
): Promise<ActionHistoryEntry[]> {
  const parameters = new URLSearchParams({ limit: String(filters.limit ?? 100) });
  if (filters.type) parameters.set('type', filters.type);
  if (filters.status) parameters.set('status', filters.status);
  if (filters.taskId) parameters.set('task_id', filters.taskId);
  if (filters.signalId) parameters.set('signal_id', filters.signalId);
  const response = await fetcher(
    `${apiBase.replace(/\/$/, '')}/actions?${parameters.toString()}`,
    { headers: { accept: 'application/json' } }
  );
  if (!response.ok) throw await responseError(response, 'Action history request failed');
  return (await response.json()) as ActionHistoryEntry[];
}

export async function fetchTasks(
  fetcher: typeof fetch,
  apiBase = '/api',
  filters: { status?: string; limit?: number } = {}
): Promise<TaskHistoryEntry[]> {
  const parameters = new URLSearchParams({ limit: String(filters.limit ?? 100) });
  if (filters.status) parameters.set('status', filters.status);
  const response = await fetcher(
    `${apiBase.replace(/\/$/, '')}/tasks?${parameters.toString()}`,
    { headers: { accept: 'application/json' } }
  );
  if (!response.ok) throw await responseError(response, 'Task history request failed');
  return (await response.json()) as TaskHistoryEntry[];
}

export async function fetchTask(
  fetcher: typeof fetch,
  taskId: string,
  apiBase = '/api'
): Promise<TaskHistoryEntry> {
  const response = await fetcher(
    `${apiBase.replace(/\/$/, '')}/tasks/${encodeURIComponent(taskId)}`,
    { headers: { accept: 'application/json' } }
  );
  if (!response.ok) throw await responseError(response, 'Task inspection request failed');
  return (await response.json()) as TaskHistoryEntry;
}

export async function cancelTask(
  fetcher: typeof fetch,
  taskId: string,
  apiBase = '/api'
): Promise<TaskHistoryEntry> {
  const response = await fetcher(
    `${apiBase.replace(/\/$/, '')}/tasks/${encodeURIComponent(taskId)}/cancel`,
    { method: 'POST', headers: { accept: 'application/json' } }
  );
  if (!response.ok) throw await responseError(response, 'Task cancellation failed');
  return (await response.json()) as TaskHistoryEntry;
}

export const ACTIVE_TASK_STATUSES = new Set(['pending', 'running', 'paused', 'blocked']);

export function taskDepth(task: TaskHistoryEntry, tasks: TaskHistoryEntry[]): number {
  const byId = new Map(tasks.map((value) => [value.id, value]));
  const visited = new Set([task.id]);
  let parentId = task.parent;
  let depth = 0;
  while (parentId && !visited.has(parentId)) {
    visited.add(parentId);
    depth += 1;
    parentId = byId.get(parentId)?.parent ?? null;
  }
  return depth;
}

export async function fetchEntities(
  fetcher: typeof fetch,
  apiBase = '/api'
): Promise<EntityInspection[]> {
  const response = await fetcher(`${apiBase.replace(/\/$/, '')}/entities`, {
    headers: { accept: 'application/json' }
  });
  if (!response.ok) throw await responseError(response, 'Entity list request failed');
  return (await response.json()) as EntityInspection[];
}

export async function fetchEntity(
  fetcher: typeof fetch,
  entityId: string,
  apiBase = '/api'
): Promise<EntityInspection> {
  const response = await fetcher(
    `${apiBase.replace(/\/$/, '')}/entities/${encodeURIComponent(entityId)}`,
    { headers: { accept: 'application/json' } }
  );
  if (!response.ok) throw await responseError(response, 'Entity inspection request failed');
  return (await response.json()) as EntityInspection;
}

export async function fetchRelationships(
  fetcher: typeof fetch,
  entityId: string,
  apiBase = '/api'
): Promise<RelationshipState[]> {
  const response = await fetcher(
    `${apiBase.replace(/\/$/, '')}/entities/${encodeURIComponent(entityId)}/relationships`,
    { headers: { accept: 'application/json' } }
  );
  if (!response.ok) throw await responseError(response, 'Relationship request failed');
  return (await response.json()) as RelationshipState[];
}

export async function fetchLogs(
  fetcher: typeof fetch,
  apiBase = '/api',
  filters: { severity?: string; eventType?: string; limit?: number } = {}
): Promise<RuntimeLogEntry[]> {
  const parameters = new URLSearchParams({ limit: String(filters.limit ?? 200) });
  if (filters.severity) parameters.set('severity', filters.severity);
  if (filters.eventType) parameters.set('event_type', filters.eventType);
  const response = await fetcher(
    `${apiBase.replace(/\/$/, '')}/logs?${parameters.toString()}`,
    { headers: { accept: 'application/json' } }
  );
  if (!response.ok) throw await responseError(response, 'Runtime log request failed');
  const body = (await response.json()) as { events: RuntimeLogEntry[] };
  return body.events;
}

export async function sendUserMessage(
  fetcher: typeof fetch,
  text: string,
  apiBase = '/api'
): Promise<SignalHistoryEntry> {
  const response = await fetcher(`${apiBase.replace(/\/$/, '')}/signals`, {
    method: 'POST',
    headers: { accept: 'application/json', 'content-type': 'application/json' },
    body: JSON.stringify({
      type: 'UserMessage',
      source: 'console',
      payload: { text },
      metadata: { channel: 'console' }
    })
  });
  if (!response.ok) throw await responseError(response, 'Message delivery failed');
  return (await response.json()) as SignalHistoryEntry;
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
