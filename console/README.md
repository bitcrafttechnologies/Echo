# Echo Console

The Echo Console is a lightweight SvelteKit development interface for the Echo
Runtime API. Phase 4C provides the application shell, runtime and connection
status, initial navigation, and a bounded live-event view. Phase 4D adds live
and historical Signal inspection with type/source filters, routing details,
related work IDs, and pause/resume behavior. Replay and the remaining
inspectors are intentionally deferred.

## Development

Install the console dependencies and run the development server:

```console
npm install
npm run dev
```

By default, Vite proxies `/api` and `/events` to `http://127.0.0.1:8000`. Set
`ECHO_API_PROXY_TARGET` to use another local Echo API. For direct browser
connections, copy `.env.example` to `.env` and set `PUBLIC_ECHO_API_URL` and
`PUBLIC_ECHO_WS_URL`.

The console remains usable when Echo is offline: it reports the unavailable
connection, retains navigation, and retries the API and event stream.

## Validation

```console
npm run check
npm test
npm run build
```
