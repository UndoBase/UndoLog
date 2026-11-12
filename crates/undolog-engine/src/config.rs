//! TLS configuration for the UndoLog engine gRPC server.
//!
//! Provides parsing, validation, and construction of tonic `ServerTlsConfig`
//! for transport TLS and optional mutual TLS (mTLS) authentication.

use std::path::{Path, PathBuf};
use thiserror::Error;
use tonic::transport::{Certificate, Identity, ServerTlsConfig};

/// Environment variable pointing to the PEM server certificate chain.
pub const ENV_TLS_CERT_PATH: &str = "UNDOLOG_ENGINE_TLS_CERT_PATH";

/// Environment variable pointing to the PEM server private key.
pub const ENV_TLS_KEY_PATH: &str = "UNDOLOG_ENGINE_TLS_KEY_PATH";

/// Environment variable pointing to the PEM client CA bundle for mTLS.
pub const ENV_TLS_CLIENT_CA_PATH: &str = "UNDOLOG_ENGINE_TLS_CLIENT_CA_PATH";

/// PEM classification for startup validation.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum PemKind {
    Certificate,
    PrivateKey,
    CaBundle,
}

/// Errors arising from engine TLS configuration and credential loading.
#[derive(Debug, Error)]
pub enum TlsConfigError {
    #[error("incomplete TLS configuration: {0} was provided, but {1} is missing")]
    IncompletePair(&'static str, &'static str),

    #[error(
        "invalid TLS configuration: {ENV_TLS_CLIENT_CA_PATH} requires both {ENV_TLS_CERT_PATH} and {ENV_TLS_KEY_PATH} to be configured"
    )]
    ClientCaWithoutServerTls,

    #[error("failed to read {setting} from {path:?}: {source}")]
    IoError {
        setting: &'static str,
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },

    #[error("invalid PEM format in {setting} ({path:?}): {reason}")]
    InvalidPem { setting: &'static str, path: PathBuf, reason: &'static str },
}

/// Validated TLS filesystem paths.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TlsConfig {
    pub cert_path: PathBuf,
    pub key_path: PathBuf,
    pub client_ca_path: Option<PathBuf>,
}

impl TlsConfig {
    /// Load and validate TLS configuration from environment variables.
    ///
    /// Returns `Ok(None)` if no TLS configuration is present (plaintext default).
    pub fn from_env() -> Result<Option<Self>, TlsConfigError> {
        Self::from_env_lookup(&mut |key| std::env::var(key).ok())
    }

    /// Load and validate TLS configuration with an arbitrary lookup function.
    pub fn from_env_lookup<F>(lookup: &mut F) -> Result<Option<Self>, TlsConfigError>
    where
        F: FnMut(&str) -> Option<String>,
    {
        let cert_path = lookup(ENV_TLS_CERT_PATH)
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .map(PathBuf::from);
        let key_path = lookup(ENV_TLS_KEY_PATH)
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .map(PathBuf::from);
        let client_ca_path = lookup(ENV_TLS_CLIENT_CA_PATH)
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .map(PathBuf::from);

        match (cert_path, key_path, client_ca_path) {
            (None, None, None) => Ok(None),
            (Some(_), None, _) => {
                Err(TlsConfigError::IncompletePair(ENV_TLS_CERT_PATH, ENV_TLS_KEY_PATH))
            }
            (None, Some(_), _) => {
                Err(TlsConfigError::IncompletePair(ENV_TLS_KEY_PATH, ENV_TLS_CERT_PATH))
            }
            (None, None, Some(_)) => Err(TlsConfigError::ClientCaWithoutServerTls),
            (Some(cert), Some(key), client_ca) => {
                Ok(Some(Self { cert_path: cert, key_path: key, client_ca_path: client_ca }))
            }
        }
    }

    /// Reads PEM credentials from disk, validates formatting, and builds `ServerTlsConfig`.
    pub fn build_server_tls_config(&self) -> Result<ServerTlsConfig, TlsConfigError> {
        let cert_bytes =
            validate_and_read_pem(&self.cert_path, ENV_TLS_CERT_PATH, PemKind::Certificate)?;
        let key_bytes =
            validate_and_read_pem(&self.key_path, ENV_TLS_KEY_PATH, PemKind::PrivateKey)?;

        let identity = Identity::from_pem(cert_bytes, key_bytes);
        let mut tls = ServerTlsConfig::new().identity(identity);

        if let Some(ca_path) = &self.client_ca_path {
            let ca_bytes =
                validate_and_read_pem(ca_path, ENV_TLS_CLIENT_CA_PATH, PemKind::CaBundle)?;
            let ca_cert = Certificate::from_pem(ca_bytes);
            tls = tls.client_ca_root(ca_cert);
        }

        Ok(tls)
    }

    /// Whether mutual TLS (client certificate verification) is enabled.
    pub fn is_mtls(&self) -> bool {
        self.client_ca_path.is_some()
    }
}

