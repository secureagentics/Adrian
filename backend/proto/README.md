# Backend proto

The wire schema for SDK <-> backend WebSocket frames.

## Layout

- `event.proto` is the Go-side copy of the wire schema. The single source of truth is `proto/event.proto` at the repo root.
- The Go-side copy is **stripped** of the `buf.validate` import and field annotations:
  - The `import "buf/validate/validate.proto";` line is removed.
  - Every `[(buf.validate.field).*]` annotation is removed.
  Both are non-wire metadata; removing them keeps the byte format identical and avoids a buf.build dependency on the Go side.
- Generated Go bindings live at `backend/internal/proto/event.pb.go`. They are committed so `go build` works without `protoc` on the developer host.

## Sync rules

When the wire schema changes:

1. Edit `proto/event.proto` (the source of truth).
2. Regenerate the SDK's Python bindings: `cd sdk/python && uv run bash tools/regen-proto.sh`.
3. Sync the backend copy and regenerate the Go bindings: `cd backend && make proto-sync`.
4. Commit both.

`make proto-sync` runs the strip + protoc invocation. It requires `protoc` and `protoc-gen-go` on `PATH`.

## Manual regeneration (if you need it)

```sh
cd backend
protoc --go_out=internal/proto --go_opt=paths=source_relative -I proto proto/event.proto
```

This is what `make proto-regen` does.
