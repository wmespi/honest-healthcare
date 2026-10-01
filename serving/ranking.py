"""The blended provider ranking for a service-line search (serving.md "Ranking").

Each component is a within-candidate-set percentile, so a $5 gap or a 1-mile gap
means "the next-cheapest / next-nearest", never an absolute unit mix-up.
Components that don't apply to a request (no plan -> no cost; no ZIP -> no
distance) drop out and the remaining weights renormalise. A provider with no
MIPS score scores a neutral 0.5 on quality; `years_in_practice` is shown, never scored.
"""
WEIGHTS = {"cost": 0.5, "distance": 0.3, "quality": 0.2}
NEUTRAL_QUALITY = 0.5
EARTH_RADIUS_MI = 3958.7613


def haversine_sql(lat: str, lon: str, lat0: str, lon0: str) -> str:
    return (f"{2 * EARTH_RADIUS_MI} * asin(sqrt("
            f"pow(sin(radians({lat} - {lat0}) / 2), 2) + "
            f"cos(radians({lat0})) * cos(radians({lat})) * pow(sin(radians({lon} - {lon0}) / 2), 2)))")


def pct_good_sql(col: str, *, best_high: bool = False) -> str:
    """0..1, 1 = best among rows where `col` is not NULL; NULL where `col` is NULL."""
    order = f"{col} DESC NULLS LAST" if best_high else f"{col} ASC NULLS LAST"
    return (f"CASE WHEN {col} IS NULL THEN NULL ELSE 1 - COALESCE("
            f"(RANK() OVER (ORDER BY {order}) - 1) / NULLIF(COUNT({col}) OVER () - 1, 0), 0) END")


def score_sql(has_cost: bool, has_distance: bool) -> str:
    """SQL for `rank_score` (0..1, higher is better) over columns cost_good /
    distance_good / quality_good produced by `pct_good_sql`."""
    parts, total = [], WEIGHTS["quality"]
    parts.append(f"{WEIGHTS['quality']} * COALESCE(quality_good, {NEUTRAL_QUALITY})")
    if has_cost:
        parts.append(f"{WEIGHTS['cost']} * COALESCE(cost_good, 0)")
        total += WEIGHTS["cost"]
    if has_distance:
        parts.append(f"{WEIGHTS['distance']} * COALESCE(distance_good, 0)")
        total += WEIGHTS["distance"]
    return f"({' + '.join(parts)}) / {total}"
