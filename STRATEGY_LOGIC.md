# לוגיקת קנייה ומכירה — Polymarket bot

מסמך זה מתאר את מה שהבוט **באמת** עושה בקוד (נכון לגרסה הנוכחית). ערכי סף ברירת מחדל נטענים מ־`config/constants.py` וניתנים לעדכון ב־`data/runtime_config.json` (מיזוג בכל טיק).

**נוסף ב־runtime:** `scan_interval_seconds`, `cash_reserve_usd` (מזומן שלא משתתף בקניות; ברירת מחדל 10$ מ־`constants`), `min_order_notional_usd`, `max_buy_notional_usd`, `max_trade_fraction_of_cash` (אחוז מ־**tradable cash** = cash פחות reserve), ו־`dashboard_weather_max_pages`. שמירה מה־UI שולחת Telegram (אם מוגדרים טוקן וצ׳אט).

---

## זרימה כללית

1. **סנכרון** (`sync_state_with_portfolio`): מושך פוזיציות פתוחות מ־Data API, מעדכן `state.json` → `active_trades` (כולל `entry_price`, `last_price` מה־API, `condition_id`, וכו').
2. **סריקה** (`run_once`): שווקי מזג אוויר רלוונטיים להיום + מעבר נוסף על פוזיציות שלא הופיעו בסריקה (יציאות / claim).
3. לכל שוק: **`process_single_market`** — סדר הבדיקות חשוב (ראו למטה).

---

## מתי קונים (BUY YES)

מתבצע רק אם **כל** התנאים הבאים מתקיימים:

| תנאי | פירוט |
|------|--------|
| אין פוזיציה | אין שורה מתאימה ב־`active_trades` לשוק הזה |
| `allow_new_buys` | רק בשווקים שנסרקו ליום המטרה; לא ב־"exit pass" לשמור על מדיניות היום |
| שוק פתוח ל־CLOB | `market_can_post_clob_orders(market)` — לא סגור, לא paused, `acceptingOrders` / `enableOrderBook` וכו' |
| מחיר YES בטווח | `buy_min ≤ yes ≤ buy_max` (ברירת מחדל ~0.55–0.70), או הרחבה במומנטום (ראו למטה) |
| לא ב־blacklist | מזהה השוק לא ב־`runtime_config` / blacklist יומי |
| anti-churn | אין cooldown פעיל אחרי יותר מדי stop-loss ברצף (לפי `churn_*`) |
| פער מול מקום שני | אם `enable_competition_filter`: ההפרש בין מחיר ה־YES של המועמד לבין ה־YES הגבוה ביותר בשווקים אחים (אותו Gamma event) ≥ `min_lead_over_runner_up` |

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

- **כניסה:** ניסיון לקנות YES כשהשוק “סביר” (לא זול מדי / לא יקר מדי לפי סף), עם מגבלות סיכון (גודל עסקה, תחרות מול תוצאות אחרות באותו אירוע, blacklist, cooldown).
- **יציאה:** שילוב של  
  - **take-profit** כשהסתברות/סימון שוק קרובים ל־1 (נעילת רווח),  
  - **stop-loss** כשהשוק חלש **וגם** אתה מתחת לכניסה (מגן מפני המשך הפסד; כולל מצב resolved NO עם mark≈0),  
  - **מומנטום** (אופציונלי) לכניסה אגרסיבית יותר ויציאה כשמתחרה באירוע קורס,  
  - **claim** כשהשוק נסגר/הוכרע.
- **אין מודל חיזוי מזג אוויר עצמאי** — הבוט משתמש במחירי השוק (Gamma + Data API) ובחוקים קבועים מראש, לא בניתוח מטאורולוגי חיצוני.

---

## קבצים רלוונטיים בקוד

| נושא | קובץ |
|------|------|
| סדר לוגיקה לשוק | `strategy/trades.py` → `process_single_market` |
| סטופ / TP | `strategy/probability.py` |
| התאמת פוזיציה לשוק Gamma | `strategy/market_match.py` |
| סנכרון מחירים | `strategy/sync_portfolio.py` |
| ספים וריצה | `config/constants.py`, `config/settings.py`, `strategy/bot_runner.py` |

למגבלות קצב API ראו [POLY_RATE_LIMITS.MD](POLY_RATE_LIMITS.MD).