/// Reads a PEM file, validates boundary headers and footers, and returns bytes.
fn validate_and_read_pem(
    path: &Path,
    setting: &'static str,
    kind: PemKind,
) -> Result<Vec<u8>, TlsConfigError> {
    let bytes = std::fs::read(path).map_err(|source| TlsConfigError::IoError {
        setting,
        path: path.to_path_buf(),
        source,
    })?;

    if bytes.is_empty() {
        return Err(TlsConfigError::InvalidPem {
            setting,
            path: path.to_path_buf(),
            reason: "file is empty",
        });
    }

    let text = match std::str::from_utf8(&bytes) {
        Ok(t) => t,
        Err(_) => {
            return Err(TlsConfigError::InvalidPem {
                setting,
                path: path.to_path_buf(),
                reason: "file is not valid UTF-8 text",
            })
        }
    };

    if !text.contains("-----BEGIN ") {
        return Err(TlsConfigError::InvalidPem {
            setting,
            path: path.to_path_buf(),
            reason: "missing PEM header delimiter",
        });
    }

    if !text.contains("-----END ") {
        return Err(TlsConfigError::InvalidPem {
            setting,
            path: path.to_path_buf(),
            reason: "missing PEM footer delimiter",
        });
    }

    match kind {
        PemKind::Certificate | PemKind::CaBundle => {
            if !text.contains("CERTIFICATE") {
                return Err(TlsConfigError::InvalidPem {
                    setting,
                    path: path.to_path_buf(),
                    reason: "missing CERTIFICATE block",
                });
            }
        }
        PemKind::PrivateKey => {
            if !text.contains("KEY") {
                return Err(TlsConfigError::InvalidPem {
                    setting,
                    path: path.to_path_buf(),
                    reason: "missing private KEY block",
                });
            }
        }
    }

    Ok(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn make_lookup(map: HashMap<&'static str, String>) -> impl FnMut(&str) -> Option<String> {
        move |key: &str| map.get(key).cloned()
    }

    #[test]
    fn test_plaintext_when_unset() {
        let mut lookup = make_lookup(HashMap::new());
        let config = TlsConfig::from_env_lookup(&mut lookup).expect("must parse successfully");
        assert!(config.is_none());
    }

    #[test]
    fn test_incomplete_pair_missing_key() {
        let mut map = HashMap::new();
        map.insert(ENV_TLS_CERT_PATH, "/path/to/cert.crt".to_string());
        let mut lookup = make_lookup(map);

        let err = TlsConfig::from_env_lookup(&mut lookup).expect_err("must fail");
        match err {
            TlsConfigError::IncompletePair(provided, missing) => {
                assert_eq!(provided, ENV_TLS_CERT_PATH);
                assert_eq!(missing, ENV_TLS_KEY_PATH);
            }
            other => panic!("unexpected error: {other:?}"),
        }
    }

    #[test]
    fn test_incomplete_pair_missing_cert() {
        let mut map = HashMap::new();
        map.insert(ENV_TLS_KEY_PATH, "/path/to/key.key".to_string());
        let mut lookup = make_lookup(map);

        let err = TlsConfig::from_env_lookup(&mut lookup).expect_err("must fail");
        match err {
            TlsConfigError::IncompletePair(provided, missing) => {
                assert_eq!(provided, ENV_TLS_KEY_PATH);
                assert_eq!(missing, ENV_TLS_CERT_PATH);
            }
            other => panic!("unexpected error: {other:?}"),
        }
    }

    #[test]
    fn test_client_ca_without_server_tls() {
        let mut map = HashMap::new();
        map.insert(ENV_TLS_CLIENT_CA_PATH, "/path/to/ca.crt".to_string());
        let mut lookup = make_lookup(map);

        let err = TlsConfig::from_env_lookup(&mut lookup).expect_err("must fail");
        assert!(matches!(err, TlsConfigError::ClientCaWithoutServerTls));
    }

    #[test]
    fn test_valid_tls_server_only() {
        let mut map = HashMap::new();
        map.insert(ENV_TLS_CERT_PATH, "/path/to/cert.crt".to_string());
        map.insert(ENV_TLS_KEY_PATH, "/path/to/key.key".to_string());
        let mut lookup = make_lookup(map);

        let config =
            TlsConfig::from_env_lookup(&mut lookup).expect("must parse").expect("must be Some");
        assert_eq!(config.cert_path, PathBuf::from("/path/to/cert.crt"));
        assert_eq!(config.key_path, PathBuf::from("/path/to/key.key"));
        assert_eq!(config.client_ca_path, None);
        assert!(!config.is_mtls());
    }

    #[test]
    fn test_valid_mtls() {
        let mut map = HashMap::new();
        map.insert(ENV_TLS_CERT_PATH, "/path/to/cert.crt".to_string());
        map.insert(ENV_TLS_KEY_PATH, "/path/to/key.key".to_string());
        map.insert(ENV_TLS_CLIENT_CA_PATH, "/path/to/ca.crt".to_string());
        let mut lookup = make_lookup(map);

        let config =
            TlsConfig::from_env_lookup(&mut lookup).expect("must parse").expect("must be Some");
        assert_eq!(config.cert_path, PathBuf::from("/path/to/cert.crt"));
        assert_eq!(config.key_path, PathBuf::from("/path/to/key.key"));
        assert_eq!(config.client_ca_path, Some(PathBuf::from("/path/to/ca.crt")));
        assert!(config.is_mtls());
    }
}
