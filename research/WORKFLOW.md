# מחקר מזג אוויר — סדר עבודה וקבצים

מסמך קצר: מה להריץ, לאן נכתב, ומה המשמעות של כל שכבת דאטה.

---

## 1) איסוף נתונים (שלב ראשון)

### מה להריץ (לפי סדר)

```bash
# מומלץ קודם עשן (מהיר + לוגים):
python -m research.run_backfill --quick-test --start-date 2026-04-14 --end-date 2026-04-14 -v
#########################################3





# אחרי שזה נראה טוב — איסוף רחב יותר:
python -m research.run_backfill -v --start-date 2026-03-15 --end-date 2026-04-13   --search-pages 40 --max-markets 5000 --refresh-outcomes   --workers 10 --progress-every 50 2>&1 | tee /tmp/backfill_30d.log

python -m research.run_backfill -v --start-date 2026-01-01 --end-date 2026-04-13 \
  --search-pages 40 --max-markets 5000 --refresh-outcomes \
  --workers 30 --progress-every 50 --cached \
  2>&1 | tee /tmp/backfill_30d.log

# אותו דבר בלי תאריכים ידניים — 7 ימים אחרונים (כולל היום), סוף החלון = היום:
python -m research.run_backfill -v --back-days 7 \
  --search-pages 40 --max-markets 5000 --cached \
  --workers 10 --progress-every 50 \
  2>&1 | tee /tmp/backfill_week.log

# סוף חלון אחר (לא היום) + 7 ימים אחורה כוללים עד התאריך הזה:
python -m research.run_backfill -v --back-days 7 --end-date 2026-04-13 \
  --search-pages 40 --max-markets 5000 --workers 10 --progress-every 50

 Backfill: --cached ו־--skip-gamma-search (ראה למטה)

# סטטיסטיקות: תחזית מול אמת (bias, MAE), לפי מודל ולפי עיר; + מדד קהל מול outcomes
python -m research analyze
# דוח ליום אירוע אחד: אמת, תחזיות, מחירי Gamma, והסתברויות מודל גסות לברקטים
python -m research summary --date 2026-04-13

python -m research summary --start-date 2026-01-01 --end-date 2026-04-13 \
  --city "tel aviv" --city "new york city"


#####################



- **`--dry-run`** — רק סריקת Gamma + פרסור, **בלי** כתיבה לדיסק ובלי קריאות WU/Open-Meteo (לבדיקת זרימה). (ראה למטה)
- **`--skip-truth`** — דילוג על משיכת אמת (WU/IBM), עדיין תחזיות Open-Meteo + outcomes (אם לא dry-run).

### לאן נכתב מה (אחרי ריצה רגילה, לא dry-run)

| קובץ | תוכן | פורמט |
|------|------|--------|
| `data/research/resolution_registry.json` | לכל `condition_id`: סוג מקור, `truth_url`, מזהים; טקסט מקור מקוצר ב־`rsrc_snip` (לא כל ה־description) | JSON אחד (מוחלף בכל ריצה מלאה של registry) |
| `data/research/resolution_overrides.json` | תיקונים ידניים לפי `condition_id` או `market_id` (למשל `truth_url` חסר) | JSON |
| `data/research/truth_daily.jsonl` | שורה לכל **יום–עיר** עם מקסימום יומי °C (אמת), מקור (`weathercom_30day` / `open_meteo_archive`), סטטוס | JSONL (append; מפתח idempotency בפנים) |
| `data/research/forecasts_history.jsonl` | לכל `(city, date, model)` ערך `temp_max_c` מ־Open-Meteo (forecast + historical-forecast) | JSONL |
| `data/research/market_outcomes.jsonl` | **צילום מצב השוק ב־Gamma** לכל ברקט — ראו סעיף 3 וטבלת שדות למטה | JSONL |
| `data/research/daily_runs.jsonl` | לוג קצר לכל הרצת backfill (ספירות, חלון תאריכים) | JSONL |

---

## 2) חישובים / כיול / דוח אירוע

### מה להריץ

```bash
# סטטיסטיקות שגיאת תחזית מול אמת (bias, MAE, לפי מודל ועיר):
python -m research analyze

