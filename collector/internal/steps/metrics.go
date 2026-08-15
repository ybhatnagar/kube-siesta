package steps

import (
	"context"
	"errors"

	"github.com/kube-siesta/collector/internal/connectors"
)

// MetricsStep pulls metric samples via the selected MetricsConnector and upserts
// them (plus the workload identities) to the state store.
type MetricsStep struct{}

func (MetricsStep) Name() string { return "metrics" }

func (MetricsStep) Run(ctx context.Context, rt Runtime) (Result, error) {
	mc, err := connectors.Metrics(rt.Source)
	if err != nil {
		return Result{}, err
	}
	res, err := mc.FetchMetrics(ctx, rt.Window, rt.Cfg)
	if err != nil {
		return Result{}, err
	}
	if err := rt.Store.UpsertWorkloads(ctx, res.Workloads); err != nil {
		return Result{}, err
	}
	n, err := rt.Store.UpsertMetricSamples(ctx, res.Samples)
	if err != nil {
		return Result{}, err
	}
	return Result{RowsWritten: n, Workloads: len(res.Workloads)}, nil
}

// interactionsStep is a placeholder so `--interactions` gives a clear message.
// The InteractionConnector path lands in a later milestone.
type interactionsStep struct{}

func (interactionsStep) Name() string { return "interactions" }

func (interactionsStep) Run(ctx context.Context, rt Runtime) (Result, error) {
	return Result{}, errors.New("interactions step is not implemented in milestone 1")
}
