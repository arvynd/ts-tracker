# ts-tracker

> **Work in progress.** Nothing runnable yet beyond a smoke benchmark.

A public, reproducible benchmark asking whether time-series foundation models beat N-BEATS/N-HiTS and a tuned LightGBM on electricity demand data they haven't seen, with real external covariates (weather, calendar), and whether their prediction intervals are honest. Primary dataset: EIA-930 hourly demand by US balancing authority and subregion. Second dataset: VN1 retail sales.

See [PLAN.md](PLAN.md) for the full project plan and phases.

**Status:** Phase 0 (repo and skeleton) done; Phase 1 (data pipeline) next. Try `just setup && just bench`.
