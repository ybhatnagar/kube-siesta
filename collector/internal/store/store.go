// Package store is the collector's StateStore repository. It writes the state-DB
// tiers 2–3; the engine reads the same DB independently. The DB
// schema is the only cross-module contract — the engine never calls the collector.
package store

import (
	"context"
	"time"
)

// Resource kinds recognized by metric_samples.resource.
const (
	ResourceCPU              = "cpu"
	ResourceMemory           = "memory"
	ResourceNetTx            = "net_tx"
	ResourceNetRx            = "net_rx"
	ResourceEphemeralStorage = "ephemeral_storage"
)

// collection_runs.status values.
const (
	StatusPending = "pending"
	StatusRunning = "running"
	StatusSuccess = "success"
	StatusFailed  = "failed"
	StatusPartial = "partial"
)

// MetricSample is one normalized time-series point (tier 3, metric_samples).
// Monotonic counters are converted to rate-of-change by the connector (IsRate=true).
type MetricSample struct {
	ClusterID   int64
	WorkloadUID string
	Resource    string
	TS          time.Time
	Value       float64
	Unit        string
	IsRate      bool
	CollectedAt time.Time
}

// WorkloadIdentity resolves a workload_uid -> (namespace, kind, name, requests) so
// the engine can label recommendation cards and compute cost. Written to
// disc_workloads (tier 2). Nil request/replica fields are stored as NULL.
type WorkloadIdentity struct {
	ClusterID        int64
	WorkloadUID      string
	Namespace        string
	Kind             string
	Name             string
	Replicas         *int64
	RequestsCPUm     *int64
	RequestsMemBytes *int64
	Labels           map[string]string
	FetchedAt        time.Time
}

// Interaction is one dependency-graph edge (tier 3, interactions). Populated by an
// InteractionConnector in a later milestone; the type is defined now so the
// interface and schema stay aligned.
type Interaction struct {
	ClusterID      int64
	SrcWorkloadUID string
	DstWorkloadUID string
	AvgCount       float64
	WindowStart    time.Time
	WindowEnd      time.Time
	CollectedAt    time.Time
}

// CollectionRun is bookkeeping for one collection (tier 3, collection_runs). The
// UI polls its status; the engine may reference it from analysis_runs.
type CollectionRun struct {
	ID          int64
	ClusterID   int64
	Scope       string // JSON
	Resources   string // JSON
	WindowStart time.Time
	WindowEnd   time.Time
	SourcesUsed string // JSON
	Status      string
	RowsWritten int
	DataAsOf    *time.Time
	StartedAt   *time.Time
	FinishedAt  *time.Time
	Error       string
}

// StateStore is the repository interface the collector writes through. Backed by
// Postgres in production and SQLite in dev (see sql.go). Keeping reads/writes
// behind this interface lets the backing DB swap without touching connectors/steps.
type StateStore interface {
	// Migrate applies pending schema migrations (idempotent).
	Migrate(ctx context.Context) error
	// Ping verifies connectivity.
	Ping(ctx context.Context) error

	// EnsureCluster upserts a cluster by name and returns its id (single-cluster
	// start; the schema already carries cluster for later multi-cluster fan-out).
	EnsureCluster(ctx context.Context, name string) (int64, error)

	// UpsertWorkloads writes workload identities (tier 2, disc_workloads),
	// idempotent on (cluster_id, workload_uid).
	UpsertWorkloads(ctx context.Context, ws []WorkloadIdentity) error
	// UpsertMetricSamples writes samples (tier 3, metric_samples), idempotent on
	// (cluster_id, workload_uid, resource, ts); returns the number of rows written.
	UpsertMetricSamples(ctx context.Context, samples []MetricSample) (int, error)

	CreateCollectionRun(ctx context.Context, r *CollectionRun) (int64, error)
	UpdateCollectionRun(ctx context.Context, r *CollectionRun) error

	Close() error
}
