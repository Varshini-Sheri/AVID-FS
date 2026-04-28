"""
server/metrics.py

Prometheus metrics for VerdictFS server nodes.
Import `registry` and pass to FastAPI's /metrics endpoint.
"""

from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry, REGISTRY

# Use the default registry so prometheus-client's built-in metrics are included.
registry = REGISTRY

dispersals_total = Counter(
    "verdictfs_dispersals_total",
    "Total number of disperse requests received",
    ["status"],   # labels: success | failed | invalid
)

echo_messages_total = Counter(
    "verdictfs_echo_messages_total",
    "Total echo messages received",
    ["from_server"],
)

ready_messages_total = Counter(
    "verdictfs_ready_messages_total",
    "Total ready messages received",
    ["from_server"],
)

stored_objects_total = Counter(
    "verdictfs_stored_objects_total",
    "Total objects durably stored on this server",
)

concurrent_dispersals = Gauge(
    "verdictfs_concurrent_dispersals",
    "Number of dispersal operations currently in progress",
)

verify_duration_seconds = Histogram(
    "verdictfs_verify_duration_seconds",
    "Time to verify a fragment against its fpcc",
    buckets=[0.0001, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5],
)

echo_broadcast_duration_seconds = Histogram(
    "verdictfs_echo_broadcast_duration_seconds",
    "Time to broadcast echo messages to all peers",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0],
)

retrieve_duration_seconds = Histogram(
    "verdictfs_retrieve_duration_seconds",
    "Time to serve a retrieve request",
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1],
)
