// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

// Package migrations embeds the SQL migration files for the Adrian
// backend. The same files are also COPYed into the adrian-setup
// bootstrap image (deploy/Dockerfile.setup), where setup.py applies
// pending migrations on bootstrap / apply-migrations. The backend
// also checks pending migrations at startup so upgrades after
// `git pull` work without a manual step. Both runners record applied
// filenames in schema_migrations.
package migrations

import "embed"

//go:embed *.sql
var Files embed.FS
