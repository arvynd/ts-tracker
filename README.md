# Forecasting Benchmark Project Plan

11 Oct 2026 · @Aravind

## Goal and scope

Working name: `trs-tracker`.

Build a public, reproducible benchmark that answers one question: on demand-like data no model has seen, do time-series foundation models beat N-BEATS/N-HiTS and a tuned LightGBM, and are their intervals honest? The project runs open-ended, phase by phase, and each phase ends with something publishable.

- **Core comparison:** statistical baselines, LightGBM, N-BEATS, N-HiTS, and 3-4 current foundation models zero-shot.
- **Uncertainty:** conformal prediction to get intervals with verified coverage.
- **Coherence:** hierarchical reconciliation so market, district, state and national forecasts add up.
- **Deployment:** a scheduled pipeline and API that keeps forecasting new data and scores itself.
- **Differentiator:** a live, leakage-free leaderboard. Most public benchmarks reuse datasets the models were pretrained on; this one scores forecasts against data that did not exist when the models were released.

## Data and leakage-safe evaluation

Start with Agmarknet mandi arrivals, then repeat the study on VN1. The rule that makes results credible: **every test window starts after the release date of the newest model in the comparison.**

### Primary dataset: Agmarknet arrivals

- The Agmarknet dataset gives daily prices and arrivals for 300+ commodities across thousands of Indian wholesale markets, updated daily.
- Target: arrival quantity (tonnes). It behaves like demand data: seasonal, festival-driven, intermittent at small markets.
- Hierarchy: market → district → state → India, crossed with commodity. CEDA's version maps markets to Census 2011 districts, which saves building that mapping.
- Starting scope: 3 commodities (onion, tomato, potato) across 3-5 states, aggregated to weekly. Expand only after the pipeline works end to end.

### Second dataset: VN1

- The VN1 competition (Aug 2024) has weekly sales by client, warehouse and product, plus price and inventory.
- It is static, so check each model's pretraining corpus before claiming zero-shot results on it.

### Evaluation protocol

1. Keep a model registry with each model's release date and pretraining corpus.
2. Rolling-origin backtest: 4-6 cutoffs, all after the newest release date; horizons of 1, 4 and 8 weeks.
3. Point metrics: MASE and RMSSE; bias as mean scaled error.
4. Probabilistic metrics: scaled CRPS / weighted quantile loss, interval coverage and width.
5. Live track: once deployed, every forecast is stored and scored when actuals arrive. This becomes the leaderboard.

## Phase 0: Repo and skeleton

A public repo that installs in one command and runs a dummy forecast end to end.

- `uv` for the environment, `ruff` + `pytest`, a Makefile with targets like `make data`, `make bench`, `make deploy-local`.
- Layout: `ingest/`, `features/`, `models/`, `conformal/`, `reconcile/`, `serve/`, `infra/`.
- Storage as Parquet, queried with DuckDB. One config file for scope (commodities, states, horizons, cutoffs).
- CI on GitHub Actions: lint, tests, and a tiny smoke benchmark on a sample.

**Done when:** `make bench` runs seasonal-naive on a 100-row sample and writes a results table.

## Phase 1: Data pipeline and hierarchy

A clean, weekly, hierarchical arrivals panel that can be refreshed with one command.

- **Ingest:** start from CEDA bulk downloads for history; use the Agmarknet portal or data.gov.in API for incremental updates. Keep raw snapshots immutable and dated.
- **Cleaning:** standardise units to tonnes, harmonise market and commodity names, flag outliers (unit-entry errors are common).
- **Missing vs zero:** a market that did not report is not a market with zero arrivals. Model this explicitly; it affects every metric downstream.
- Aggregate to weekly; keep daily for a later stretch goal.
- Build the summing matrix for market → district → state → India per commodity, plus commodity totals.
- Covariates (optional, later): festival calendar, monsoon onset, wholesale price.
- EDA notebook: intermittency share per level, seasonality, series that start or stop mid-history.

**Done when:** a dated Parquet panel and summing matrix exist, and a data-quality report lists coverage and gaps per state.

## Phase 2: Benchmarking harness

One harness, one backtest split, every model behind the same `fit` / `predict_quantiles` interface.

| Family | Models | Library | Where it runs |
| --- | --- | --- | --- |
| Baselines | Seasonal naive, AutoETS, AutoTheta | statsforecast | CPU |
| Gradient boosting | LightGBM, point + quantile objectives | mlforecast | CPU |
| Deep learning | N-BEATS, N-HiTS | neuralforecast | RTX 4070 |
| Foundation models, zero-shot | 3-4 picks from the GIFT-Eval leaderboard, e.g. Chronos, TimesFM, Moirai, Granite PatchTST-FM-r2 | Hugging Face / vendor packages | RTX 4070 |

- Pick foundation models by the leaderboard's **strictly zero-shot** column, not the pretrained one.
- Optional stretch: fine-tune the best foundation model and report it separately from zero-shot.
- Log every run (model, version, release date, cutoff, metrics, runtime, cost) to a results table; MLflow if you want the UI.
- Slice results by hierarchy level, intermittency and horizon. "Foundation models win at the state level but lose on sparse markets" is a more useful finding than one overall rank.

**Done when:** a results table and one summary chart compare all models across levels and horizons.

