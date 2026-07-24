#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics

# Regenerate adrian/proto/event_pb2.py and event_pb2.pyi from event.proto.
#
# The SDK vendors the generated buf.validate Python bindings at
# adrian/proto/buf/validate/ but does NOT check in the source
# validate.proto or google wellknown .proto files.  This script stages
# those transient dependencies in a scratch directory, runs protoc with
# the mypy-protobuf plugin, and cleans up.
#
# Dependencies:
#   - protoc                 (apt install protobuf-compiler)
#   - protoc-gen-mypy        (pip install mypy-protobuf, already in pyproject.toml dev deps)
#   - curl                   (for downloading the transient proto sources)
#   - internet access        (to fetch buf.validate + google wellknowns)
#
# Usage: bash tools/regen-proto.sh   (from the SDK repo root)

set -euo pipefail

SDK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SDK_ROOT"

# Canonical proto lives at the repo-root proto/ (single source of truth);
# the generated bindings are committed under adrian/proto/.
PROTO_DIR="$(cd "$SDK_ROOT/../.." && pwd)/proto"

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

# 3. Locate mypy-protobuf plugin (pip install drops it under ~/.local/bin or
# the active venv's bin dir).
PLUGIN="$(command -v protoc-gen-mypy || true)"
if [ -z "$PLUGIN" ]; then
  echo "protoc-gen-mypy not found on PATH. Install with: pip install mypy-protobuf" >&2
  exit 1
fi

# 4. Regenerate
protoc \
  --plugin=protoc-gen-mypy="$PLUGIN" \
  --python_out=adrian/proto \
  --mypy_out=adrian/proto \
  -I "$PROTO_DIR" \
  -I "$DEP_DIR" \
  "$PROTO_DIR/event.proto"

# 5. Rewrite the protoc-generated `from buf.validate import ...` to a relative
# import, so the vendored stubs under adrian/proto/buf/validate/ resolve
# without requiring adrian/proto/ to be on sys.path.  Without this, `import
# adrian` fails in any clean venv that doesn't have a separate top-level
# `buf` package installed.
sed -i 's|^from buf\.validate import|from .buf.validate import|' \
  adrian/proto/event_pb2.py

echo "Regenerated adrian/proto/event_pb2.py + event_pb2.pyi"
