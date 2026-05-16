# Entry & Exit Examples — Concrete Scenarios

This document shows **real-world style examples** for each entry type: when the bot buys, when it holds, and when it sells. Includes both ✅ "BUY" and ❌ "SKIP" examples.

---

## 1. NORMAL BUY

**What it is:** A non-momentum, competition-driven entry into a market already trading in the 0.65–0.84 band.

**Entry conditions:**
- YES in `[BUY_MIN_THRESHOLD, BUY_MAX_THRESHOLD]` (currently 1.1/1.1 = **DISABLED**; re-enable by setting e.g. 0.65 / 0.84)
- Market leads siblings by ≥ 50 percentage points (competition filter)
- No negative momentum in the last 15 minutes
- Live CLOB best ask confirmed below the band ceiling before submitting

**Take-profit:** Standard `take_profit_normal` (e.g. 0.97)
**Stop-loss:** `stop_loss_normal=0.50` OR entry ×(1 - 0.30)

### ✅ BUY example — Tokyo max 26°C at 0.81

```
09:05  YES = 0.81   siblings: [24°C=0.10, 25°C=0.08, 27°C=0.01]
                    market_lead_gap = 0.81 - 0.10 = 0.71  ≥ 0.50 ✓
                    in band [0.65, 0.84] ✓
                    15m momentum = +0.01 (slightly positive) ✓
                    → BUY at 0.81, entry_type=normal
```

### ❌ SKIP — price too high

```
09:05  YES = 0.86   siblings: [25°C=0.12]
                    0.86 > BUY_MAX_THRESHOLD=0.84 → SKIP live_price_above_entry_type_max
```

### ❌ SKIP — competition too close

```
09:05  YES = 0.75   siblings: [26°C=0.36, 24°C=0.12]
                    market_lead_gap = 0.75 - 0.36 = 0.39 < 0.50
                    → SKIP competition_fail
```

### ❌ SKIP — falling price

```
09:05  YES = 0.80   15m momentum = -0.15 (price dropped 15 points in 15min)
                    → SKIP negative_momentum
```

### Exit examples

| Scenario | Exit reason |
|----------|-------------|
| YES reaches 0.97 | `take-profit` |
| YES drops from 0.81 entry to 0.55 (below stop 0.57 = 0.81×0.70) | `stop-loss` (SL_RELATIVE) |
| YES drops below hard floor 0.50 | `stop-loss` (SL_ABSOLUTE) |
| Sibling surges +0.20 in 15 min | `competitor-surge` |

---

## 2. NORMAL WINNER (new strategy)

**What it is:** A "collect end-of-day profit" strategy. Buys high-conviction near-resolved markets — YES must be ≥ 0.945 **and** must have been stably above 0.75 for at least 30 minutes.

**Entry conditions:**
- YES in [0.945, 0.9949]
- Contiguous tail of ring-buffer samples shows price above 0.75 for ≥ 30 min (no gap > 2 min)
- Market not already held by bot

**Take-profit:** YES ≥ 0.9997 (market about to resolve YES)
**Stop-loss:** max(0.88, entry × 0.95)

### ✅ BUY example — Paris max 24°C

```
Timeline:
  07:30  YES = 0.77  (above 0.75 ✓)
  08:00  YES = 0.83
  08:30  YES = 0.90
  09:00  YES = 0.94
  09:30  YES = 0.945   ← bot evaluates entry
         contiguous run above 0.75: 07:30→09:30 = 120 min ≥ 30 min ✓
         YES in [0.945, 0.9949] ✓
         → BUY at 0.945, entry_type=normal_winner
```

### ✅ BUY example — exact 30 min window

```
Timeline:
  09:00  YES = 0.80
  09:10  YES = 0.88
  09:20  YES = 0.92
  09:30  YES = 0.945  ← bot evaluates
         contiguous run above 0.75: 09:00→09:30 = 30 min = exactly min_sec ✓
         → BUY
```

### ❌ SKIP — price spiked but no history

```
Timeline:
  09:28  YES = 0.40  (was below 0.75)
  09:29  YES = 0.55
  09:30  YES = 0.945  ← bot evaluates
         contiguous run above 0.75: only ~1 min (from when it crossed 0.75) < 30 min
         → SKIP  (stability_check failed)
```

### ❌ SKIP — price dipped below floor recently

