# Deixic SDK

Use the Deixic SDK when an application needs to submit a task, follow its
durable progress, interrupt it, answer a tool request, or approve or deny a
requested action.

The supported clients expose eight operations from
`deixicpublic.v1.DeixicPublicService`. They do
not expose the browser API or create a second execution path. Deixic remains
the owner of task state, approvals, and action history.

| Job | TypeScript | Python |
| --- | --- | --- |
| Read a thread | `threads.get()` | `threads.get()` |
| Backfill events | `events.list()` | `events.list()` |
| Watch new events | `events.watch()` | `events.watch()` |
| Submit a message | `messages.send()` | `messages.send()` |
| Interrupt work | `controls.interrupt()` | `controls.interrupt()` |
| Answer a request | `controls.respond()` | `controls.respond()` |
| Read an approval receipt | `receipts.get()` | `receipts.get()` |
| Resolve an allowed receipt action | `receipts.resolve()` | `receipts.resolve()` |

Install the TypeScript client with `npm install @evalops/deixic-sdk` or the
Python client with `pip install deixic-sdk`. Both clients default to
`https://app.deixic.com` and accept an explicit base URL for another deployed
environment.

New to the SDK? Start with the getting started guide for
[TypeScript](sdk/getting-started-typescript.md),
[Python](sdk/getting-started-python.md), or
[Go](sdk/getting-started-go.md). Each one walks from installation to a
completed task and an approval decision.

## Safety contract

- Choose the organization and workspace when constructing the client. Method
  calls cannot replace them.
- Supply an idempotency key for every mutation. Reuse it only when resuming the
  same logical operation.
- Treat message submission as acceptance, not completion. Read or watch the
  accepted turn until Deixic records a terminal state.
- Save event cursors. On `resetRequired`, replace the local projection with the
  supplied snapshot before continuing.
- Keep API keys in trusted server-side code. A browser application should use
  its authenticated backend or an OAuth credential source.
- The clients do not retry unavailable or transport failures. They allow one
  authentication replay only when the refreshed credential preserves the same
  subject, tenant, and declared scopes.

The checked package contract is
[public-surface.json](https://github.com/dx-corp/api/blob/main/contracts/public-surface.json).
Package examples and error details live in the
[TypeScript SDK](https://github.com/dx-corp/deixic-node/blob/main/README.md) and
[Python SDK](https://github.com/dx-corp/deixic-python/blob/main/README.md).
