# Deixic API contracts

This repository contains the reviewed public Protocol Buffer contracts and
generated OpenAPI descriptions used by the Deixic SDK. The authoritative
source remains `dx-corp/mono`; `.repository-projection.json` records the exact
source revision used for each projection.

`deixic.v1.DeixicService` is the supported SDK facade. Its eight supported
operations and their mutation requirements are recorded in
`contracts/public-surface.json`. The additional packages in `proto/` are the
transitive message and service dependencies required to compile that facade.

The checked-in OpenAPI documents are generated artifacts. To regenerate them
with the pinned public plugin:

```sh
buf dep update
buf lint
buf generate
python3 scripts/contracts/normalize-openapi-generated.py openapi
node scripts/distribution-validation.mjs --name api --target .
```

`buf dep update`, generation, and validation access the Buf Schema Registry for
the locked dependencies and pinned plugin. They do not contact a Deixic
service. The normalization step preserves OpenAPI 3.1 semantics for protobuf
oneofs, enum constraints, repeated fields, and bytes validation.

This contract repository does not publish SDK packages or grant access to a
Deixic deployment. Client installation and authentication are described in
`docs/deixic-sdk.md`.
