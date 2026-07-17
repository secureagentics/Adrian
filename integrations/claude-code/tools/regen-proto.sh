#!/usr/bin/env bash
# Regenerate adrian_cc/proto/event_pb2.py + event_pb2.pyi from the repo-root
# canonical proto (//proto/event.proto) so the plugin's vendored bindings never
# drift from the wire format the backend and Python SDK share. This mirrors the
# SDK's regen path (sdk/python/tools/regen-proto.sh); both generate from the
# same //proto/event.proto.
#
# The plugin vendors the generated buf.validate Python bindings at
# adrian_cc/proto/buf/validate/ but does NOT check in validate.proto or the
# google wellknown .proto files. This script stages those transient deps in a
# scratch dir, runs protoc with the mypy-protobuf plugin, and cleans up. The
# buf.validate bindings themselves are vendored separately and are not
# regenerated here (same as the SDK).
#
# IMPORTANT: use a protoc/protobuf whose generated runtime is compatible with
# the vendored protobuf under vendor/ (currently protobuf 7.35.x). A much newer
# generator emits a runtime_version assertion that can refuse to import against
# the vendored runtime. After regenerating, run
#   PYTHONPATH=vendor:. python -m adrian_cc.agent verify
# (or the test suite) to confirm the bindings still load and round-trip.
#
# Dependencies:
#   - protoc            (apt install protobuf-compiler)
#   - protoc-gen-mypy   (pip install mypy-protobuf)
#   - curl + network    (fetches the buf.validate + google wellknown proto deps)
#
# Usage: bash integrations/claude-code/tools/regen-proto.sh

set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PLUGIN_ROOT"

# Canonical proto: the repo-root proto/ (single source of truth), the same
# source the SDK and backend generate from.
PROTO_DIR="$(cd "$PLUGIN_ROOT/../.." && pwd)/proto"

DEP_DIR="$(mktemp -d)"
trap 'rm -rf "$DEP_DIR"' EXIT

# 1. buf.validate/validate.proto
mkdir -p "$DEP_DIR/buf/validate"
curl -fsSL -o "$DEP_DIR/buf/validate/validate.proto" \
  https://raw.githubusercontent.com/bufbuild/protovalidate/main/proto/protovalidate/buf/validate/validate.proto

# 2. google wellknown imports
GPROTO_URL="https://raw.githubusercontent.com/protocolbuffers/protobuf/main/src/google/protobuf"
mkdir -p "$DEP_DIR/google/protobuf"
for f in descriptor.proto duration.proto field_mask.proto timestamp.proto; do
  curl -fsSL -o "$DEP_DIR/google/protobuf/$f" "$GPROTO_URL/$f"
done

# 3. Locate the mypy-protobuf plugin.
PLUGIN="$(command -v protoc-gen-mypy || true)"
if [ -z "$PLUGIN" ]; then
  echo "protoc-gen-mypy not found on PATH. Install with: pip install mypy-protobuf" >&2
  exit 1
fi

# 4. Regenerate into the plugin's proto package.
protoc \
  --plugin=protoc-gen-mypy="$PLUGIN" \
  --python_out=adrian_cc/proto \
  --mypy_out=adrian_cc/proto \
  -I "$PROTO_DIR" \
  -I "$DEP_DIR" \
  "$PROTO_DIR/event.proto"

# 5. Rewrite the protoc-generated `from buf.validate import ...` to a relative
# import so the vendored stubs under adrian_cc/proto/buf/validate/ resolve
# without adrian_cc/proto/ being on sys.path (matches the checked-in bindings).
sed -i 's|^from buf\.validate import|from .buf.validate import|' \
  adrian_cc/proto/event_pb2.py

echo "Regenerated adrian_cc/proto/event_pb2.py + event_pb2.pyi from $PROTO_DIR/event.proto"
