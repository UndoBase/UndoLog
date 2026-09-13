//! OpenTelemetry tracing and log export setup.
//!
//! Configures the OTLP exporter for traces and initializes a `tracing`
//! subscriber that bridges OpenTelemetry spans into the existing `tracing`
//! structured logging pipeline.
//!
//! The telemetry pipeline is optional: when `UNDOLOG_OTEL_ENDPOINT` is
//! unset or empty, the engine falls back to JSON log output only.

use std::env;
use std::time::Duration;

use opentelemetry::trace::TracerProvider as _;
use opentelemetry::{global, KeyValue};
use opentelemetry_otlp::{SpanExporter, WithExportConfig};
use opentelemetry_sdk::trace::SdkTracerProvider;
use opentelemetry_sdk::Resource;
use opentelemetry_semantic_conventions::resource as semconv;
use tracing_opentelemetry::OpenTelemetryLayer;
use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::EnvFilter;

/// Service name used in OTLP resource attributes.
const SERVICE_NAME: &str = "undolog-engine";

/// Environment variable that enables OTLP export when set.
const ENV_OTEL_ENDPOINT: &str = "UNDOLOG_OTEL_ENDPOINT";

/// Environment variable to override the service name (optional).
const ENV_OTEL_SERVICE_NAME: &str = "UNDOLOG_OTEL_SERVICE_NAME";

/// Environment variable to set a custom log level for the OpenTelemetry
/// pipeline. When unset, the default `UNDOLOG_LOG_LEVEL` is used.
const ENV_OTEL_LOG_LEVEL: &str = "UNDOLOG_OTEL_LOG_LEVEL";

/// Holds the tracer provider so it can be flushed on shutdown.
pub struct TracingGuard {
    provider: Option<SdkTracerProvider>,
}

impl Drop for TracingGuard {
    fn drop(&mut self) {
        if let Some(provider) = self.provider.take() {
            if let Err(e) = provider.shutdown() {
                eprintln!("OpenTelemetry tracer provider shutdown error: {e}");
            }
        }
    }
}

/// Initialize the tracing pipeline with optional OpenTelemetry export.
///
/// Returns a [`TracingGuard`] that must be held until the process exits so
/// that spans are flushed before shutdown.
///
/// When `UNDOLOG_OTEL_ENDPOINT` is set (e.g. `http://localhost:4317`),
/// traces are exported via OTLP/gRPC. Otherwise only JSON log output is
/// configured.
pub fn init_telemetry() -> TracingGuard {
    let endpoint = env::var(ENV_OTEL_ENDPOINT).ok().filter(|s| !s.is_empty());
    let log_level = env::var(ENV_OTEL_LOG_LEVEL)
        .or_else(|_| env::var("UNDOLOG_LOG_LEVEL"))
        .unwrap_or_else(|_| "info".to_string());

    let env_filter =
        EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new(&log_level));

    match endpoint {
        Some(ep) => {
            let service_name =
                env::var(ENV_OTEL_SERVICE_NAME).unwrap_or_else(|_| SERVICE_NAME.to_string());

            let resource = Resource::builder()
                .with_attribute(KeyValue::new(semconv::SERVICE_NAME, service_name))
                .build();

            let exporter = SpanExporter::builder()
                .with_tonic()
                .with_endpoint(ep)
                .with_timeout(Duration::from_secs(5))
                .build()
                .expect("failed to create OTLP span exporter");

            let provider = SdkTracerProvider::builder()
                .with_resource(resource)
                .with_simple_exporter(exporter)
                .build();

            let tracer = provider.tracer(SERVICE_NAME);
            global::set_tracer_provider(provider.clone());

            let subscriber = tracing_subscriber::registry()
                .with(env_filter)
                .with(tracing_subscriber::fmt::layer().json())
                .with(OpenTelemetryLayer::new(tracer));

            tracing::subscriber::set_global_default(subscriber)
                .expect("failed to set global tracing subscriber");

            TracingGuard { provider: Some(provider) }
        }
        None => {
            let subscriber = tracing_subscriber::registry()
                .with(env_filter)
                .with(tracing_subscriber::fmt::layer().json());

            tracing::subscriber::set_global_default(subscriber)
                .expect("failed to set global tracing subscriber");

            TracingGuard { provider: None }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn init_telemetry_without_endpoint_configures_json_logging() {
        std::env::remove_var("UNDOLOG_OTEL_ENDPOINT");
        let _guard = init_telemetry();
    }
}
