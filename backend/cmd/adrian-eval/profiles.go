// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"

	"github.com/secureagentics/Adrian/backend/internal/db"
	"github.com/secureagentics/Adrian/backend/internal/store"
)

// Profile is one agent profile a case can be judged against. It mirrors
// what a customer configures in the dashboard: the agent's remit, the
// behaviours they expect (M0) and the risks they want flagged (M3).
// The judge reads these from the database and renders them into its
// system prompt, so the same action can be in scope for one agent and a
// violation for another.
type Profile struct {
	Name     string   `json:"name"`
	Remit    string   `json:"remit"`
	Expected []string `json:"expected"` // user-defined M0 entries
	Risks    []string `json:"risks"`    // user-defined M3 entries
}

// LoadProfiles reads a JSON object of {name: profile} from path.
func LoadProfiles(path string) (map[string]Profile, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var out map[string]Profile
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&out); err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	for name, p := range out {
		if p.Remit == "" {
			return nil, fmt.Errorf("%s: profile %q has no remit", path, name)
		}
	}
	return out, nil
}

// ProfileStore is a throwaway database holding the profiles a run needs.
// It is the real store the backend uses, so the judge resolves profiles
// through the production path rather than a stand-in.
type ProfileStore struct {
	Store *store.Store
	ids   map[string]string // profile name -> row id
	close func()
}

// IDFor returns the database id for a profile name, or "" when the name
// is unknown, which makes the judge fall back to the generic remit.
func (p *ProfileStore) IDFor(name string) string {
	if p == nil {
		return ""
	}
	return p.ids[name]
}

// Close releases the temporary database.
func (p *ProfileStore) Close() {
	if p != nil && p.close != nil {
		p.close()
	}
}

// NewProfileStore writes the given profiles into a temporary SQLite
// database created with the project's own migrations, and returns a
// store the judge can read them from.
func NewProfileStore(ctx context.Context, profiles map[string]Profile) (*ProfileStore, error) {
	if len(profiles) == 0 {
		return nil, nil
	}
	dir, err := os.MkdirTemp("", "adrian-eval-profiles-")
	if err != nil {
		return nil, err
	}
	cleanup := func() { os.RemoveAll(dir) }

	conn, err := db.Open(filepath.Join(dir, "eval.db"))
	if err != nil {
		cleanup()
		return nil, fmt.Errorf("open profile database: %w", err)
	}

	ps := &ProfileStore{
		Store: store.New(conn),
		ids:   make(map[string]string, len(profiles)),
		close: func() { conn.Close(); cleanup() },
	}
	for name, p := range profiles {
		m0, _ := json.Marshal(p.Expected)
		m3, _ := json.Marshal(p.Risks)
		row := &store.AgentProfile{
			ID: "eval-" + name, Name: name, Enabled: true, Remit: p.Remit,
			M0Entries: string(m0), M3Entries: string(m3),
		}
		if err := ps.Store.CreateAgentProfile(ctx, row); err != nil {
			ps.Close()
			return nil, fmt.Errorf("create profile %q: %w", name, err)
		}
		ps.ids[name] = row.ID
	}
	return ps, nil
}
