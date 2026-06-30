// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package api

import (
	"net/http"

	"github.com/secureagentics/Adrian/backend/internal/store"
)

type policyResponse struct {
	Mode                        string `json:"mode"`
	PolicyM0                    bool   `json:"policy_m0"`
	PolicyM2                    bool   `json:"policy_m2"`
	PolicyM3                    bool   `json:"policy_m3"`
	PolicyM4                    bool   `json:"policy_m4"`
	FailClosedOnClassifierError bool   `json:"fail_closed_on_classifier_error"`
	UpdatedAt                   string `json:"updated_at"`
}

type policyPatchRequest struct {
	Mode                        *string `json:"mode"`
	PolicyM0                    *bool   `json:"policy_m0"`
	PolicyM2                    *bool   `json:"policy_m2"`
	PolicyM3                    *bool   `json:"policy_m3"`
	PolicyM4                    *bool   `json:"policy_m4"`
	FailClosedOnClassifierError *bool   `json:"fail_closed_on_classifier_error"`
}

func (s *Server) handleGetPolicy(w http.ResponseWriter, r *http.Request) {
	pol, err := s.store.GetPolicy(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, "policy lookup failed")
		return
	}
	writeJSON(w, http.StatusOK, policyResponseFromStore(pol))
}

func (s *Server) handleUpdatePolicy(w http.ResponseWriter, r *http.Request) {
	var req policyPatchRequest
	if err := decodeJSON(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid json")
		return
	}
	if req.Mode != nil && !validMode(*req.Mode) {
		writeError(w, http.StatusBadRequest, "invalid mode (must be alert/hitl/block)")
		return
	}

	patch := &store.PolicyPatch{
		Mode:                        req.Mode,
		PolicyM0:                    req.PolicyM0,
		PolicyM2:                    req.PolicyM2,
		PolicyM3:                    req.PolicyM3,
		PolicyM4:                    req.PolicyM4,
		FailClosedOnClassifierError: req.FailClosedOnClassifierError,
	}
	if err := s.store.UpdatePolicy(r.Context(), patch); err != nil {
		writeError(w, http.StatusInternalServerError, "update failed")
		return
	}

	pol, err := s.store.GetPolicy(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, "post-update read failed")
		return
	}

	details := map[string]any{}
	if req.Mode != nil {
		details["mode"] = *req.Mode
	}
	if req.PolicyM0 != nil {
		details["policy_m0"] = *req.PolicyM0
	}
	if req.PolicyM2 != nil {
		details["policy_m2"] = *req.PolicyM2
	}
	if req.PolicyM3 != nil {
		details["policy_m3"] = *req.PolicyM3
	}
	if req.PolicyM4 != nil {
		details["policy_m4"] = *req.PolicyM4
	}
	if req.FailClosedOnClassifierError != nil {
		details["fail_closed_on_classifier_error"] = *req.FailClosedOnClassifierError
	}
	writeAuditLog(r.Context(), s.store, userID(r), "policy_updated", "policies", details)

	writeJSON(w, http.StatusOK, policyResponseFromStore(pol))
}

func policyResponseFromStore(p *store.Policy) policyResponse {
	return policyResponse{
		Mode:                        p.Mode,
		PolicyM0:                    p.PolicyM0,
		PolicyM2:                    p.PolicyM2,
		PolicyM3:                    p.PolicyM3,
		PolicyM4:                    p.PolicyM4,
		FailClosedOnClassifierError: p.FailClosedOnClassifierError,
		UpdatedAt:                   p.UpdatedAt.UTC().Format("2006-01-02T15:04:05.000Z"),
	}
}

func validMode(m string) bool {
	switch m {
	case "alert", "hitl", "block":
		return true
	}
	return false
}
