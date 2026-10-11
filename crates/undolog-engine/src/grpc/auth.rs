//! Optional bearer-token authentication for the engine's gRPC endpoint.
//!
//! Enforcement is driven by a shared token supplied at startup. With no
//! token configured every request passes through untouched, which keeps
//! local development and the compose stack working unchanged. With a
//! token configured every RPC must carry it in the `authorization`
//! metadata key as `Bearer <token>`; anything else is answered with
//! `UNAUTHENTICATED` before the handler runs, naming the expected
//! credential without echoing any secret.

use tonic::service::interceptor::InterceptedService;
use tonic::service::Interceptor;
use tonic::{Request, Status};

/// Metadata key carrying the bearer token. gRPC metadata keys are
/// lowercase ASCII, so the header name needs no case handling.
const AUTHORIZATION_KEY: &str = "authorization";

/// Prefix the presented credential must carry. The scheme is compared
/// literally, so a caller must send exactly this form.
const BEARER_PREFIX: &str = "Bearer ";

/// Rejection message: names the expected credential and never echoes a
/// token, configured or presented.
const CREDENTIALS_EXPECTED: &str =
    "missing or invalid credentials: expected `authorization` metadata as `Bearer <token>`";

/// Normalize a raw configured token into the enforcing form.
///
/// A missing, empty, or whitespace-only value maps to `None`, which
/// leaves enforcement off.
pub fn enforcing_token(raw: Option<String>) -> Option<String> {
    raw.map(|token| token.trim().to_string()).filter(|token| !token.is_empty())
}

/// Interceptor checking the shared token on every RPC of the service it
/// wraps.
#[derive(Clone, Debug)]
pub struct TokenInterceptor {
    expected: Option<String>,
}

impl TokenInterceptor {
    /// Build an interceptor for the configured token; `None` disables
    /// enforcement.
    pub fn new(token: Option<String>) -> Self {
        Self { expected: enforcing_token(token) }
    }
}

impl Interceptor for TokenInterceptor {
    fn call(&mut self, request: Request<()>) -> Result<Request<()>, Status> {
        match self.expected.as_deref() {
            None => Ok(request),
            Some(expected) => match presented_token(&request) {
                Some(presented) if constant_time_eq(presented.as_bytes(), expected.as_bytes()) => {
                    Ok(request)
                }
                _ => Err(Status::unauthenticated(CREDENTIALS_EXPECTED)),
            },
        }
    }
}

/// Wrap a gRPC service so every RPC passes the token interceptor first.
///
/// The server binary and the authentication tests share this wrapper, so
/// the two assemblies cannot drift apart.
pub fn authenticated<S>(
    inner: S,
    token: Option<String>,
) -> InterceptedService<S, TokenInterceptor> {
    InterceptedService::new(inner, TokenInterceptor::new(token))
}

/// Extract the presented bearer token from the request metadata.
///
/// Returns `None` when the key is absent, the value is not valid ASCII,
/// or the `Bearer ` prefix is missing.
fn presented_token(request: &Request<()>) -> Option<&str> {
    let value = request.metadata().get(AUTHORIZATION_KEY)?;
    value.to_str().ok()?.strip_prefix(BEARER_PREFIX)
}

/// Compare two byte slices in constant time.
///
/// The loop visits every position of the longer slice and folds each
/// byte difference, together with the length difference itself, into
/// one accumulator that is zero only for identical slices, so the
/// result cannot be discovered earlier by timing where a mismatch lies.
fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    let mut diff = a.len() ^ b.len();
    for i in 0..a.len().max(b.len()) {
        let x = a.get(i).copied().unwrap_or(0);
        let y = b.get(i).copied().unwrap_or(0);
        diff |= usize::from(x ^ y);
    }
    diff == 0
}

// ── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use tonic::Code;

    use super::*;

    fn request_with(header: &str) -> Request<()> {
        let mut request = Request::new(());
        request
            .metadata_mut()
            .insert(AUTHORIZATION_KEY, header.parse().expect("header must be valid metadata"));
        request
    }

    #[test]
    fn constant_time_eq_accepts_identical_slices() {
        assert!(constant_time_eq(b"secret-token", b"secret-token"));
    }

    #[test]
    fn constant_time_eq_accepts_two_empty_slices() {
        assert!(constant_time_eq(b"", b""));
    }

    #[test]
    fn constant_time_eq_rejects_a_last_byte_difference() {
        assert!(!constant_time_eq(b"secret-token", b"secret-tokez"));
    }

    #[test]
    fn constant_time_eq_rejects_a_length_difference() {
        assert!(!constant_time_eq(b"secret", b"secret-token"));
        assert!(!constant_time_eq(b"secret-token", b"secret"));
    }

    #[test]
    fn presented_token_extracts_the_bearer_value() {
        assert_eq!(presented_token(&request_with("Bearer secret")), Some("secret"));
    }

    #[test]
    fn presented_token_requires_the_bearer_prefix() {
        assert_eq!(presented_token(&request_with("secret")), None);
        assert_eq!(presented_token(&request_with("Basic secret")), None);
        assert_eq!(presented_token(&request_with("Bearer ")), Some(""));
    }

    #[test]
    fn presented_token_is_none_without_the_metadata_key() {
        assert_eq!(presented_token(&Request::new(())), None);
    }

    #[test]
    fn blank_configuration_disables_enforcement() {
        assert_eq!(enforcing_token(None), None);
        assert_eq!(enforcing_token(Some(String::new())), None);
        assert_eq!(enforcing_token(Some("   ".to_string())), None);
        assert_eq!(enforcing_token(Some("  secret  ".to_string())), Some("secret".to_string()));
    }

    #[test]
    fn interceptor_passes_every_request_when_disabled() {
        let mut interceptor = TokenInterceptor::new(None);
        assert!(interceptor.call(Request::new(())).is_ok());
    }

    #[test]
    fn interceptor_accepts_the_matching_token() {
        let mut interceptor = TokenInterceptor::new(Some("secret".to_string()));
        assert!(interceptor.call(request_with("Bearer secret")).is_ok());
    }

    #[test]
    fn interceptor_rejects_a_missing_token_with_a_named_credential() {
        let mut interceptor = TokenInterceptor::new(Some("secret".to_string()));
        let status = interceptor.call(Request::new(())).expect_err("must be rejected");
        assert_eq!(status.code(), Code::Unauthenticated);
        assert!(status.message().contains("authorization"), "must name the header");
        assert!(status.message().contains("Bearer"), "must name the expected form");
        assert!(!status.message().contains("secret"), "must not echo the configured token");
    }

    #[test]
    fn interceptor_rejects_a_wrong_token_without_echoing_it() {
        let mut interceptor = TokenInterceptor::new(Some("secret".to_string()));
        let status =
            interceptor.call(request_with("Bearer hunter2-wrong")).expect_err("must be rejected");
        assert_eq!(status.code(), Code::Unauthenticated);
        assert!(!status.message().contains("hunter2-wrong"), "must not echo the presented token");
        assert!(!status.message().contains("secret"), "must not echo the configured token");
    }
}
