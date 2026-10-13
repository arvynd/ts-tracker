# Forecasting Benchmark Project Plan

11 Oct 2026 · @Aravind · revised 13 Oct 2026 (primary dataset changed from Agmarknet to EIA-930; weekly first track)

## Goal and scope

Working name: `ts-tracker`.

Build a public, reproducible benchmark that answers one question: on electricity demand data no model has seen, with real external covariates, do time-series foundation models beat N-BEATS/N-HiTS and a tuned LightGBM, and are their intervals honest? The project runs open-ended, phase by phase, and each phase ends with something publishable.

- **Core comparison:** statistical baselines, LightGBM, N-BEATS, N-HiTS, and 3-4 current foundation models zero-shot.
- **Covariates:** each model that can use them is run with and without weather and calendar covariates, so the benchmark measures what covariates are worth.
- **Uncertainty:** conformal prediction to get intervals with verified coverage.
- **Coherence:** hierarchical reconciliation so subregion, balancing authority, region and national forecasts add up.
- **Deployment:** a scheduled pipeline and API that keeps forecasting new data and scores itself.
- **Differentiator:** a live, leakage-free leaderboard. Most public benchmarks reuse datasets the models were pretrained on; this one scores forecasts against data that did not exist when the models were released.

## Data and leakage-safe evaluation

Start with EIA-930 hourly electricity demand, then repeat the study on VN1 as a retail replication. The rule that makes results credible: **every test window starts after the release date of the newest model in the comparison.**

### Primary dataset: EIA-930 (US Hourly Electric Grid Monitor)

Why this dataset: it is open, updates daily, and has strong known-future covariates (weather, calendar). The earlier candidate, Agmarknet via CEDA, was dropped as too aggregated, with no documented bulk download and no covariates. NYC TLC and Citi Bike were considered and dropped as overused and likely in pretraining corpora.

- **Source:** Form EIA-930, hourly operating data reported by balancing authorities (BAs) in the Lower 48. History comes from EIA's half-year CSV files (`https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/EIA930_BALANCE_<year>_<Mon>_<Mon>.csv`, and the same with `SUBREGION`). The URL pattern is undocumented, so verify it at ingest time. Incremental updates use the EIA API (free key).
- **Target:** hourly demand in MW at the source. The **first track is weekly**: mean MW per week. Daily and hourly tracks are later extensions, and `freq` is a config parameter. Timestamps are UTC at end of hour; local time is also given.
- **Weekly aggregation (by decision):** weeks run Monday 00:00 to Sunday 24:00 **UTC**, one shared clock for every node, so each week has exactly 168 hours (no daylight-saving effect) and the hierarchy stays exactly additive across time zones. The weekly value is the mean MW over the week (equivalent to the sum up to a constant factor of 168). A week is null unless all 168 hours are present, which also drops partial first and last weeks. Weekly is computed from the hourly panel, not from local-time daily values. Peak demand is not additive and is not a hierarchical target.
- **Checked on a Jan-Jun 2025 sample:**
  - 62 BAs in 13 EIA regions; about 13% of BA-hours have missing demand, and some BAs have no demand at all.
  - Only 8 BAs report subregions: CISO (4), ERCO (8), ISNE (8), MISO (6), NYIS (11), PJM (20), PNM (9), SWPP (17), about 83 subregions in total.
  - Subregions do **not** sum to the published BA demand. CISO is near-exact; MISO, NYIS and SWPP are within 1% almost always but have isolated 20-30 GW gaps; ERCO, PJM and PNM are within 1% only 50-80% of hours; ISNE is poor (99th percentile gap about 21%).
  - 132 null subregion values and 98 non-positive BA demand values.
- **Hierarchy (defined bottom-up, by decision):** do not use the published BA totals. Leaves are subregions for the 8 BAs that report them and the BA itself for the other BAs with usable demand. BA = sum of its subregions, region = sum of its BAs (BA-to-region map from the `Region` column), Lower 48 = sum of regions. The tree has mixed depth and is coherent by construction. This is a known limitation, documented in every result: derived BA series differ from EIA's published BA demand.
- **Covariates:**
  - Weather (temperature, humidity, wind, cloud, precipitation) from Open-Meteo for each BA or subregion centroid, aggregated to the weekly grid (including heating and cooling degree-days). Known-future as forecasts, but only skillful to about 1-2 weeks; beyond that it is climatology (see the leakage rule).
  - Calendar: day of week, US federal and state holidays, daylight-saving transitions. Known-future.
  - EIA's own day-ahead demand forecast at BA level. A reference baseline to beat, not an input.
  - Generation mix per BA (solar, wind, gas, ...). Past-only, so it is used only where a model accepts past covariates.
  - Later, optional: ISO day-ahead prices and fuel prices.