```
Timeline:
  07:00–09:00  YES ≈ 0.90  (above floor ✓)
  09:25        YES = 0.60  (dip — breaks contiguous run!)
  09:30        YES = 0.947  ← bot evaluates
               contiguous run after dip: only 5 min < 30 min
               → SKIP  (stability_check failed)
```

### ❌ SKIP — price in band but too low entry (not in 0.945 zone)

```
09:30  YES = 0.92   (below normal_winner_min_entry=0.945)
       → not evaluated as normal_winner (price gate not met)
       → may still qualify as momentum or double_momentum
```

### ❌ SKIP — price above band ceiling

```
09:30  YES = 0.9950  (above normal_winner_max_entry=0.9949)
       → SKIP  (price gate not met — avoid buying right at resolution)
```

### Exit examples

| Scenario | Exit reason |
|----------|-------------|
| YES reaches 0.9997 | `take-profit` |
| YES drops from 0.945 entry to below 0.8978 (entry×0.95 = 0.8978) | `stop-loss` (SL_RELATIVE) |
| YES drops below hard floor 0.88 | `stop-loss` (SL_ABSOLUTE) |

---

## 3. MOMENTUM ENTRY

**What it is:** Buys a fast-rising market. Price must rise by at least +0.20 absolute points OR +100% fractionally **and** a sibling market must be falling on the same time window (leader-yield gate).

**Entry conditions:**
- Absolute rise ≥ 0.20 OR pct rise ≥ 100% in window W (1m–15m grid)
- On the same window W: a sibling must have dropped ≥ 0.40 pts OR ≥ 41% of its own price
- Current YES in [0.55, 0.85]
- At least 3 price samples in the ring buffer

**Take-profit:** `take_profit_momentum` (e.g. 0.97)
**Stop-loss:** `stop_loss_momentum=0.35` OR entry ×(1 − 0.50)

### ✅ BUY example — Madrid 30°C surges

```
08:00  YES = 0.40  (older sample — sibling Madrid 31°C = 0.55)
08:10  YES = 0.62  (+0.22 rise in 10m = +55% ✓)
       Sibling Madrid 31°C: 0.55 → 0.10 (−0.45 pts = −82% of 0.55 ✓)
       Current YES = 0.62  in [0.55, 0.85] ✓
       3 samples ✓
       → BUY at 0.62, entry_type=momentum, trigger_window=10m_win
```

### ✅ BUY example — percent gate triggers (cheap bucket surging)

```
07:50  YES = 0.05  (small bucket)
08:00  YES = 0.12  (+140% rise in 10m, above pct threshold 100% ✓)
       Sibling leader fell from 0.85 → 0.44 (−48% ✓)
       Current YES = 0.12  in [0.10 dbl_min, but 0.12 < mom_min 0.55]
       → SKIP  (below momentum_min_price 0.55; may qualify double_momentum if
                using wider dbl_min=0.10 — see section 4)
```

### ❌ SKIP — rise too small

```
08:00  YES = 0.60  → 08:10 YES = 0.75  (+0.15 pts < 0.20 threshold)
       pct rise = 25% < 100%
       Neither gate passes → SKIP
```

### ❌ SKIP — sibling not falling (leader-yield fails)

```
08:00  YES = 0.55  → 08:10 YES = 0.80  (+0.25 pts ✓ rise)
       All siblings stable: leader stayed at 0.85
       → SKIP  leader_yield_blocked (no qualifying sibling drop)
```

### ❌ SKIP — price above momentum band

```
08:10  YES = 0.86  (above momentum_max_entry=0.85)
       Rise qualifies, sibling fell — but current price out of band
       → SKIP
```

### Exit examples

| Scenario | Exit reason |
|----------|-------------|
| YES reaches 0.97 | `take-profit` |
| Fast drop: peak 0.80 → 0.62 within the momentum window (−0.18 > `momentum_fast_exit_drop`) **and** `momentum_fast_exit_enabled` is **true** | `momentum-stop-loss` |
| Same swing when `momentum_fast_exit_enabled` is **false** | no exit from this rule — per-type `stop-loss` / trailing still apply if breached |
| Sibling market surges +0.15 in 15 min | `competitor-surge` |
| YES drops below hard floor 0.35 | `stop-loss` (SL_ABSOLUTE) |
| Trailing: entered 0.65, peaked at 0.90 → locked 0.75; now < 0.75 | `trailing-stop` |

---

