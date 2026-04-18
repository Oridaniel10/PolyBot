# Model probability, forecast, and calibration (weather YES buckets)

This document describes **how the bot turns a point forecast (°C) into `model_prob` = P(YES)** for a Polymarket temperature bucket, and how **calibration** shifts that forecast. It matches the code paths:

- `forecast/forecast_service.py` → `get_forecast_max_for_city_day`
- `research/calibration_apply.py` → `adjust_consensus_optional`, `resolved_research_sigma_c`, `mae_global`, `sigma_from_mae`
- `research/probability_from_forecast.py` → `implied_yes_prob`
- `strategy/probability_engine.py` → `compute_model_prob`

The **decision engine** then compares `model_prob` to the CLOB YES price, competition, gates, etc. (`strategy/decision_core.py`). This file focuses only on **forecast → μ → σ → P(bucket)**.

---

## 1. Raw forecast(s)

1. **Open-Meteo** daily max °C for `(city, event_date)` in the city’s IANA timezone (`forecast/open_meteo.py`).
2. Optionally **OpenWeather** for the same (`forecast/openweather.py`) when `enable_openweather_forecast` is on and a key exists.
3. **Consensus** = mean of available sources (one or two numbers). If only OM exists, consensus = OM.

*Example:* Open-Meteo returns **17.9 °C** for London on the event day; OpenWeather is off → consensus = **17.9 °C**.

---

## 2. Calibration = additive bias on consensus

File: `data/research/calibration_latest.json` (path constant `RESEARCH_CALIBRATION_LATEST_FILE`).

- `bias_for_city(city_key)` reads **`by_city[city].bias_c`** if present; otherwise falls back to **`models.open_meteo_forecast.bias_c`** (global OM bias).
- **Adjusted consensus** (what the code calls `consensus_c` in the decision path):

```text
μ = consensus_raw + bias_c   (°C)
```

*Example:* raw consensus **17.9 °C**, global bias **+0.3 °C** → **μ = 18.2 °C** (this is the Gaussian **mean** for daily max T).

---

## 3. Gaussian spread σ (uncertainty)

Used as **`sigma_c`** in `implied_yes_prob(..., sigma_c=sigma)`.

- If runtime **`research_sigma_c` > 0** (merged from `runtime_config.json` / `config/settings.py`):  
  **`σ = clamp(research_sigma_c, 0.5 … 8.0)`** (see `resolved_research_sigma_c`).
- Else: **`σ = sigma_from_mae(mae_global())`** with  
  `sigma_from_mae(mae) = max(0.5, mae × √(π/2))`  
  and `mae_global()` from **`models.open_meteo_forecast.mae_c`** in `calibration_latest.json` (fallback behaviour in code if missing).

*Example:* `research_sigma_c = 1.307` → **σ = 1.307**.  
*Example (σ from MAE only):* MAE = **1.4 °C** → σ = max(0.5, 1.4 × 1.253…) ≈ **1.75 °C**.

Belief model (research / model_prob only): **T ~ Normal(μ, σ²)** with T = true daily max °C (continuous simplification).

---

## 4. From (μ, σ) to P(YES) for the market’s bracket

Function: `implied_yes_prob(parsed, forecast_mean_c=μ, sigma_c=σ)` with `Φ(z)` = standard normal CDF.

### 4.1 EXACT integer °C bucket (most common in your titles)

For threshold **c** °C (integer from the title):

```text
lo = floor(c − 0.5)
hi = ceil(c + 0.5)
P(YES) = P(lo < T < hi) = Φ((hi − μ)/σ) − Φ((lo − μ)/σ)
```

*Numeric example:* **“Will the highest temperature in London be 18°C on …?”**  
- Parsed **c = 18** → **lo = 17**, **hi = 19** (as in code).  
- **μ = 18.2 °C**, **σ = 1.307 °C**.

Then:

- z_hi = (19 − 18.2) / 1.307 ≈ **0.612**
- z_lo = (17 − 18.2) / 1.307 ≈ **−0.918**
- **P(YES) ≈ Φ(0.612) − Φ(−0.918) ≈ 0.550** → about **55.0%** model probability for that exact bucket.

So even if the market trades near 50%, the model can say ~55% because μ sits slightly above the bucket centre.

### 4.2 AT LEAST (“X°C or above”)

```text
lo = threshold_c − 0.5
P(YES) = 1 − Φ((lo − μ)/σ)
```

*Sketch:* threshold **20 °C**, **μ = 18.2**, **σ = 1.307** → lo = **19.5** → P(YES) ≈ **1 − Φ((19.5−18.2)/1.307) ≈ 0.16** (~16%).

### 4.3 AT MOST (“X°C or below”)

```text
hi = threshold_c + 0.5
P(YES) = Φ((hi − μ)/σ)
```

---

## 5. End-to-end mini table (EXACT 18 °C)

| Step | Value |
|------|--------|
| Open-Meteo max | 17.9 °C |
| + calibration bias | +0.3 °C |
| **μ (mean of T)** | **18.2 °C** |
| **σ** | **1.307** (from `research_sigma_c` in this example) |
| Bracket | EXACT 18 °C → interval **(17, 19)** in code |
| **model_prob P(YES)** | **≈ 0.550** |

---

## 6. Where this plugs into the bot

1. `get_forecast_max_for_city_day` returns **`consensus_adjusted`** = μ after bias.  
2. `compute_model_prob(parsed, consensus_c, research_sigma_c)` → **`model_prob`**.  
3. `decision_core.evaluate_entry` uses `model_prob` vs CLOB, edges, competition, momentum, etc.

**Research “edge”** (separate pipeline) uses the same calibrated consensus for implied YES vs fees; see `strategy/research_signal.py` and `STRATEGY_LOGIC.md` — not re-derived here.

---

## 7. Fahrenheit titles

Polymarket titles in °F are parsed to **threshold in °C** first (`forecast/parse_title.py`); all Gaussian math above is in **°C**.