- **Covariate leakage rule:** use the weather forecast that existed at each cutoff, not observed weather. Weather forecast skill fades after 1-2 weeks, so for longer horizons use climatology (for example, the multi-year average for that week), never observed weather, and label those results as calendar and climatology only. Check that Open-Meteo's archived-forecast data supports point-in-time forecasts before building the covariate pipeline; if it does not, say so and restrict covariate results accordingly.
- **Starting scope:** the 8 BAs with subregions, weekly frequency. Add the remaining BAs once the pipeline works end to end.

### Second dataset: VN1

- The VN1 competition (Aug 2024) has weekly sales by client, warehouse and product, plus price and inventory.
- It is static, and price is its only covariate, so it is a retail replication and a contrast to the covariate-rich primary dataset. Check each model's pretraining corpus before claiming zero-shot results on it.

### Evaluation protocol

1. Keep a model registry with each model's release date and pretraining corpus.
2. Rolling-origin backtest: 4-6 cutoffs, all after the newest release date; weekly horizons of 1, 2 and 4 weeks. Weekly seasonality is 52. An 8-week horizon is optional and counts as calendar and climatology only. Daily (horizons 1, 7, 14 days; periods 7 and 365) and hourly day-ahead are later tracks.
3. Point metrics: MASE and RMSSE; bias as mean scaled error.
4. Probabilistic metrics: scaled CRPS / weighted quantile loss, interval coverage and width.
5. Covariate arms: every covariate-capable model is run with no covariates, calendar only, and calendar plus weather. Report the lift per model, per level and per horizon.
6. Reference baseline: EIA's published day-ahead demand forecast, at the BAs where it applies. It is comparable only on a daily or hourly track, not at weekly horizons.
7. Live track: once deployed, every forecast is stored and scored when actuals arrive. This becomes the leaderboard.

## Phase 0: Repo and skeleton (done)

A public repo that installs in one command and runs a dummy forecast end to end.

- `uv` for the environment, `ruff` + `pytest`, a justfile with recipes like `just data`, `just bench`, `just deploy-local`.
- Layout: `ingest/`, `features/`, `models/`, `conformal/`, `reconcile/`, `serve/`, `infra/`.
- Storage as Parquet, queried with Polars. One config file for scope (BAs, regions, horizons, cutoffs).
- CI on GitHub Actions: lint, tests, and a tiny smoke benchmark on a sample.

**Done when:** `just bench` runs seasonal-naive on a 100-row sample and writes a results table.

## Phase 1: Data pipeline and hierarchy

A clean, hierarchical demand panel at hourly, daily and weekly frequency, with covariates, that can be refreshed with one command.

- **Ingest:** download the half-year CSVs for history; use the EIA API for incremental updates. Keep raw snapshots immutable and dated. EIA revises and restates data, so store each snapshot rather than overwriting.
- **Cleaning:** parse UTC and local time, handle DST, flag outliers and non-positive values. The sample shows isolated multi-GW glitch hours in subregions, and they would corrupt derived sums.
- **Missing vs zero:** a missing subregion value is not zero demand. A derived aggregate is null (or imputed with an explicit flag) when any of its leaves is missing. This affects every metric downstream.
- Keep the clean hourly panel. Build a daily panel (local-time days, mean MW, complete days only) and the **weekly panel from the hourly panel** (UTC weeks, mean MW, 168 hours required). Weekly is the first benchmark track; daily and hourly feed later tracks.
- Build the summing matrix from the bottom-up hierarchy above, with a mapping file for subregion to BA to region.
- **Covariates:** weather pipeline (point-in-time forecasts as of each cutoff), holiday calendar, EIA forecast, generation mix.
- EDA notebook: missingness per BA, seasonality, weather sensitivity, agreement between derived and published BA demand, series that start or stop mid-history.

