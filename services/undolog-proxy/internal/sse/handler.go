package sse

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"
)

// parseLastEventID parses the Last-Event-ID header value. When the header is
// omitted or empty, it returns present=false without error. When the header is
// non-empty, it parses an unsigned monotonic integer sequence ID.
func parseLastEventID(raw string) (id uint64, present bool, err error) {
	trimmed := strings.TrimSpace(raw)
	if trimmed == "" {
		return 0, false, nil
	}
	parsed, err := strconv.ParseUint(trimmed, 10, 64)
	if err != nil {
		return 0, false, err
	}
	return parsed, true, nil
}

// Handler serves the SSE endpoint used by the dashboard.
func (b *Broadcaster) Handler(w http.ResponseWriter, r *http.Request) {
	orgID := strings.TrimSpace(r.Header.Get("X-Org-Id"))
	if orgID == "" {
		http.Error(w, "org_id required", http.StatusUnauthorized)
		return
	}

	lastEventID, hasLastEventID, err := parseLastEventID(r.Header.Get("Last-Event-ID"))
	if err != nil {
		http.Error(w, "invalid Last-Event-ID", http.StatusBadRequest)
		return
	}

	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming unsupported", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.WriteHeader(http.StatusOK)

	// SSE streams must outlive the server WriteTimeout, which would otherwise
	// terminate the connection while the dashboard idles between heartbeats.
	// Clearing the deadline is best-effort: writers without a deadline concept
	// (e.g. httptest.ResponseRecorder) report ErrNotSupported, which is a no-op.
	_ = http.NewResponseController(w).SetWriteDeadline(time.Time{})

	// Flush headers so client observes stream as open before first event arrives.
	flusher.Flush()

	var lastEventIDPtr *uint64
	if hasLastEventID {
		lastEventIDPtr = &lastEventID
	}
	replay, events, unsubscribe := b.Subscribe(orgID, lastEventIDPtr)
	defer unsubscribe()

	for i := range replay {
		if err := writeEvent(w, replay[i]); err != nil {
			return
		}
		flusher.Flush()
	}

	heartbeat := time.NewTicker(25 * time.Second)
	defer heartbeat.Stop()

	for {
		select {
		case evt, ok := <-events:
			if !ok {
				return
			}
			if err := writeEvent(w, evt); err != nil {
				return
			}
			flusher.Flush()
		case <-heartbeat.C:
			// A failed heartbeat means the connection is gone (e.g. a silent
			// network drop), so end the handler instead of idling forever.
			if _, err := fmt.Fprint(w, ": ping\n\n"); err != nil {
				return
			}
			flusher.Flush()
		case <-r.Context().Done():
			return
		}
	}
}

func writeEvent(w http.ResponseWriter, evt Event) error {
	payload, err := json.Marshal(evt)
	if err != nil {
		return err
	}
	if _, err := fmt.Fprintf(w, "event: %s\n", evt.Type); err != nil {
		return err
	}
	if evt.ID > 0 {
		if _, err := fmt.Fprintf(w, "id: %s\n", strconv.FormatUint(evt.ID, 10)); err != nil {
			return err
		}
	}
	if _, err := fmt.Fprintf(w, "data: %s\n\n", payload); err != nil {
		return err
	}
	return nil
}

// ServeHTTP allows Broadcaster to satisfy http.Handler.
func (b *Broadcaster) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	b.Handler(w, r)
}
