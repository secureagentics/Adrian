// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package api

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"log/slog"
	"net/http"
	"time"

	"github.com/secureagentics/Adrian/backend/internal/auth"
	"github.com/secureagentics/Adrian/backend/internal/store"
)

const sessionLifetime = 24 * time.Hour

type loginRequest struct {
	Email    string `json:"email"`
	Password string `json:"password"`
}

type loginResponse struct {
	UserID             string `json:"user_id"`
	Email              string `json:"email"`
	Name               string `json:"name"`
	Role               string `json:"role"`
	MustChangePassword bool   `json:"must_change_password"`
}

type changePasswordRequest struct {
	OldPassword string `json:"old_password"`
	NewPassword string `json:"new_password"`
}

func (s *Server) handleLogin(w http.ResponseWriter, r *http.Request) {
	var req loginRequest
	if err := decodeJSON(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid json")
		return
	}
	if req.Email == "" || req.Password == "" {
		writeError(w, http.StatusBadRequest, "email and password required")
		return
	}

	user, err := s.store.LookupUserByEmail(r.Context(), req.Email)
	if err != nil {
		if errors.Is(err, store.ErrNotFound) {
			writeAuditLog(r.Context(), s.store, "", "login_failed", "users",
				map[string]any{"email": req.Email, "reason": "no such user"})
			writeError(w, http.StatusUnauthorized, "invalid credentials")
			return
		}
		writeError(w, http.StatusInternalServerError, "lookup failed")
		return
	}

	if err := auth.Verify(req.Password, user.PasswordHash); err != nil {
		writeAuditLog(r.Context(), s.store, user.ID, "login_failed", "users",
			map[string]any{"reason": "wrong password"})
		writeError(w, http.StatusUnauthorized, "invalid credentials")
		return
	}

	token := newSessionToken()
	expiresAt := time.Now().Add(sessionLifetime)
	if err := s.store.CreateSession(r.Context(), token, user.ID, expiresAt); err != nil {
		slog.ErrorContext(r.Context(), "session.create_failed", "error", err)
		writeError(w, http.StatusInternalServerError, "session create failed")
		return
	}

	setSessionCookie(w, token, sessionLifetime)

	writeAuditLog(r.Context(), s.store, user.ID, "login_success", "users", nil)

	writeJSON(w, http.StatusOK, loginResponse{
		UserID:             user.ID,
		Email:              user.Email,
		Name:               user.Name,
		Role:               user.Role,
		MustChangePassword: user.MustChangePassword,
	})
}

func (s *Server) handleLogout(w http.ResponseWriter, r *http.Request) {
	if tok := extractToken(r); tok != "" {
		_ = s.store.DeleteSession(r.Context(), tok)
	}
	clearSessionCookie(w)

	if uid := userID(r); uid != "" {
		writeAuditLog(r.Context(), s.store, uid, "logout", "users", nil)
	}

	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) handleChangePassword(w http.ResponseWriter, r *http.Request) {
	var req changePasswordRequest
	if err := decodeJSON(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid json")
		return
	}
	if len(req.NewPassword) < 8 || len(req.NewPassword) > 256 {
		writeError(w, http.StatusBadRequest, "new password must be 8-256 chars")
		return
	}

	uid := userID(r)
	user, err := s.store.LookupUserByID(r.Context(), uid)
	if err != nil {
		writeError(w, http.StatusUnauthorized, "user not found")
		return
	}

	if err := auth.Verify(req.OldPassword, user.PasswordHash); err != nil {
		writeError(w, http.StatusUnauthorized, "old password incorrect")
		return
	}

	newHash, err := auth.Hash(req.NewPassword)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "hash failed")
		return
	}
	if err := s.store.UpdatePassword(r.Context(), uid, newHash); err != nil {
		writeError(w, http.StatusInternalServerError, "update failed")
		return
	}

	// Invalidate every other session for this user. The current
	// session stays valid so the dashboard doesn't have to log back in.
	_ = s.store.DeleteSessionsForUser(r.Context(), uid, sessionID(r))

	writeAuditLog(r.Context(), s.store, uid, "password_changed", "users", nil)

	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) handleMe(w http.ResponseWriter, r *http.Request) {
	user, err := s.store.LookupUserByID(r.Context(), userID(r))
	if err != nil {
		writeError(w, http.StatusNotFound, "user not found")
		return
	}
	writeJSON(w, http.StatusOK, loginResponse{
		UserID:             user.ID,
		Email:              user.Email,
		Name:               user.Name,
		Role:               user.Role,
		MustChangePassword: user.MustChangePassword,
	})
}

func setSessionCookie(w http.ResponseWriter, token string, ttl time.Duration) {
	http.SetCookie(w, &http.Cookie{
		Name:     sessionCookieName,
		Value:    token,
		Path:     "/",
		MaxAge:   int(ttl.Seconds()),
		HttpOnly: true,
		Secure:   true,
		SameSite: http.SameSiteLaxMode,
	})
}

func clearSessionCookie(w http.ResponseWriter) {
	http.SetCookie(w, &http.Cookie{
		Name:     sessionCookieName,
		Value:    "",
		Path:     "/",
		MaxAge:   -1,
		HttpOnly: true,
		Secure:   true,
		SameSite: http.SameSiteLaxMode,
	})
}

func newSessionToken() string {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		// crypto/rand failure is unrecoverable; the program shouldn't
		// continue handing out predictable tokens.
		panic("crypto/rand failed: " + err.Error())
	}
	return hex.EncodeToString(b)
}
