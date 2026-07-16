# Generated protobuf bindings

These files are GENERATED from the repo-root canonical proto
[`//proto/event.proto`](../../../../proto/event.proto), the single source of
truth shared by the backend, the Python SDK, and this plugin. Do not hand-edit
them.

- `event_pb2.py`, `event_pb2.pyi` are regenerated from `//proto/event.proto`.
- `buf/validate/validate_pb2.py` is a vendored buf.validate binding (generated
  once, not regenerated here, matching the SDK).

## Regenerate after a proto change

Whenever `//proto/event.proto` changes, regenerate this copy so the plugin's
wire format cannot drift from the backend/SDK:

```
bash integrations/claude-code/tools/regen-proto.sh
```

It needs `protoc`, `protoc-gen-mypy` (`pip install mypy-protobuf`), `curl`, and
network access (it fetches the buf.validate and google wellknown proto deps).
The SDK regenerates the same way via `sdk/python/tools/regen-proto.sh`; both
read the same `//proto/event.proto`.

## Toolchain note

Use a protoc/protobuf whose generated runtime is compatible with the vendored
protobuf under [`../../vendor/`](../../vendor) (currently 7.35.x). A much newer
generator emits a `runtime_version` assertion that can refuse to import against
the vendored runtime. After regenerating, confirm the bindings still load and
round-trip:

```
PYTHONPATH=vendor:. PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python \
  python -m adrian_cc.agent verify
```
