# Getting started with the Deixic TypeScript SDK

This guide builds a small Node.js script that checks access to a Deixic
channel, submits a task, waits for the answer, and handles an approval request.
Read the [guide index](README.md) first for the credentials and concepts every
SDK shares.

## Requirements

- Node.js 20 or later
- The `DEIXIC_*` environment variables described in the
  [guide index](README.md#before-you-start)

Run the SDK from trusted server-side code. Do not ship an API key to a browser;
a browser application should call its own authenticated server, or supply an
OAuth credential source as shown in [Use rotating credentials](#use-rotating-credentials).

## 1. Install the package

```sh
mkdir deixic-quickstart && cd deixic-quickstart
npm init -y
npm pkg set type=module
npm install @evalops/deixic-sdk
```

## 2. Create a client

Create `client.mjs`:

```js
import { createDeixicClient } from "@evalops/deixic-sdk";

function requiredEnv(name) {
  const value = process.env[name];
  if (!value) throw new Error(`Set ${name}`);
  return value;
}

export const deixic = createDeixicClient({
  apiKey: requiredEnv("DEIXIC_API_KEY"),
  organizationId: requiredEnv("DEIXIC_ORGANIZATION_ID"),
  workspaceId: requiredEnv("DEIXIC_WORKSPACE_ID"),
  // Omit baseUrl to use https://app.deixic.com.
  baseUrl: process.env.DEIXIC_BASE_URL,
});
```

The organization and workspace are fixed for this client. Every request sends
them in the request body and in the `X-Organization-ID` and `X-Workspace-ID`
headers.

## 3. Check setup

Before you submit work, confirm that the key can read the channel and that the
workspace has a model available:

```js
import { deixic } from "./client.mjs";

const setup = await deixic.tasks.checkSetup({ channelId: "company" });
if (setup.status !== "accessible") {
  console.error(setup.status, setup.nextAction, setup.error?.requestId);
  process.exit(1);
}
console.log("Default model:", setup.defaultModel?.model ?? "none");
```

`checkSetup` makes one read request. It does not prove write access;
submission checks write authorization.

## 4. Submit a task and wait for the answer

Create `quickstart.mjs`:

```js
import { deixic } from "./client.mjs";

const channelId = "company";

const task = await deixic.tasks.start({
  channelId,
  body: "Summarize the open changes in this workspace.",
  // One key per business request. Reuse it only to retry this same request.
  idempotencyKey: "quickstart-2026-09-25-001",
});

const result = await task.wait({
  timeoutMs: 120_000,
  onEvent: (event) => console.error("event", event.kind, event.text),
});

switch (result.status) {
  case "completed":
    console.log(result.body);
    for (const receipt of result.receipts) {
      console.log("receipt", receipt.id, receipt.title, receipt.state);
    }
    break;
  case "waiting":
    console.log("Needs a decision:", result.reason, result.event?.requestId);
    break;
  default:
    // failed, interrupted, unfinished, or unacknowledged.
    console.log(result.status, result.reason ?? "");
}
```

Run it:

```sh
node quickstart.mjs
```

`tasks.start()` returns once Deixic accepts the request. `task.wait()` then
follows the turn's events until it completes, fails, is interrupted, or needs
attention. If `timeoutMs` elapses first, the result is `unfinished` and the
work keeps running in Deixic. Stopping observation never cancels the task.

A completed answer does not prove that every external action succeeded. Check
each receipt's `state` (for example `ReceiptLifecycleState.SUCCEEDED`) before
you act on it.

## 5. Answer an approval request

When a task needs an approval, `wait()` returns `status: "waiting"` with the
pending request in `result.event`. Show it to a person who is allowed to
decide, then send that decision:

```js
import {
  OperatingThreadRequestType,
  OperatingThreadResponseAction,
} from "@evalops/deixic-sdk";

if (
  result.status === "waiting" &&
  result.event?.requestKind === OperatingThreadRequestType.APPROVAL
) {
  const request = result.event;
  const approved = await askOperator(request); // Your application's decision.

  await deixic.controls.respond({
    channelId,
    turnId: result.turnId,
    response: {
      requestId: request.requestId,
      callId: request.callId,
      requestKind: request.requestKind,
      action: approved
        ? OperatingThreadResponseAction.APPROVE
        : OperatingThreadResponseAction.DENY,
    },
    idempotencyKey: `decision-${request.requestId}`,
  });

  const after = await task.wait({ timeoutMs: 120_000 });
  console.log(after.status);
}
```

Deixic checks that the caller may make the decision. To stop a running task
instead, call
`deixic.controls.interrupt({ channelId, turnId, idempotencyKey, reason })`.

## 6. Survive a restart

A worker can crash after it submits a task but before it reads the result.
Use `tasks.prepare()` with an `onCheckpoint` callback to save progress, and
`tasks.resume()` in a later process:

```js
import { readFile, writeFile } from "node:fs/promises";
import { deixic } from "./client.mjs";

const save = (checkpoint) =>
  writeFile("task.json", JSON.stringify(checkpoint), { mode: 0o600 });

// First process: save before sending, then submit.
const task = await deixic.tasks.prepare({
  channelId: "company",
  body: "Summarize the open changes in this workspace.",
  idempotencyKey: "quickstart-2026-09-25-002",
  onCheckpoint: save,
});
await task.submit();

// Later process: resume observation. This never submits work.
const resumed = deixic.tasks.resume(
  JSON.parse(await readFile("task.json", "utf8")),
  { onCheckpoint: save },
);
const result = await resumed.wait({ timeoutMs: 120_000 });
```

If the result is `unacknowledged`, the submit response was lost. Call
`await resumed.replay()` to resend the original request with its original key.
Do not start again with a new key. Checkpoints contain the task body but no
credential; store them as customer data.

## Handle errors

SDK failures are `DeixicError` instances:

```js
import { DeixicError } from "@evalops/deixic-sdk";

try {
  await deixic.tasks.start({ channelId: "company", body: "Hi", idempotencyKey: "k-1" });
} catch (error) {
  if (error instanceof DeixicError) {
    console.error(error.kind, error.status, error.code, error.requestId);
  }
  throw error;
}
```

Include `requestId` when you contact support. Mutations are never retried
automatically after a transport or availability failure; retry with the same
idempotency key only when you mean to resend the same request.

## Use rotating credentials

For OAuth or short-lived workload tokens, pass `auth` instead of `apiKey`:

```js
const deixic = createDeixicClient({
  organizationId,
  workspaceId,
  auth: {
    getCredential: () => ({
      accessToken,
      subject,
      organizationId,
      workspaceId,
      scopes: ["console:read", "console:write"],
    }),
    refreshCredential: refreshAccessToken,
  },
});
```

After an authentication challenge, the SDK retries once with the refreshed
credential, and only if it keeps the same subject, tenant, and scopes.

## Next steps

- [TypeScript SDK reference](https://github.com/dx-corp/deixic-node#readme): task
  outcomes, the account-brief example application, thread recovery, and the
  lower-level `threads`, `events`, `messages`, and `receipts` methods.
- [Deixic SDK contract](../deixic-sdk.md): supported operations and safety
  rules.
