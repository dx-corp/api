# Getting started with the Deixic Go client

This guide builds a small Go program that submits a Deixic task, waits for the
answer, and answers an approval request. Read the [guide index](README.md)
first for the credentials and concepts every SDK shares.

The Go module is the generated [Connect](https://connectrpc.com/docs/go/getting-started)
client for `deixicpublic.v1.DeixicPublicService`. Unlike the TypeScript and
Python SDKs, it has no task helpers: your program adds the credentials and
tenant headers, supplies idempotency keys, and polls for the result. The
client does not choose an organization, workspace, or idempotency key for you.

## Requirements

- Go 1.26 or later
- The `DEIXIC_*` environment variables described in the
  [guide index](README.md#before-you-start)

## 1. Install the module

```sh
mkdir deixic-quickstart && cd deixic-quickstart
go mod init example.com/deixic-quickstart
go get github.com/dx-corp/deixic-go/deixicpublic/v1/deixicpublicv1connect connectrpc.com/connect
```

The generated client sends binary protobuf (`Content-Type: application/proto`)
with `Connect-Protocol-Version: 1`, which is what the Deixic API requires. Do
not switch it to JSON encoding.

## 2. Write the program

Create `main.go`. It builds a client, submits a task, polls the thread until
the accepted turn reaches a final or waiting state, and prints the answer:

```go
package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"time"

	"connectrpc.com/connect"
	deixicv1 "github.com/dx-corp/deixic-go/deixicpublic/v1"
	"github.com/dx-corp/deixic-go/deixicpublic/v1/deixicpublicv1connect"
)

const channelID = "company"

func main() {
	ctx := context.Background()
	scope := &deixicv1.Scope{
		OrganizationId: mustEnv("DEIXIC_ORGANIZATION_ID"),
		WorkspaceId:    mustEnv("DEIXIC_WORKSPACE_ID"),
	}
	baseURL := os.Getenv("DEIXIC_BASE_URL")
	if baseURL == "" {
		baseURL = "https://app.deixic.com"
	}

	client := deixicpublicv1connect.NewDeixicPublicServiceClient(
		http.DefaultClient,
		baseURL,
		connect.WithInterceptors(authInterceptor(mustEnv("DEIXIC_API_KEY"), scope)),
	)

	submitted, err := client.SubmitTask(ctx, connect.NewRequest(&deixicv1.SubmitTaskRequest{
		Scope:    scope,
		ThreadId: channelID,
		Body:     "Summarize the open changes in this workspace.",
		// One key per business request. Reuse it only to retry this same request.
		IdempotencyKey: "quickstart-2026-09-25-001",
	}))
	if err != nil {
		log.Fatal(describe(err))
	}
	turnID := submitted.Msg.GetAcceptedTurn().GetTurnId()
	fmt.Println("accepted turn", turnID)

	turn, err := waitForTurn(ctx, client, scope, turnID, 2*time.Minute)
	if err != nil {
		log.Fatal(describe(err))
	}

	switch turn.GetState() {
	case deixicv1.TurnState_TURN_STATE_COMPLETED:
		answer, err := findMessage(ctx, client, scope, turn.GetAssistantMessageId())
		if err != nil {
			log.Fatal(describe(err))
		}
		fmt.Println(answer.GetBody())
		fmt.Println("receipts:", answer.GetReceiptIds())
	case deixicv1.TurnState_TURN_STATE_WAITING:
		fmt.Println("needs a decision:", turn.GetWaitingReason())
	default:
		fmt.Println(turn.GetState(), turn.GetTerminalError().GetCode())
	}
}

// authInterceptor adds the bearer token and tenant headers to every call,
// including streaming calls such as WatchEvents.
func authInterceptor(apiKey string, scope *deixicv1.Scope) connect.Interceptor {
	return &headerInterceptor{set: func(h http.Header) {
		h.Set("Authorization", "Bearer "+apiKey)
		h.Set("X-Organization-ID", scope.GetOrganizationId())
		h.Set("X-Workspace-ID", scope.GetWorkspaceId())
	}}
}

type headerInterceptor struct{ set func(http.Header) }

func (i *headerInterceptor) WrapUnary(next connect.UnaryFunc) connect.UnaryFunc {
	return func(ctx context.Context, req connect.AnyRequest) (connect.AnyResponse, error) {
		i.set(req.Header())
		return next(ctx, req)
	}
}

func (i *headerInterceptor) WrapStreamingClient(next connect.StreamingClientFunc) connect.StreamingClientFunc {
	return func(ctx context.Context, spec connect.Spec) connect.StreamingClientConn {
		conn := next(ctx, spec)
		i.set(conn.RequestHeader())
		return conn
	}
}

func (i *headerInterceptor) WrapStreamingHandler(next connect.StreamingHandlerFunc) connect.StreamingHandlerFunc {
	return next
}

// waitForTurn polls the thread until the accepted turn completes, fails, is
// interrupted, or waits for a decision. A timeout stops polling only; the
// task keeps running in Deixic.
func waitForTurn(
	ctx context.Context,
	client deixicpublicv1connect.DeixicPublicServiceClient,
	scope *deixicv1.Scope,
	turnID string,
	timeout time.Duration,
) (*deixicv1.TaskTurn, error) {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	for {
		thread, err := client.GetThread(ctx, connect.NewRequest(&deixicv1.GetThreadRequest{
			Scope:    scope,
			ThreadId: channelID,
			Limit:    50,
		}))
		if err != nil {
			return nil, err
		}
		for _, turn := range thread.Msg.GetTurns() {
			if turn.GetTurnId() != turnID {
				continue
			}
			switch turn.GetState() {
			case deixicv1.TurnState_TURN_STATE_COMPLETED,
				deixicv1.TurnState_TURN_STATE_FAILED,
				deixicv1.TurnState_TURN_STATE_INTERRUPTED,
				deixicv1.TurnState_TURN_STATE_WAITING:
				return turn, nil
			}
		}
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-time.After(2 * time.Second):
		}
	}
}

// findMessage pages through the thread to find the turn's final answer.
func findMessage(
	ctx context.Context,
	client deixicpublicv1connect.DeixicPublicServiceClient,
	scope *deixicv1.Scope,
	messageID string,
) (*deixicv1.TaskMessage, error) {
	pageToken := ""
	for {
		thread, err := client.GetThread(ctx, connect.NewRequest(&deixicv1.GetThreadRequest{
			Scope:     scope,
			ThreadId:  channelID,
			Limit:     50,
			PageToken: pageToken,
		}))
		if err != nil {
			return nil, err
		}
		for _, message := range thread.Msg.GetMessages() {
			if message.GetId() == messageID {
				return message, nil
			}
		}
		pageToken = thread.Msg.GetNextPageToken()
		if pageToken == "" {
			return nil, fmt.Errorf("message %s not found", messageID)
		}
	}
}

func describe(err error) string {
	var connectErr *connect.Error
	if errors.As(err, &connectErr) {
		return fmt.Sprintf("%s: %s (request %s)",
			connectErr.Code(), connectErr.Message(), connectErr.Meta().Get("X-Request-ID"))
	}
	return err.Error()
}

func mustEnv(name string) string {
	value := os.Getenv(name)
	if value == "" {
		log.Fatalf("set %s", name)
	}
	return value
}
```

Run it:

```sh
go run .
```

What the program does:

- **Authentication and scope.** `authInterceptor` sends the API key as a
  bearer token and the organization and workspace in the `X-Organization-ID`
  and `X-Workspace-ID` headers. Every request message also carries the same
  `Scope`, as the TypeScript and Python SDKs do. Set both from the same values.
- **Submission.** `SubmitTask` returns once Deixic accepts the request. The
  `AcceptedTurn` identifies the work to follow. If the call fails with a
  network error, you cannot tell whether Deixic accepted it: retry with the
  same idempotency key and body, never a new key.
- **Waiting.** `waitForTurn` polls `GetThread` until the turn is completed,
  failed, interrupted, or waiting for a decision. A `responded` turn has a
  preliminary answer and is still running, so the program keeps polling.
  Cancelling the context stops polling only; the task keeps running in Deixic.
- **The answer.** A completed turn names its final message in
  `AssistantMessageId`. `findMessage` pages through the thread to read it. A
  completed answer does not prove every external action succeeded; read each
  receipt with `GetReceipt` and check its state before acting on it.

To stream progress instead of polling, call `WatchEvents` with the thread's
`replay_cursor` and save each event's `cursor`. If a page sets
`reset_required`, replace your local copy of the thread with the snapshot on
that page before you continue.

## 3. Answer an approval request

A waiting turn needs a decision from a person who is allowed to make it. Find
the turn's pending request in its events, then respond. Add this function to
`main.go` and call it from the `TURN_STATE_WAITING` case once your application
has the decision:

```go
// approve answers the turn's pending approval request after an authorized
// person decides.
func approve(
	ctx context.Context,
	client deixicpublicv1connect.DeixicPublicServiceClient,
	scope *deixicv1.Scope,
	turn *deixicv1.TaskTurn,
	approved bool,
) error {
	var request *deixicv1.TaskEvent
	cursor := turn.GetFirstCursor() - 1
	for {
		page, err := client.ListEvents(ctx, connect.NewRequest(&deixicv1.ListEventsRequest{
			Scope:       scope,
			ThreadId:    channelID,
			AfterCursor: cursor,
			Limit:       100,
		}))
		if err != nil {
			return err
		}
		for _, event := range page.Msg.GetEvents() {
			if event.GetTurnId() == turn.GetTurnId() &&
				event.GetRequestKind() == deixicv1.RequestKind_REQUEST_KIND_APPROVAL {
				request = event // Keep the latest approval request for this turn.
			}
		}
		cursor = page.Msg.GetNextCursor()
		if !page.Msg.GetHasMore() {
			break
		}
	}
	if request == nil {
		return errors.New("no approval request is visible for this turn")
	}

	action := deixicv1.ResponseAction_RESPONSE_ACTION_DENY
	if approved {
		action = deixicv1.ResponseAction_RESPONSE_ACTION_APPROVE
	}
	_, err := client.RespondToRequest(ctx, connect.NewRequest(&deixicv1.RespondToRequestRequest{
		Scope:          scope,
		ThreadId:       channelID,
		TurnId:         turn.GetTurnId(),
		RequestId:      request.GetRequestId(),
		CallId:         request.GetCallId(),
		RequestKind:    request.GetRequestKind(),
		Action:         action,
		IdempotencyKey: "decision-" + request.GetRequestId(),
	}))
	return err
}
```

Deixic checks that the caller may make the decision. To stop a running task
instead, call `InterruptTask` with the turn ID, a new idempotency key, and a
reason.

## Handle errors

Failed calls return a `*connect.Error`. Its `Code()` gives the category, and
the `X-Request-ID` response header, available from `Meta()`, identifies the
request when you contact support. The `describe` function in the program above
prints both.

## Next steps

- [Go client reference](https://github.com/dx-corp/deixic-go#readme): module layout and
  source of the generated code.
- [TypeScript](getting-started-typescript.md) and
  [Python](getting-started-python.md) guides: the SDK task helpers show the
  full observation and restart-recovery behavior that a Go program can follow.
- [Deixic SDK contract](../deixic-sdk.md): supported operations and safety
  rules.
