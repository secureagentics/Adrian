# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics

"""Vendored protobuf definitions for the Adrian Worker Core API wire format."""

# Pre-load Google well-known types into the descriptor pool before
# buf/validate/validate_pb2.py tries to reference them.
from google.protobuf import duration_pb2 as _duration_pb2  # noqa: F401
from google.protobuf import field_mask_pb2 as _field_mask_pb2  # noqa: F401
from google.protobuf import timestamp_pb2 as _timestamp_pb2  # noqa: F401
