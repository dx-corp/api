# Getting started with the Deixic Python SDK

This guide builds a small Python script that checks access to a Deixic
channel, submits a task, waits for the answer, and handles an approval request.
Read the [guide index](README.md) first for the credentials and concepts every
SDK shares.

## Requirements

- Python 3.10 or later
- The `DEIXIC_*` environment variables described in the
  [guide index](README.md#before-you-start)

Use API keys only from trusted server-side code.

## 1. Install the package

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install deixic-sdk
```

## 2. Create a client

Create `client.py`:

```python
import os

from deixic import Deixic

deixic = Deixic(
    api_key=os.environ["DEIXIC_API_KEY"],
    organization_id=os.environ["DEIXIC_ORGANIZATION_ID"],
    workspace_id=os.environ["DEIXIC_WORKSPACE_ID"],
    base_url=os.environ.get("DEIXIC_BASE_URL", "https://app.deixic.com"),
)
```

The organization and workspace are fixed for this client. Every request sends
them in the request body and in the `X-Organization-ID` and `X-Workspace-ID`
headers.

## 3. Check setup

Before you submit work, confirm that the key can read the channel and that the
workspace has a model available:

```python
import sys

from client import deixic

setup = deixic.tasks.check_setup(channel_id="company")
if setup.status != "accessible":
    request_id = setup.error.request_id if setup.error else None
    print(setup.status, setup.next_action, request_id, file=sys.stderr)
    sys.exit(1)
print("Default model:", setup.default_model.model if setup.default_model else "none")
```

`check_setup` makes one read request. It does not prove write access;
submission checks write authorization.

## 4. Submit a task and wait for the answer

Create `quickstart.py`:

```python
import sys

from client import deixic

CHANNEL_ID = "company"

task = deixic.tasks.start(
    channel_id=CHANNEL_ID,
    body="Summarize the open changes in this workspace.",
    # One key per business request. Reuse it only to retry this same request.
    idempotency_key="quickstart-2026-09-25-001",
)

result = task.wait(
    timeout=120,
    on_event=lambda event: print("event", event.kind, event.text, file=sys.stderr),
)

if result.status == "completed":
    print(result.body)
    for receipt in result.receipts:
        print("receipt", receipt.id, receipt.title, receipt.state)
elif result.status == "waiting":
    print("Needs a decision:", result.reason, result.event.request_id if result.event else "")
else:
    # failed, interrupted, unfinished, or unacknowledged.
    print(result.status, result.reason)
```

Run it:

```sh
python quickstart.py
```

`tasks.start()` returns once Deixic accepts the request. `task.wait()` then
follows the turn's events until it completes, fails, is interrupted, or needs
attention. If `timeout` (in seconds) elapses first, the result is `unfinished`
and the work keeps running in Deixic. Stopping observation never cancels the
task.

A completed answer does not prove that every external action succeeded. Check
each receipt's `state` (for example `protocol.RECEIPT_STATE_SUCCEEDED`) before
you act on it.

To validate a structured answer, pass your own parser:
`result.parse(json.loads)`. Parser errors propagate.

## 5. Answer an approval request

When a task needs an approval, `wait()` returns `status == "waiting"` with the
pending request in `result.event`. Show it to a person who is allowed to
decide, then send that decision:

```python
from deixic import protocol as pb

if (
    result.status == "waiting"
    and result.event is not None
    and result.event.request_kind == pb.REQUEST_KIND_APPROVAL
):
    request = result.event
    approved = ask_operator(request)  # Your application's decision.

    deixic.controls.respond(
        channel_id=CHANNEL_ID,
        turn_id=result.turn_id,
        response=pb.RespondToRequestRequest(
            request_id=request.request_id,
            call_id=request.call_id,
            request_kind=request.request_kind,
            action=pb.RESPONSE_ACTION_APPROVE if approved else pb.RESPONSE_ACTION_DENY,
        ),
        idempotency_key=f"decision-{request.request_id}",
    )

    after = task.wait(timeout=120)
    print(after.status)
```

Deixic checks that the caller may make the decision. To stop a running task
instead, call
`deixic.controls.interrupt(channel_id=..., turn_id=..., idempotency_key=..., reason=...)`.

## 6. Survive a restart

A worker can crash after it submits a task but before it reads the result.
Use `tasks.prepare()` with an `on_checkpoint` callback to save progress, and
`tasks.resume()` in a later process:

```python
import json
import os
from pathlib import Path

from client import deixic

STATE = Path("task.json")


def save(checkpoint):
    STATE.write_text(json.dumps(checkpoint))
    os.chmod(STATE, 0o600)


# First process: save before sending, then submit.
task = deixic.tasks.prepare(
    channel_id="company",
    body="Summarize the open changes in this workspace.",
    idempotency_key="quickstart-2026-09-25-002",
    on_checkpoint=save,
)
task.submit()

# Later process: resume observation. This never submits work.
resumed = deixic.tasks.resume(json.loads(STATE.read_text()), on_checkpoint=save)
result = resumed.wait(timeout=120)
```

If the result is `unacknowledged`, the submit response was lost. Call
`resumed.replay()` to resend the original request with its original key. Do
not start again with a new key. Checkpoints contain the task body but no
credential; store them as customer data.

The installed package includes a complete version of this flow:

```sh
python -m deixic.examples.task_result start task.json \
  --channel company --body 'Summarize the open changes in this workspace.'
python -m deixic.examples.task_result resume task.json
```

## Handle errors

SDK failures raise `DeixicError`:

```python
from deixic import DeixicError

try:
    deixic.tasks.start(channel_id="company", body="Hi", idempotency_key="k-1")
except DeixicError as error:
    print(error.kind, error.status_code, error.code, error.request_id)
    raise
```

Include `request_id` when you contact support. Mutations are never retried
automatically after a transport or availability failure; retry with the same
idempotency key only when you mean to resend the same request.

## Authenticate without a stored key

CI jobs and cloud workloads can exchange a platform-issued token for a
short-lived Deixic token instead of storing an API key. Pass a
`credential_provider` instead of `api_key`:

```python
import os

from deixic import (
    Deixic,
    WorkloadFederationCredentialProvider,
    github_actions_assertion_source,
)

identity_url = os.environ["DEIXIC_IDENTITY_URL"]
deixic = Deixic(
    credential_provider=WorkloadFederationCredentialProvider(
        identity_url=identity_url,
        assertion_source=github_actions_assertion_source(
            audience=f"{identity_url}/v1/workload-federation/exchange",
        ),
    ),
    organization_id=os.environ["DEIXIC_ORGANIZATION_ID"],
    workspace_id=os.environ["DEIXIC_WORKSPACE_ID"],
)
```

An organization admin must register the issuer and a matching rule first. See
[Workload federation](https://github.com/dx-corp/deixic-python#workload-federation)
for the setup and the available assertion sources.

## Next steps

- [Python SDK reference](https://github.com/dx-corp/deixic-python#readme): task outcomes,
  the account-brief example application, reading coding output, and the
  lower-level `threads`, `events`, `messages`, and `receipts` methods.
- [Deixic SDK contract](../deixic-sdk.md): supported operations and safety
  rules.
