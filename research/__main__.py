"""python -m research [analyze] [...]"""

import sys


def main() -> int:
    rest = sys.argv[1:]
    if rest and rest[0] == "analyze":
        from research.analyze_calibration import run_analyze

        return run_analyze(rest[1:])
    if rest and rest[0] == "summary":
        from research.event_summary import run_event_summary

        return run_event_summary(rest[1:])
    if rest and rest[0] in ("export-cities", "export_cities"):
        from research.city_csv_export import run_export_cities_main

        return run_export_cities_main(rest[1:])
    if rest and rest[0] in ("verify-open-meteo", "verify_open_meteo"):
        from research.verify_open_meteo import run_verify

        return run_verify(rest[1:])
    if rest and rest[0] in ("verify-cities", "verify_cities"):
        from research.cities_catalog import (
            load_research_city_keys,
            verify_all_cities_have_coordinates,
        )

        keys = load_research_city_keys()
        bad = verify_all_cities_have_coordinates(keys)
        if bad:
            print("CRITICAL missing coords/tz:")
            for b in bad:
                print(" ", b)
            return 1
        print(f"OK: {len(keys)} cities have CITY_TZ + CITY_LAT_LON")
        return 0
    from research.run_backfill import run_backfill_main

    return run_backfill_main(rest)


if __name__ == "__main__":
    raise SystemExit(main())
