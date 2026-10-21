// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package main

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"time"
)

var unsafeName = regexp.MustCompile(`[^A-Za-z0-9._-]+`)

// autoSavePath picks a new report file in dir named after the time and
// model, for example 2026-10-08T16-40-29Z_gpt-6.1-sol_runs3.json. It
// never returns the name of an existing file.
func autoSavePath(dir, model string, runs int, now time.Time) string {
	name := now.UTC().Format("2006-01-02T15-04-05Z") + "_" + unsafeName.ReplaceAllString(model, "-")
	if runs > 1 {
		name += fmt.Sprintf("_runs%d", runs)
	}
	path := filepath.Join(dir, name+".json")
	for i := 2; fileExists(path); i++ {
		path = filepath.Join(dir, fmt.Sprintf("%s-%d.json", name, i))
	}
	return path
}

func fileExists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

// ensureResultsDir creates dir if needed. A new folder gets its own
// .gitignore so saved reports do not show up as changes in git (which
// would also mark every later report as having uncommitted changes).
// To keep a report in git, add it with git add -f.
func ensureResultsDir(dir string) error {
	if fileExists(dir) {
		return nil
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(dir, ".gitignore"), []byte("# adrian-eval reports; add one with git add -f to keep it\n*\n"), 0o644)
}