## 4. DOUBLE MOMENTUM ENTRY

**What it is:** Like momentum but with **much higher** thresholds. Catches massive multi-standard-deviation moves on very cheap buckets. Entry band is wider (0.10–0.91).

**Entry conditions:**
- Absolute rise ≥ 0.40 OR pct rise ≥ 900% in window W
- Same leader-yield gate as momentum
- Current YES in [0.10, 0.91]

**Take-profit / Stop-loss:** same as momentum (per-type fields share the same constants by default).

### ✅ BUY example — extreme surge from 0.02

```
07:00  YES = 0.02
08:00  YES = 0.20  (+900% rise in 60m, above 900% pct threshold ✓)
       Sibling leader fell from 0.95 → 0.50 (−47% ✓)
       Current YES = 0.20  in [0.10, 0.91] ✓
       → BUY at 0.20, entry_type=double_momentum, trigger_window=60m_win
```

### ✅ BUY example — absolute gate triggers

```
07:00  YES = 0.20
07:15  YES = 0.65  (+0.45 pts in 15m ≥ 0.40 ✓)
       Sibling dropped from 0.70 → 0.28 (−60% ✓)
       → BUY at 0.65, entry_type=double_momentum
```

### ❌ SKIP — standard momentum fires first

```
If abs rise = 0.22 (above standard 0.20 but below double 0.40):
  double path: SKIP (rise too small for double)
  standard path: BUY at entry_type=momentum
  → never reaches double_momentum
```

### ❌ SKIP — current price above double band

```
YES = 0.92  (above double_momentum_max_price=0.91)
Even if rise was massive — band ceiling blocks it
```

### Exit examples — same as Momentum (same stop-loss structure)

---

## 5. PERSISTENT LEADER ENTRY (2-Hour Dominance)

**What it is:** A market that has held **#1 YES rank** among its siblings for ≥ 80% of the last 2 hours gets **relaxed** momentum thresholds — only +0.10 absolute OR +25% pct rise needed. No sibling fall required.

**Entry conditions:**
- `is_persistent_leader`: market was #1 in ≥ 80% of ring-buffer samples over last 7200s
- Small rise: ≥ 0.10 pts OR ≥ 25% in any entry window
- Current YES in [0.55, 0.85]

### ✅ BUY example — Osaka 34°C leads all day, small nudge

```
09:00  Event: [34°C=0.70, 33°C=0.20, 35°C=0.10]
       34°C has been #1 for the last 2h in 85% of samples ✓ (≥80%)
10:00  34°C: 0.70 → 0.82 (+0.12 in 15m, ≥ 0.10 threshold ✓)
       Current YES = 0.82 in [0.55, 0.85] ✓
       → BUY at 0.82, entry_type=momentum, reason=persistent_leader_2h
       (no sibling fall required)
```

### ❌ SKIP — market has NOT dominated for 2h

```
09:00  34°C took the lead only 20 minutes ago (rank was 2nd before that)
       is_persistent_leader = False (fraction < 0.80)
       → falls through to normal momentum path (needs +0.20 + sibling fall)
```

### ❌ SKIP — no rise at all

```
34°C dominated for 2h ✓ but price flat: 0.72 → 0.73 (+0.01 < 0.10)
       pct rise = 1.4% < 25%
       Neither PL gate passes → falls through to normal evaluation
```

### Exit examples — same as Momentum (entry_type is tagged as `"momentum"`)

---

## Summary Table

| Strategy | Entry Band | Rise Required | Sibling Fall | Stability Gate | Take-Profit | Stop-Loss |
|----------|------------|---------------|--------------|----------------|-------------|-----------|
| **Normal Buy** | 0.65–0.84 | none | none (competition filter) | none | ~0.97 | 0.50 / −30% |
| **Normal Winner** | 0.945–0.9949 | none | none | ≥30 min above 0.75 | **0.9997** | 0.88 / −5% |
| **Momentum** | 0.55–0.85 | +0.20 OR +100% | ≥−0.40 pts OR −41% | none | ~0.97 | 0.35 / −50% |
| **Double Momentum** | 0.10–0.91 | +0.40 OR +900% | ≥−0.40 OR −41% | none | ~0.97 | 0.05 / −50% |
| **Persistent Leader** | 0.55–0.85 | +0.10 OR +25% | **none** | #1 rank ≥80% of 2h | ~0.97 | 0.35 / −50% |