**Done when:** dated Parquet panels (hourly, daily, weekly), a summing matrix and a covariate table exist, and a data-quality report lists coverage, gaps and derived-vs-published BA agreement per BA.

## Phase 2: Benchmarking harness

One harness, one backtest split, every model behind the same `fit` / `predict_quantiles` interface, with an optional covariates argument.

| Family | Models | Library | Where it runs |
| --- | --- | --- | --- |
| Baselines | Seasonal naive, AutoETS, AutoTheta | statsforecast | CPU |
| Gradient boosting | LightGBM, point + quantile objectives, with weather and calendar features | mlforecast | CPU |
| Deep learning | N-BEATS, N-HiTS (N-HiTS and others with exogenous inputs) | neuralforecast | RTX 4070 |
| Foundation models, zero-shot | 3-4 picks from the GIFT-Eval leaderboard, e.g. Chronos, TimesFM, Moirai, Granite PatchTST-FM-r2; prefer those that accept covariates | Hugging Face / vendor packages | RTX 4070 |

- Pick foundation models by the leaderboard's **strictly zero-shot** column, not the pretrained one.
- Record for each model whether it accepts covariates, and whether known-future and past-only are supported separately.
- Optional stretch: fine-tune the best foundation model and report it separately from zero-shot.
- Log every run (model, version, release date, cutoff, covariate arm, metrics, runtime, cost) to a results table; MLflow if you want the UI.
- Slice results by hierarchy level, covariate arm, weather regime and horizon. "Covariates help most during heat waves and cold snaps" is a more useful finding than one overall rank.

**Done when:** a results table and one summary chart compare all models across levels, horizons and covariate arms.

## Phase 3: Conformal prediction

Wrap every model's output so 80% and 95% intervals actually cover 80% and 95% of outcomes, then show which models needed the most correction.

- Split conformal on residuals from a calibration window just before each cutoff, as the simple baseline.
- Conformalized quantile regression on top of models that already output quantiles (LightGBM quantile, foundation models).
- Time-series-aware methods, since residuals are not exchangeable: EnbPI and adaptive conformal inference (ACI).
- Tooling: statsforecast and neuralforecast have built-in conformal intervals; MAPIE for the rest.
- Report coverage and width per model, per hierarchy level, per covariate arm, and over time. Coverage drifting during heat waves, cold snaps and holiday weeks is a finding worth showing.
- **Key comparison:** raw foundation-model quantiles vs conformalized. If zero-shot quantiles are already well calibrated on unseen data, that is a headline. Also check whether covariates tighten intervals without losing coverage.

**Done when:** a coverage-vs-width plot for every model and method, at both nominal levels.

## Phase 4: Hierarchical reconciliation

Make forecasts coherent across subregion, BA, region and the Lower 48, and measure whether coherence helps or hurts accuracy at each level.

- Observed data is coherent by construction (bottom-up definition above). Only the base forecasts need reconciling.
- Methods via Nixtla's `hierarchicalforecast`: BottomUp, TopDown, MinTrace (OLS, WLS-struct, MinT-shrink).
- Probabilistic reconciliation: bootstrap and PERMBU, compared against Gaussian.
- **The interesting experiment:** conformalize then reconcile vs reconcile then conformalize. Reconciling can break calibrated coverage; few public write-ups test this order on real data.
- **Mixed base forecasts:** foundation model at aggregate levels, LightGBM with weather features at small leaves, then reconcile. Tests whether the best model per level plus reconciliation beats any single model.
- Caveat to state up front: the hierarchy is small (about 130 leaves) and shallow, so findings are indicative, not definitive.
- Check: incoherence before and after, accuracy change per level, coverage change per level.

**Done when:** a table of accuracy and coverage per level for each reconciliation method and order.

## Phase 5: Deployment

Ship one container image that runs as scheduled jobs plus an API. Cloud Run is the v1 target and Lambda the alternative; develop locally against Floci.

The scoring job is what turns a deployment into a benchmark: forecasts made today are scored when actuals arrive days later.

