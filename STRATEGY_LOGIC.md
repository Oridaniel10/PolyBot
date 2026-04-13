# לוגיקת קנייה ומכירה — Polymarket bot

מסמך זה מתאר את מה שהבוט **באמת** עושה בקוד (נכון לגרסה הנוכחית). ערכי סף ברירת מחדל נטענים מ־`config/constants.py` וניתנים לעדכון ב־`data/runtime_config.json` (מיזוג בכל טיק).

**נוסף ב־runtime:** `scan_interval_seconds`, `cash_reserve_usd`, `min_order_notional_usd`, `max_buy_notional_usd`, `max_trade_fraction_of_cash`, `dashboard_weather_max_pages`, וכן **תחזית ו־flow** (ראו סעיף נפרד). שמירה מה־UI שולחת Telegram (אם מוגדרים טוקן וצ׳אט).

---

## תהליכים מומלצים (מקומי / שרת)

| תהליך | פקודה / הערה |
|--------|----------------|
| **בוט מסחר** | `python strategy/bot_runner.py` (או הנתיב שבו אתה מריץ) — **רק עותק אחד**; כולל לולאת מסחר + אופציונלית דוח תחזית לטלגרם ברקע |
| **דשבורד** | `python -m app.dashboard` (או `uvicorn` לפי `main_bot.py`) — API ב־`/api/*` ו־UI ב־`/dashboard/` אחרי `npm run build` בתיקיית `ui/` |
| **תחזית standalone** | `python -m forecast` — דוח טלגרם לפי מרווח ב־runtime; **לא** תלוי ב־`forecast_digest_enabled` (מיועד לכיבוי דיג׳סט בבוט + תהליך נפרד) |

אין צורך להריץ שירות נפרד ל־Open-Meteo (קריאות HTTP לפי דרישה). ל־OpenWeather (אופציונלי): הגדר `OPENWEATHER_API_KEY` ב־`.env` והפעל `enable_openweather_forecast` ב־runtime.

---

## זרימה כללית