## Phase 3: Conformal prediction

Wrap every model's output so 80% and 95% intervals actually cover 80% and 95% of outcomes, then show which models needed the most correction.

- Split conformal on residuals from a calibration window just before each cutoff, as the simple baseline.
- Conformalized quantile regression on top of models that already output quantiles (LightGBM quantile, foundation models).
- Time-series-aware methods, since residuals are not exchangeable: EnbPI and adaptive conformal inference (ACI).
- Tooling: statsforecast and neuralforecast have built-in conformal intervals; MAPIE for the rest.
- Report coverage and width per model, per hierarchy level, and over time. Coverage drifting during monsoon or festival weeks is a finding worth showing.
- **Key comparison:** raw foundation-model quantiles vs conformalized. If zero-shot quantiles are already well calibrated on unseen data, that is a headline.

**Done when:** a coverage-vs-width plot for every model and method, at both nominal levels.

## Phase 4: Hierarchical reconciliation

Make forecasts coherent across market, district, state and India, and measure whether coherence helps or hurts accuracy at each level.

- Methods via Nixtla's `hierarchicalforecast`: BottomUp, TopDown, MinTrace (OLS, WLS-struct, MinT-shrink).
- Probabilistic reconciliation: bootstrap and PERMBU, compared against Gaussian.
- **The interesting experiment:** conformalize then reconcile vs reconcile then conformalize. Reconciling can break calibrated coverage; few public write-ups test this order on real data.
- **Mixed base forecasts:** foundation model at aggregate levels, LightGBM at sparse markets, then reconcile. Tests whether the best model per level plus reconciliation beats any single model.
- Check: incoherence before and after, accuracy change per level, coverage change per level.

**Done when:** a table of accuracy and coverage per level for each reconciliation method and order.

## Phase 5: Deployment

Ship one container image that runs as scheduled jobs plus an API. Cloud Run is the v1 target and Lambda the alternative; develop locally against Floci.

The scoring job is what turns a deployment into a benchmark: forecasts made today are scored when actuals arrive weeks later.

- **Container-first:** one Docker image with the FastAPI app and the job entrypoints. Run it as a Cloud Run service plus Cloud Run Jobs, or on Lambda via the AWS Lambda Web Adapter.
- Storage behind a small interface (local disk, S3 or GCS) so the cloud choice stays swappable.
- **Local development:** docker compose plus Floci on `localhost:4566` for S3, Lambda, EventBridge and API Gateway. Floci runs Lambda in real Docker containers, but it is not full AWS parity, so verify on the real cloud before calling a phase done.
- Serve only CPU-friendly models (LightGBM, a small foundation model). Heavier models stay in the offline benchmark on your GPU.
- Infrastructure as code with Terraform or OpenTofu, both supported by Floci.
- Endpoints: `/forecast` (by commodity, level and node), `/leaderboard`, `/health`.
- Monitoring: job logs, a data-freshness check, and an alert when rolling interval coverage drifts.

**Done when:** the weekly schedule runs unattended for 4 weeks and the leaderboard updates on its own.

## Phase 6: VN1 extension

Rerun Phases 2-4 on VN1 to test whether the Agmarknet findings hold on retail sales.

- Adapter that maps VN1 to the same panel format and builds its client → warehouse → product summing matrix.
- Use price and inventory as covariates for LightGBM and covariate-aware models; note that stock-outs censor sales, the same uncensoring problem as in device demand.
- **Leakage audit first:** check each foundation model's stated pretraining sources for VN1. Report any overlap next to the results.

**Done when:** one comparison table across both datasets, with a short section on what transferred and what did not.

## Phase 7: Portfolio write-up

Publish findings in a form a hiring manager can scan in two minutes and a practitioner can reproduce in an afternoon.

- **README:** the question, the headline result, one chart, and make commands to reproduce.
- **Long-form post:** leakage-safe protocol, where foundation models win and lose, conformal coverage, reconciliation order.
- **Live leaderboard page** fed by the deployed scoring job.
- **Model cards** for each model: version, release date, licence, pretraining notes.
- Don't wait for the end: a short post after Phase 2 and Phase 3 each keeps momentum and gets early feedback.

**Done when:** repo, post and leaderboard are public and linked from each other.

## Risks and open questions

| Risk | Mitigation |
| --- | --- |
| Agmarknet portal changes format or blocks scraping | CEDA bulk files for history; data.gov.in API as backup; keep raw snapshots |
| Reporting gaps mistaken for zero demand | Explicit missing-vs-zero flag from Phase 1; report metrics with and without sparse markets |
| Foundation models too large for serverless | Benchmark locally on GPU; serve only CPU-friendly models; batch jobs on Cloud Run Jobs if needed |
| Model licences block public results or serving | Check each licence before adding a model to the registry |
| Scope creep across two datasets and seven phases | Each phase has a done-when; publish after each before starting the next |

### Open questions

- [ ] Cloud Run or Lambda as the v1 target? The plan keeps both open until Phase 5.
- [ ] Which 3 commodities and which states for the first slice?
- [ ] Weekly only, or daily as a stretch track?
- [ ] Is floci-gcp mature enough to emulate the Cloud Run path locally, or only the AWS path via Floci?
