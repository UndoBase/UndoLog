---
title: "Helm Installation"
description: "Install and customize UndoLog on Kubernetes using the Helm chart."
section: "guides"
---
# Helm Installation

Deploy UndoLog on Kubernetes using the Helm chart.

---

## Prerequisites

- Kubernetes 1.25+
- Helm 3.10+
- A PostgreSQL 16 instance accessible from the cluster

---

## Quick start

```bash
# Add the chart (local)
helm install undolog deploy/helm/undolog \
  --set database.url=postgresql://user:pass@postgres-host:5432/undolog \
  --set apiKeys=sk-prod=org-acme
```

---

## Configuration

### Required values

| Value | Description |
|-------|-------------|
| `database.url` | PostgreSQL connection string |
| `apiKeys` | Comma-separated `key=org_id` pairs for proxy authentication |

### Default values

The chart ships with sensible defaults. Override as needed:

```yaml
# values-production.yaml
engine:
  replicas: 2
  resources:
    requests:
      cpu: 500m
      memory: 512Mi
    limits:
      cpu: "2"
      memory: 1Gi

proxy:
  replicas: 2
  resources:
    requests:
      cpu: 250m
      memory: 128Mi
    limits:
      cpu: "1"
      memory: 512Mi

pdb:
  enabled: true
  minAvailable: 1

hpa:
  enabled: true
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70
```

Install with custom values:

```bash
helm install undolog deploy/helm/undolog \
  -f values-production.yaml \
  --set database.url=postgresql://user:pass@host/db \
  --set apiKeys=sk-prod=org-acme
```

---

## Components

The chart deploys:

| Component | Description | Ports |
|-----------|-------------|-------|
| Engine | Rust gRPC effect engine | 50051 (gRPC), 9090 (health) |
| Proxy | Go MCP interceptor | 8080 (HTTP) |

### Engine

- Liveness and readiness probes on port 9090 (`GET /`)
- Advisory lock tuning via environment variables
- Registry refresh from database

### Proxy

- Liveness and readiness probes on port 8080 (`GET /health`)
- Engine connection created lazily and reconnects automatically
- Configurable timeouts and retry policy

---

## Networking

### ClusterIP services

Both engine and proxy use `ClusterIP` services by default. The proxy connects to the engine via the internal service DNS:

```
<release>-undolog-engine:50051
```

### Ingress

Enable the Ingress resource to expose the proxy externally:

```yaml
ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
  hosts:
    - host: undolog.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: undolog-tls
      hosts:
        - undolog.example.com
```

---

## High availability

### Pod disruption budget

Enabled by default. Ensures at least 1 proxy pod remains available during voluntary disruptions:

```yaml
pdb:
  enabled: true
  minAvailable: 1
```

### Horizontal pod autoscaler

Disabled by default. Enable for auto-scaling based on CPU/memory:

```yaml
hpa:
  enabled: true
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70
  targetMemoryUtilizationPercentage: 80
```

---

## Customization

### Environment variables

Override engine or proxy environment variables:

```yaml
engine:
  env:
    UNDOLOG_LOG_LEVEL: debug
    UNDOLOG_LOCK_MAX_ATTEMPTS: "5"
    UNDOLOG_REGISTRY_REFRESH_SECS: "120"

proxy:
  env:
    UNDOLOG_PROXY_READ_TIMEOUT_SECS: "20"
    UNDOLOG_PROXY_REQUEST_TIMEOUT_SECS: "60"
```

### Resource limits

```yaml
engine:
  resources:
    requests:
      cpu: 250m
      memory: 256Mi
    limits:
      cpu: "1"
      memory: 512Mi

proxy:
  resources:
    requests:
      cpu: 100m
      memory: 64Mi
    limits:
      cpu: 500m
      memory: 256Mi
```

### Node selection

```yaml
engine:
  nodeSelector:
    node-type: compute
  tolerations:
    - key: "compute"
      operator: "Equal"
      value: "true"
      effect: "NoSchedule"
```

---

## Verify the installation

```bash
# Check pods
kubectl get pods -l app.kubernetes.io/name=undolog

# Check services
kubectl get svc -l app.kubernetes.io/name=undolog

# Port-forward to test locally
kubectl port-forward svc/<release>-undolog-proxy 8080:8080

# Test health
curl -s http://localhost:8080/health
```

---

## Uninstall

```bash
helm uninstall undolog
```

Persistent volumes (PostgreSQL data) are not removed by `helm uninstall`. Delete them manually if needed:

```bash
kubectl delete pvc -l app.kubernetes.io/name=undolog
```

---

## See also

- [Running in production](running-in-production.md): production deployment checklist
- [PostgreSQL high availability](postgresql-ha.md): WAL streaming, read replicas, failover
- [Configuration reference](../reference/configuration.md): all environment variables
