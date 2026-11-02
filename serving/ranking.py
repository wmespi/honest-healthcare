"""Great-circle distance in SQL, for ordering results by how near they are."""
EARTH_RADIUS_MI = 3958.7613


def haversine_sql(lat: str, lon: str, lat0: str, lon0: str) -> str:
    return (f"{2 * EARTH_RADIUS_MI} * asin(sqrt("
            f"pow(sin(radians({lat} - {lat0}) / 2), 2) + "
            f"cos(radians({lat0})) * cos(radians({lat})) * pow(sin(radians({lon} - {lon0}) / 2), 2)))")
