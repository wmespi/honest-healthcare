"""The routes the real find-care screen calls, in the shape application/serving/contract.py
sets, over the find-care serving tables (#135), each under /api (main.py):

  /plans       every plan a person can pick
  /services    every service, with what the screen says about it
  /find-care   one service on one plan near one ZIP code, one row per place

Every rule about who is on the plan and what a price is lives in the build, so
these read the tables as they are. The contract sorts the results and works out
the cheapest, the closest and the median.
"""
from typing import Annotated, Optional

from fastapi import APIRouter, HTTPException, Query

from ..contract import RADIUS_OPTIONS_MI, FindCare, FindCareQuery, Plan, Problem, Service, plan_order
from ..data_sources import (
    COST_SHARING_SRC, PLACES_SRC, PLANS_SRC, PRICES_SRC, PROVIDERS_SRC, SERVICES_SRC, ZIPS_SRC, db,
)

router = APIRouter()

# With no radius asked for, a search takes the tightest one with at least this many places.
ENOUGH_PLACES = 5
EARTH_RADIUS_MI = 3958.7613


def haversine_sql(lat: str, lon: str, lat0: str, lon0: str) -> str:
    """Great-circle distance in miles between two points, as SQL over the
    columns or values named."""
    return (f"{2 * EARTH_RADIUS_MI} * asin(sqrt("
            f"pow(sin(radians({lat} - {lat0}) / 2), 2) + "
            f"cos(radians({lat0})) * cos(radians({lat})) * pow(sin(radians({lon} - {lon0}) / 2), 2)))")


def _rows(cursor):
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _one(conn, sql, params):
    rows = _rows(conn.execute(sql, params))
    return rows[0] if rows else None


def _not_found(code, message):
    return HTTPException(404, detail=Problem(code=code, message=message).model_dump())


@router.get("/plans", response_model=list[Plan])
def get_plans():
    plans = [Plan(**row) for row in _rows(db().execute(
        f"SELECT plan_id, name, carrier, metal_level, needs_referral, deductible FROM {PLANS_SRC}"))]
    return sorted(plans, key=plan_order)


@router.get("/services", response_model=list[Service])
def get_services():
    return _rows(db().execute(f"""
        SELECT service_id, name, keywords, about, could_change, asks, preventive_note,
               medicare_rate, medicare_facility_rate
        FROM {SERVICES_SRC} ORDER BY name
    """))


def priced(place) -> bool:
    """Whether a place has a most you'd pay: every part has a price."""
    return all(p["max"] is not None for p in place["parts"])


def radius_for(places, asked: Optional[int]) -> int:
    """The radius asked for, or the tightest with ENOUGH_PLACES, or the widest."""
    if asked is not None:
        return asked
    return next((r for r in RADIUS_OPTIONS_MI
                 if sum(p["distance_mi"] <= r for p in places) >= ENOUGH_PLACES), RADIUS_OPTIONS_MI[-1])


def wider_radius(places, results) -> Optional[int]:
    """When no result has a most you'd pay, the smallest radius that reaches the
    nearest place with one, or with no results, the nearest place. `places` are
    nearest first."""
    if any(priced(p) for p in results):
        return None
    target = next((p for p in places if priced(p)), None) if results else next(iter(places), None)
    if target is None:
        return None
    return next((r for r in RADIUS_OPTIONS_MI if target["distance_mi"] <= r), None)


@router.get("/find-care", response_model=FindCare, responses={404: {"model": Problem}})
def find_care(q: Annotated[FindCareQuery, Query()]):
    conn = db()
    service = _one(conn, f"SELECT service_category FROM {SERVICES_SRC} WHERE service_id = ?", [q.service_id])
    if service is None:
        raise _not_found("unknown_service", f"There's no service {q.service_id!r}.")
    plan = _one(conn, f"SELECT network_id FROM {PLANS_SRC} WHERE plan_id = ?", [q.plan_id])
    if plan is None:
        raise _not_found("unknown_plan", f"There's no plan {q.plan_id!r}.")
    origin = _one(conn, f"SELECT zip, lat, lon FROM {ZIPS_SRC} WHERE zip = ?", [q.zip])
    if origin is None:
        raise _not_found("unknown_zip", f"There's no ZIP code {q.zip}.")
    cost_sharing = _one(conn, f"""
        SELECT copay, coinsurance, after_deductible FROM {COST_SHARING_SRC}
        WHERE plan_id = ? AND service_category = ?
    """, [q.plan_id, service["service_category"]])

    places = _rows(conn.execute(f"""
        WITH o AS (SELECT ?::DOUBLE AS lat, ?::DOUBLE AS lon)
        SELECT l.place_id, l.name, l.kind, l.address, l.city, l.lat, l.lon, l.phone, l.stars,
               {haversine_sql("l.lat", "l.lon", "o.lat", "o.lon")} AS distance_mi,
               p.parts, p.several_rates, p.billers, p.providers
        FROM {PRICES_SRC} p JOIN {PLACES_SRC} l USING (place_id) CROSS JOIN o
        WHERE p.network_id = ? AND p.service_id = ?
        ORDER BY distance_mi, l.place_id
    """, [origin["lat"], origin["lon"], plan["network_id"], q.service_id]))
    radius = radius_for(places, q.radius_mi)
    results = [p for p in places if p["distance_mi"] <= radius]
    closest_outside = places[0] if places and not results else None

    shown = results + ([closest_outside] if closest_outside else [])
    npis = sorted({npi for p in shown for npi in p["providers"]})
    providers = {r["npi"]: r for r in _rows(conn.execute(
        f"SELECT npi, name, specialty, score FROM {PROVIDERS_SRC} WHERE list_contains(?::BIGINT[], npi)", [npis]))}

    def place(p):
        return {**p, "distance_mi": round(p["distance_mi"], 1),
                "providers": sorted((providers[n] for n in p["providers"]), key=lambda v: (v["name"], v["npi"]))}

    return FindCare(service_id=q.service_id, plan_id=q.plan_id, origin=origin, radius_mi=radius, sort=q.sort,
                    cost_sharing=cost_sharing, results=[place(p) for p in results],
                    wider_radius_mi=wider_radius(places, results),
                    closest_outside=place(closest_outside) if closest_outside else None)
