# Local-dev convenience targets. Production builds happen via
# deploy/Dockerfile.backend and docker compose; this Makefile is for
# `go run` workflows on a developer host.

GO ?= go

.PHONY: build run test vet tidy clean proto-sync proto-regen

build:
	$(GO) build -o ./bin/adrian ./cmd/adrian

run:
	$(GO) run ./cmd/adrian

test:
	$(GO) test ./...

vet:
	$(GO) vet ./...

tidy:
	$(GO) mod tidy

clean:
	rm -rf ./bin

# Sync proto/event.proto from the repo-root canonical copy and regenerate
# the Go bindings. Strips the buf.validate import and field annotations
# (see proto/README.md). Requires protoc + protoc-gen-go on PATH.
proto-sync:
	@echo "Syncing proto/event.proto from ../proto/..."
	@sed \
	    -e '/^import "buf\/validate\/validate.proto";$$/d' \
	    -e 's| \[(buf\.validate\.field)[^]]*\]||g' \
	    -e '/^package adrian\.core_api\.v1;$$/a\option go_package = "github.com/secureagentics/Adrian/backend/internal/proto;proto";' \
	    ../proto/event.proto > proto/event.proto
	@$(MAKE) proto-regen

# Regenerate Go bindings from proto/event.proto.
proto-regen:
	@echo "Regenerating internal/proto/event.pb.go..."
	@protoc \
	    --go_out=internal/proto \
	    --go_opt=paths=source_relative \
	    -I proto \
	    proto/event.proto
