# Deixic SDK getting started guides

These guides take you from an empty project to a first completed Deixic task.
Each one covers installation, credentials, a setup check, submitting a task,
reading the result, and answering an approval request.

| Language | Package | Guide |
| --- | --- | --- |
| TypeScript (Node.js 20+) | `@evalops/deixic-sdk` | [getting-started-typescript.md](getting-started-typescript.md) |
| Python (3.10+) | `deixic-sdk` | [getting-started-python.md](getting-started-python.md) |
| Go | `github.com/dx-corp/deixic-go` | [getting-started-go.md](getting-started-go.md) |

The TypeScript and Python clients include task helpers that wait for a result,
save restart checkpoints, and reconnect bounded read requests. The Go module is
the generated Connect client for the same `deixicpublic.v1.DeixicPublicService`
contract, so a Go application supplies credentials, tenant scope, and polling
itself.

For infrastructure-as-code, the
[Terraform provider](https://github.com/dx-corp/terraform-provider-deixic#readme) manages
Deixic business objects through the same public service.

## Before you start

You need:

- a Deixic organization ID and workspace ID;
- a **Deixic tasks** API key, created in Settings → API access, stored in your
  server-side secret manager;
- the ID of a channel (thread) in that workspace that the key can read and
  write. The examples use `company`.

Every guide reads its configuration from the same environment variables:

```sh
export DEIXIC_API_KEY=...            # Keep this out of source control and browser code.
export DEIXIC_ORGANIZATION_ID=org_...
export DEIXIC_WORKSPACE_ID=ws_...
# Optional. Defaults to https://app.deixic.com.
export DEIXIC_BASE_URL=https://app.deixic.com
```

## Concepts shared by every SDK

- **Tenant scope.** The organization and workspace are fixed when you create
  the client. Individual calls cannot replace them.
- **Idempotency keys.** Every mutation (submit, interrupt, respond, resolve)
  takes a key that you choose. Use a new key for each new request, such as the
  ID of the business event that triggered it. Reuse a key only to retry the
  same request with the same body.
- **Acceptance is not completion.** Submitting a task returns an accepted turn.
  Deixic then moves that turn through running, waiting, responded, and finally
  completed, failed, or interrupted. Read or watch the turn until it reaches
  one of those final states.
- **Waiting turns need a decision.** A turn that needs an approval or input
  stays waiting until your application, acting for an authorized person,
  responds. The SDKs never approve anything automatically.
- **Receipts are the record of actions.** A completed answer can reference
  receipts. Check each receipt's state before you treat an external action as
  done.
- **Cursors resume observation.** Event cursors are 64-bit integers. Save the
  last one you processed and resume from it after a restart. If a page reports
  `reset_required`, replace your local copy of the thread with the snapshot on
  that page.
- **Mutations are not retried.** The clients do not resend a mutation after a
  network or availability failure. Your application decides whether to retry
  with the same idempotency key.

The full list of supported operations and the safety contract is in
[deixic-sdk.md](../deixic-sdk.md).
