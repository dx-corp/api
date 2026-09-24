# Deixic API contracts

This repository contains the reviewed public Protocol Buffer contracts and
generated OpenAPI descriptions used by the Deixic SDK. The authoritative
source remains `dx-corp/mono`; `.repository-projection.json` records the exact
source revision used for each projection.

`deixicpublic.v1.DeixicPublicService` is the supported public application
contract. The task SDK's eight supported operations and mutation requirements
are recorded in `contracts/public-surface.json`. The same protocol also serves
the Code client's readiness, setup, and issue-report calls and the Terraform
provider's business-object CRUD resource. The protocol imports only the Protobuf
well-known `Timestamp` type. Internal service definitions are absent from this
repository and cannot be imported into the public contract.

## Compatibility and promotion

The `v1` package is a durable external API. Existing field numbers, names,
types, enum values, service methods, and observable behavior remain stable.
New optional fields and methods may be added after their public meaning is
documented. Removed fields and enum values must reserve both their numbers and
names; incompatible changes require a new versioned package. The source repo
runs Buf breaking checks against the published public module.

Promoting an internal concept requires a public use case, an explicit public
message with no internal imports or `Any` escape hatch, a server-side
projection, language client compatibility checks, and an audit of every built
artifact's complete descriptor and schema closure. Similar field layouts do
not justify sharing an internal message. The customer checkpoint format
`deixic.task.v1` is versioned separately from this wire protocol.

The checked-in OpenAPI documents are generated artifacts. To regenerate them
with the pinned public plugin:

```sh
buf lint
buf generate
python3 scripts/contracts/normalize-openapi-generated.py openapi
node scripts/distribution-validation.mjs --name api --target .
```

Generation accesses the Buf Schema Registry for the pinned plugin. It does not
contact a Deixic service. The normalization step preserves OpenAPI 3.1 semantics for protobuf
oneofs, enum constraints, repeated fields, and bytes validation.

This contract repository does not publish SDK packages or grant access to a
Deixic deployment. Client installation and authentication are described in
`docs/deixic-sdk.md`.