# דוח מרוכז ליום אירוע אחד (עירות, מוביל קהל, מול אמת אם קיימת):
python -m research summary --date 2026-04-14
```

### לאן נכתב

| פלט | תוכן |
|-----|------|
| `data/research/calibration_latest.json` | פלט `analyze`: `models.*.bias_c`, `mae_c`, `n`; `by_city`; מדד `crowd` (מוביל לפי מחיר YES מול אמת) |
| `data/research/event_summary_YYYY-MM-DD.json` | פלט `summary --date`: אירועים, מוביל, תחזיות, השוואה |
| `data/research/event_summary_latest.json` | עותק אחרון של ה־summary (אותו תוכן כמו הדאטד) |

### הסתברות “רכה” (מודל נקודתי → P לברקט)

- הקוד ב־`research/probability_from_forecast.py` לוקח תחזית נקודתית °C + רעש גאוסי (~2°C) ומחשב **הסתברות משוערת ל־YES** לברקט שמפורש מכותרת השאלה.
- משמש בעיקר ב־`event_summary` (עמודות `model_p_*`), לא בקבצי JSONL הגולמיים.

---

## 3) `market_outcomes.jsonl` — זה **לא** ההימורים שלך

- **`gamma_yes_p`** (בעבר גם `yes_price` לתאימות לאחור) = מחיר/הסתברות **YES של אותו שוק בפולימרקט** כפי שמוחזר מ־**Gamma** (`outcomePrices[0]`) ברגע ה־backfill.
- זה **לא** גודל פוזיציה שלך, לא Data API של החשבון, ולא “מה ניצח” בפועל אלא אם השוק כבר נסגר ואז המחיר קרוב ל־0 או ל־1.
- **`market_closed`**: האם השוק מסומן סגור/נפתר ב־Gamma.
- **`truth_max_c` / `fits`**: מול אמת שחולצה ל־`(city_key, event_date)` אם קיימת בשלב האיסוף; אם עדיין אין אמת לתאריך — יהיו `null`.

כלומר: הקובץ מתאר **את השווקים בפולימרקט** (מחירים ומצב), לא את הפורטפוליו של הבוט.

---

## 4) הבוט בזמן מסחר (אחרי שיש כיול)

- `forecast/forecast_service.py` קורא `data/research/calibration_latest.json` (אם קיים) ומתקן bias על consensus.
- `data/runtime_config.json`: `research_exit_on_model_flip` ליציאה כשהמודל “הופך” מול הברקט (אופציונלי).

---

## 5) קיצור קבצים וקריאה מהירה

ריצות backfill חדשות כותבות רשומות **דקות יותר**. שורות ישנות ב־JSONL עשויות להשתמש בשמות ארוכים (`question`, `yes_price`, …) — הקוד ב־`research/outcome_fields.py` תומך בשני הפורמטים.

### שדות מומלצים ב־`market_outcomes.jsonl` (ריצות חדשות)

| שדה | משמעות |
|-----|--------|
| `gamma_yes_p` | מחיר/הסתברות YES מ־Gamma (`outcomePrices[0]`) — **לא** גודל ההימור שלך |
| `q` | תחילית קצרה של שאלת השוק (עד ~120 תווים) |
| `closed` | האם השוק סגור ב־Gamma |
| `fits` | האם הברקט הזה תואם ל־`truth_max_c` (אם יש אמת) |
| `truth_max_c` | ערך אמת °C אם היה זמין בזמן הכתיבה |

### שדות ב־`truth_daily.jsonl` (ריצות חדשות)

ללא `wu_page_html` / `weathercom_api_key_found`; נשארים `truth_max_c`, `status`, `truth_source`, `geocode` (אם יש), `wu_url`.

### שדות ב־`forecasts_history.jsonl`

`tz` במקום `tz_name` (שורות ישנות עדיין עם `tz_name` — תקין).

---

## 6) ריצה נקייה מההתחלה (אופציונלי)

```bash
rm -f data/research/truth_daily.jsonl data/research/forecasts_history.jsonl \
      data/research/market_outcomes.jsonl data/research/daily_runs.jsonl
# השאר resolution_registry אם רוצים; אחרת מחק גם אותו לפני ריצה מלאה
```
