package plugin

import "net/http"

// The header plugin's fixed pair. Deployments that need a different name or
// value write their own plugin, as the configuration reference shows.
const (
	headerName  = "X-Custom-Header"
	headerValue = "undolog"
)

// headerPlugin marks every proxied request with a fixed header. It is the
// built-in worked example for custom auth headers: the upstream tool server
// can require the header, and the proxy adds it without deployment code.
type headerPlugin struct{}

// Name returns the configuration name of the header plugin.
func (headerPlugin) Name() string { return "header" }

// Before adds the header to the outbound request.
func (headerPlugin) Before(r *http.Request) Decision {
	r.Header.Set(headerName, headerValue)
	return Decision{}
}

// After does not modify responses: the header plugin only annotates
// requests, and the interface requires the hook to exist.
func (headerPlugin) After(http.ResponseWriter, *http.Request) {}