- **Container-first:** one Docker image with the FastAPI app and the job entrypoints. Run it as a Cloud Run service plus Cloud Run Jobs, or on Lambda via the AWS Lambda Web Adapter.
- Storage behind a small interface (local disk, S3 or GCS) so the cloud choice stays swappable.
- **Local development:** docker compose plus Floci on `localhost:4566` for S3, Lambda, EventBridge and API Gateway. Floci runs Lambda in real Docker containers, but it is not full AWS parity, so verify on the real cloud before calling a phase done.
- Serve only CPU-friendly models (LightGBM, a small foundation model). Heavier models stay in the offline benchmark on your GPU.
- Infrastructure as code with Terraform or OpenTofu, both supported by Floci.
- Daily ingest job; weekly forecast job after each UTC week closes. Fetch weather forecasts and store them as of forecast time, forecast, then score forecasts whose actuals have arrived.
- Endpoints: `/forecast` (by level and node), `/leaderboard`, `/health`.
- Monitoring: job logs, a data-freshness check, an alert when rolling interval coverage drifts, and an alert when EIA file or API formats change.

**Done when:** the schedule runs unattended for 8 weeks, so that 4-week-horizon forecasts get scored too, and the leaderboard updates on its own.

## Phase 6: VN1 extension

Rerun Phases 2-4 on VN1 to test whether the EIA-930 findings hold on retail sales.

- Adapter that maps VN1 to the same panel format and builds its client → warehouse → product summing matrix.
- Use price and inventory as covariates for LightGBM and covariate-aware models; note that stock-outs censor sales, the same uncensoring problem as in device demand.
- **Leakage audit first:** check each foundation model's stated pretraining sources for VN1. Report any overlap next to the results.
- The contrast is part of the finding: rich covariates and dense series (energy) against sparse, price-only retail.

**Done when:** one comparison table across both datasets, with a short section on what transferred and what did not.

## Phase 7: Portfolio write-up

Publish findings in a form a hiring manager can scan in two minutes and a practitioner can reproduce in an afternoon.

- **README:** the question, the headline result, one chart, and just commands to reproduce.
- **Long-form post:** leakage-safe protocol, what covariates are worth, where foundation models win and lose, conformal coverage, reconciliation order.
- **Live leaderboard page** fed by the deployed scoring job.
- **Model cards** for each model: version, release date, licence, pretraining notes.
- Don't wait for the end: a short post after Phase 2 and Phase 3 each keeps momentum and gets early feedback.

**Done when:** repo, post and leaderboard are public and linked from each other.

## Risks and open questions

| Risk | Mitigation |
| --- | --- |
| EIA half-year file URLs, columns or API change (the file URL pattern is undocumented) | Keep raw snapshots; test the ingest against fixtures; freshness and format alerts in Phase 5 |
| Subregions do not sum to published BA demand; glitch hours in the data | Bottom-up hierarchy by decision; outlier flags before summing; document the difference from published BA demand |
| Missing hours mistaken for zero demand | Explicit missing-vs-zero flag from Phase 1; derived totals are null when any leaf is missing; report metrics with and without gappy series |
| Covariate leakage from using observed weather instead of the forecast at cutoff | Store point-in-time forecasts; verify Open-Meteo archived forecasts support this before reporting covariate results |
| EIA data restated after the fact | Dated immutable snapshots; score the live track against a fixed lag |
| Hierarchy too small to support strong reconciliation claims | State it as a limitation; the VN1 replication adds a deeper tree |
| Foundation models too large for serverless | Benchmark locally on GPU; serve only CPU-friendly models; batch jobs on Cloud Run Jobs if needed |
| Model licences block public results or serving | Check each licence before adding a model to the registry |
| Scope creep across two datasets and seven phases | Each phase has a done-when; publish after each before starting the next |

### Open questions

- [ ] Cloud Run or Lambda as the v1 target? The plan keeps both open until Phase 5.
- [x] Weekly first, with daily and hourly built into the pipeline for later tracks (decided 13 Oct).
- [ ] Horizons of 1, 2 and 4 weeks: confirm after checking how far the archived weather forecasts are reliable.
- [ ] Is the 8-week horizon worth including as calendar and climatology only?
- [ ] Do Open-Meteo's archived forecasts give true point-in-time forecasts for past dates?
- [ ] Which foundation models accept covariates, and in what form (known-future, past-only)?
- [ ] Is floci-gcp mature enough to emulate the Cloud Run path locally, or only the AWS path via Floci?
- [ ] NHS prescribing data (practice × drug, monthly, price covariates) was considered as a deeper-hierarchy dataset but its format and licence were never checked. Revisit if the EIA hierarchy proves too thin.
