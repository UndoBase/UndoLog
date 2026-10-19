# UndoLog Dashboard

A web dashboard for inspecting UndoLog sessions, real-time tool execution
events, and human approval requests.

## Status: Experimental

The dashboard is experimental and currently in development. It is not yet
part of the stable documented UndoLog surface and is subject to active redesign.
For production deployments, interact with UndoLog via the Go proxy REST API,
the gRPC engine interface, or the SDK client libraries.

## Architecture

The dashboard is built with Next.js 15 App Router, React 19, and TypeScript.
It consumes real-time tool events from the Go proxy over Server-Sent Events
(`GET /events`) and submits human approval decisions (approve, reject, modify)
to the proxy REST API (`/approvals`).

## Environment Variables

The dashboard requires build-time environment variables to locate the proxy
and SSE endpoints:

| Variable | Default | Description |
|---|---|---|
| `NEXT_PUBLIC_PROXY_URL` | `http://localhost:8080` | Base URL of the UndoLog Go proxy REST API |
| `NEXT_PUBLIC_SSE_URL` | `http://localhost:8080` | Base URL for the Go proxy Server-Sent Events stream |

These variables must be provided at build time (as in `docker-compose.yml` and
`infra/Dockerfile.dashboard`).

## Development

Install dependencies:

```bash
npm install
```

Run the development server:

```bash
npm run dev
```

Build the production bundle:

```bash
npm run build
```

Typecheck without emitting artifacts:

```bash
npx tsc --noEmit
```