1. **סנכרון** (`sync_state_with_portfolio`): מושך פוזיציות פתוחות מ־Data API, מעדכן `state.json` → `active_trades` (כולל `entry_price`, `last_price` מה־API, `condition_id`, וכו').
2. **סריקה** (`run_once`): שווקי מזג אוויר רלוונטיים להיום + מעבר נוסף על פוזיציות שלא הופיעו בסריקה (יציאות / claim). בסוף הטיק: **דגימת flow** (אם מופעלת) — נפח/OI ל־`data/flow_samples.jsonl` עם הגבלת אירועים לטיק (`flow_max_events_per_tick`).
3. לכל שוק: **`process_single_market`** — סדר הבדיקות חשוב (ראו למטה).

---

## מתי קונים (BUY YES)

מתבצע רק אם **כל** התנאים הבאים מתקיימים:

| תנאי | פירוט |
|------|--------|
| אין פוזיציה | אין שורה מתאימה ב־`active_trades` לשוק הזה |
| `allow_new_buys` | רק בשווקים שנסרקו ליום המטרה; לא ב־"exit pass" לשמור על מדיניות היום |
| שוק פתוח ל־CLOB | `market_can_post_clob_orders(market)` — לא סגור, לא paused, `acceptingOrders` / `enableOrderBook` וכו' |
| מחיר YES בטווח | `buy_min ≤ yes ≤ buy_max` (ברירת מחדל ~0.60–0.70), או הרחבה במומנטום (ראו למטה) |
| לא ב־blacklist | מזהה השוק לא ב־`runtime_config` / blacklist יומי |
| anti-churn | אין cooldown פעיל אחרי יותר מדי stop-loss ברצף (לפי `churn_*`) |
| פער מול מקום שני | אם `enable_competition_filter`: ההפרש בין מחיר ה־YES של המועמד לבין ה־YES הגבוה ביותר בשווקים אחים (אותו Gamma event) ≥ `min_lead_over_runner_up` |
| שעה מקומית בעיר | לפני `BUY_EARLIEST_HOUR` (ברירת מחדל 10) **באזור הזמן של העיר בכותרת השוק** — לא קונים (ייצוב תחזית בבוקר מקומי) |
| מקסימום פוזיציות | לא יותר מ־`MAX_CONCURRENT_POSITIONS` פוזיציות פתוחות בו־זמנית |
| תחזית חיצונית (אופציונלי) | אם `forecast_gate_buy`: קניית YES רק אם מודל התחזית (Open-Meteo ± OpenWeather) “תומך” בברקט לפי `forecast_contradict_margin_c`. אם `forecast_reduce_usd_if_weak`: מקטין נוטיונל כשהתחזית חלשה (`forecast_weak_size_factor`) |

**גודל עסקה:** `tradable_cash = max(0, free_cash - cash_reserve_usd)`. אז `planned = min(tradable × max_trade_fraction_of_cash, max_buy_notional_usd, tradable)`; אם `planned < min_order_notional_usd` לא קונים. דוגמה: cash 30$, reserve 10$ ⇒ tradable 20$; עם fraction 0.99 ו־cap 6$ ⇒ עסקה עד 6$ (אם מעל min order).

**מומנטום (אם `enable_momentum`):** אם בחלון הדקות `momentum_window_min` עלייה ≥ `momentum_rise` והמחיר ≥ `momentum_min_price`, אפשר להיכנס עד `momentum_max_entry` גם מעל `buy_max` הרגיל.

---

## מתי מוכרים / יוצאים

### סדר הבדיקות ב־`process_single_market` (לפוזיציה קיימת)

1. **התאמת Gamma ↔ תיק**  
   אם `trade_row_matches_gamma_market` נכשל — **לא מבצעים כל פעולה** על השוק (מניעת מכירה על טוקן לא נכון).  
   **חשוב:** אם `condition_id` בפוזיציה תואם לשוק ב־Gamma, ההתאמה נחשבת **מספקת** (לא תלוי בדיוק טקסט של כותרת).

2. **שוק סגור / resolved**  
   אם `status` ב־`closed` / `claimable` / `resolved` → ניסיון **`claim_position`** (לא מכירת CLOB רגילה).

3. **מומנטום — מכירה לפי ירידת peer** (אם מופעל)  
   פוזיציה פתוחה + שוק מקבל הזמנות: אם שוק אחר **באותו event** ירד ב־YES מעל `momentum_peer_drop` בחלון → **`close_position`** עם סיבה `momentum-peer-drop`.

3b. **Flow — יציאה לפי peer surge** (אם `enable_flow_peer_exit`)  
   לפי דגימות אחרונות ב־`flow_samples.jsonl`: אם “peer” באותו אירוע מראה ירידת נפח/לחץ חזקה ביחס לפוזיציה שלך → **`close_position`** עם סיבה `flow-peer-surge` (עוקף חלק מ־cooldowns של מינימום CLOB, כמו stop/take).

4. **Stop-loss**  
   מופעל רק אם:
   - **מחיר חלש:** `gamma_yes < stop_loss` **או** `mark (מ־Data API) < stop_loss` — כולל **mark=0** כש־Gamma עדיין “גבוה” (Stale).
   - **בהפסד מול כניסה:** `mark < entry` (כולל mark≈0 מול long YES).

5. **Take-profit**  
   אם `max(gamma_yes, mark) ≥ take_profit` (ברירת מחדל 0.98) → **`close_position`** (`take-profit`).  
   לאחר תיקון: `last_price` בסנכרון **תמיד** משקף את ה־mark מה־API (גם 0), ולא מחליפים 0 ב־entry בטעות.

6. **שוק לא מקבל CLOB**  
   אם לא ניתן לפרסם הזמנות — יציאה “מושהית”, התראה חד־פעמית (לא חוסם take-profit/stop שכבר הופעלו לפני כן בסיבוב — הסדר למעלה מבטיח שניסה למכור קודם).

### מה קורה ב־`close_position`

- אם יש `pending_limit_sell_order_id` — לא מוכרים שוב עד ניקוי.
- אם cooldown “מתחת למינימום CLOB” — מחכים.
- אם אין YES על הבורסה לפי Data API — מסירים מצב / הודעת SELL N/A.
- אחרת — `place_market_sell_yes` (או limit GTC / skip לפי הלקוח).

---

## למה ייתכן שלא נמכר למרות שבתצוגה “כבר ניצחתי / הפסדתי”?

סיבות נפוצות (חלקן תוקנו בגרסה האחרונה):

1. **חסימה בגלל כותרת** — בעבר נדרש גם התאמת מחרוזת שאלה; אם Data API ו־Gamma ניסחו מעט שונה, **כל** המכירה נחסמה. עכשיו **`condition_id` תואם = מספיק**.
2. **Stop-loss שלא זיהה הפסד קיצוני** — `mark=0` או Gamma stale: תנאי “חלש”/“בהפסד” לא התקיימו. עכשיו **mark&lt;threshold** ו־**mark&lt;entry** מטפלים גם ב־0.
3. **`last_price` שגוי בסנכרון** — `0` ב־curPrice נחשב falsy והוחלף ב־entry; take-profit/stop ראו מחיר כניסה במקום שוק. עכשיו **`last_price` = cur מה־API תמיד**.
4. **שוק ב־`/closed` אבל לא ב־רשימת הסטטוסים** — אז לא claim ולא sell; צריך לבדוק ב־Gamma את `status` / CLOB.
5. **מינימום מניות CLOB / SELL SKIPPED** — נשאר ב־state עם cooldown.
6. **אין מפתח Gamma** — אם אי אפשר לטעון שוק מ־`get_market_by_id` / `condition_id`, `process_single_market` לא רץ על אותו dict; ה־exit pass אמור למשוך לפי id/condition מהמצב.

---

## Anti-churn (אחרי stop-loss)

- כל **stop-loss** מצליח מגדיל מונה לשוק.
- מגיע ל־`churn_max_stop_cycles` → **cooldown** לפני קנייה חוזרת.
- **Take-profit** מאפס את המונה (מדיניות: לא “להעניש” רווח).

---

## תחרות (אותו Event)

לפני BUY: השוואת מחיר ה־YES שלך מול ה־YES הגבוה ביותר בשווקים **אחרים** באותו אירוע Gamma. אם אין event id בשוק — המסנן לא חוסם.

---

## על מה מבוססת האסטרטגיה (בשורה התחתונה)

- **כניסה:** ניסיון לקנות YES כשהשוק “סביר” (לא זול מדי / לא יקר מדי לפי סף), עם מגבלות סיכון (גודל עסקה, תחרות מול תוצאות אחרות באותו אירוע, blacklist, cooldown, שעה מקומית, מגבלת פוזיציות), ואופציונלית **סינון/הקטנה לפי תחזית חיצונית** (`forecast_*`).
- **יציאה:** שילוב של
  - **take-profit** כשהסתברות/סימון שוק קרובים ל־1 (נעילת רווח),
  - **stop-loss** כשהשוק חלש **וגם** אתה מתחת לכניסה (מגן מפני המשך הפסד; כולל מצב resolved NO עם mark≈0),
  - **מומנטום** (אופציונלי) לכניסה אגרסיבית יותר ויציאה כשמתחרה באירוע קורס,
  - **flow-peer-surge** (אופציונלי) לפי דגימות נפח/OI,
  - **claim** כשהשוק נסגר/הוכרע.
- **תחזית חיצונית:** Open-Meteo (ברירת מחדל) ואופציונלית OpenWeather — לדוח טלגרם (`forecast_digest_*`), לתצוגה בדשבורד (`GET /api/forecast/preview`), ולשער כניסה/גודל (`forecast_gate_buy`, `forecast_reduce_usd_if_weak`). זה **לא** “AI”; זה מקורות תחזית סטנדרטיים + השוואה לברקטים בכותרת.

---

## תחזית (Telegram + דשבורד) ו־rate limits

- **דוח טלגרם:** חוט רקע ב־`bot_runner` כש־`forecast_digest_enabled`; מרווח `forecast_digest_interval_sec` (ברירת מחדל ~10 דק׳, מוגבל 120–3600 שנ׳). היקף: `forecast_digest_scope` = `scan` (אותה אצוות שווקים כמו הסורק) או `positions` (רק פוזיציות פתוחות).
- **דשבורד:** טבלת “Forecast preview” קוראת את אותה לוגיקת קיבוץ דרך `/api/forecast/preview` (קריאות תחזית + Gamma כמו רשימת מזג אוויר).
- **Flow:** בכל טיק נדגמים עד `flow_max_events_per_tick` אירועים; קריאות Data API ו־CLOB מקובצות בלקוח — ראו [POLY_RATE_LIMITS.MD](POLY_RATE_LIMITS.MD). אל תריץ **שני** בוטים במקביל על אותו חשבון.

---

## הגנות ב־`config/constants.py` (ברירת מחדל)

| הגנה | ערך | סיבה |
|------|------|------|
| `BUY_EARLIEST_HOUR` | 10 (**שעה מקומית של העיר בכותרת**) | תחזיות מתייצבות בבוקר מקומי; לא קונים לפני |
| `MAX_CONCURRENT_POSITIONS` | 7 | מגביל חשיפה כוללת |
| `BUY_MIN_THRESHOLD` / `BUY_MAX_THRESHOLD` | 0.60 / 0.70 | טווח כניסה ל־YES |
| `STOP_LOSS_THRESHOLD` | 0.50 | יציאה מהירה יותר כשהשוק חלש ואתה בהפסד |
| `TAKE_PROFIT_THRESHOLD` | 0.96 | נעילת רווח לפני קצה 1.0 |
| `MIN_LEAD_OVER_RUNNER_UP` | 0.10 | דורש יתרון מול ברקטים אחרים באותו אירוע |

## ניתוח סיכונים — למה ההפסדים חזקים מהרווחים

### אסימטריית רווח/הפסד
- כניסה ב-0.65, TP ב-0.96 = **+0.31 למניה**
- כניסה ב-0.65, SL ב-0.50 = **-0.15 למניה**
- כניסה ב-0.65, קריסה ל-0 = **-0.65 למניה** (הפסד מלא)

עם SL ב-0.50 (במקום 0.44), ההפסד המרבי במקרה רגיל הוא **-0.15**, לעומת רווח של **+0.31**. יחס סיכון/סיכוי = **1:2** — צריכים לצדוק רק ב-33% מהזמן.

### עמלות Weather
Taker fee rate = **0.05**. פורמולה: `fee = shares × 0.05 × p × (1-p)`. על עסקה של $5 ב-0.65: **~$0.10 קנייה + ~$0.10 מכירה = ~$0.20 לכל round-trip**. על 5 עסקאות ביום = **~$1.00 עמלות**.

### קריסה ל-0 (Rug / Liquidity Drain)
כשמישהו מוכר כמות גדולה ברגע, המחיר קופץ מ-0.60 ל-0.00 ללא מעבר דרך ה-SL. **אין הגנה מושלמת** מזה בשוק עם נזילות נמוכה. ההגנות הטובות ביותר:
1. **MAX_CONCURRENT_POSITIONS** — מגביל חשיפה לכמה עסקאות
2. **MAX_BUY_NOTIONAL** — מגביל גודל עסקה בודדת
3. **BUY_EARLIEST_HOUR** — נכנסים רק כשהתחזית יציבה
4. **MIN_LEAD_OVER_RUNNER_UP** — לא נכנסים לשוק מפוקפק

## רעיונות לשיפור עתידי

### שיטות נוספות לבחירת השקעות
1. **Bracket hedging** — במקום לקנות YES אחד, לקנות 2-3 brackets סמוכים (22°C + 23°C) עם חלוקת סיכון
2. **Late entry** — לקנות רק אחרי 14:00 כשהתחזית כמעט סופית (פחות רווח אבל הרבה יותר בטוח)
3. **Arbitrage scanning** — אם סכום ה-YES של כל ה-brackets > 1.0, יש הזדמנות ארביטראז'

---

## קבצים רלוונטיים בקוד

| נושא | קובץ |
|------|------|
| סדר לוגיקה לשוק | `strategy/trades.py` → `process_single_market` |
| סטופ / TP | `strategy/probability.py` |
| התאמת פוזיציה לשוק Gamma | `strategy/market_match.py` |
| סנכרון מחירים | `strategy/sync_portfolio.py` |
| דגימת flow / אותות יציאה | `strategy/flow_sampling.py`, `strategy/flow_signals.py` |
| תחזית + דוח HTML טלגרם + preview | `forecast/` (בעיקר `digest_runner.py`, `forecast_service.py`) |
| API דשבורד (כולל `/api/forecast/preview`) | `app/dashboard.py` |
| לקוח: אצוות Data/CLOB | `polymarket_client.py` |
| ספים וריצה | `config/constants.py`, `config/settings.py`, `strategy/bot_runner.py` |

למגבלות קצב API ראו [POLY_RATE_LIMITS.MD](POLY_RATE_LIMITS.MD).
