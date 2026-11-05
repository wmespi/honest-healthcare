"""The find-care API contract: the typed shape of every route the real screen calls.

  GET /plans       every plan a person can pick
  GET /services    every service, with what the screen says about it
  GET /find-care   one service on one plan near one ZIP code, one row per place

FastAPI publishes these models at /docs and /openapi.json and checks every
response against them. The routes read the serving tables on #135 as they are,
so every rule (who is on the plan, what a price is, how places sort) lives in
the build or here, never in the screen. Money is US dollars to the cent, and
distance is straight-line miles to one decimal place.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field, computed_field, model_validator

# A bill behind a price. A practice files one all-in rate, or the scan and the
# reading separately; a hospital also bills its own facility fee.
PartName = Literal["all_in", "scan", "reading", "facility"]
PlaceKind = Literal["hospital", "imaging_center", "practice"]
Sort = Literal["price", "distance"]
RadiusMi = Literal[5, 10, 25, 50]
RADIUS_OPTIONS_MI = (5, 10, 25, 50)


class Problem(BaseModel):
    """The body of every 4xx this API sends on purpose, as FastAPI's `detail`."""
    code: Literal["unknown_service", "unknown_plan", "unknown_zip"]
    message: str


class Plan(BaseModel):
    """One thing a person can pick, priced by one contract."""
    plan_id: str
    name: str
    carrier: str
    needs_referral: bool


class Service(BaseModel):
    """A service someone can search for, and what the screen says about it."""
    service_id: str = Field(description="Our own slug, such as `mri-lower-back`.")
    name: str
    keywords: str = Field(description="Other words the service picker matches.")
    about: str
    could_change: list[str] = Field(description="What could make the bill differ from the price shown.")
    asks: list[str] = Field(description="Questions to ask before booking.")
    preventive_note: Optional[str] = Field(
        description="Shown first when the service is usually free as preventive care.")
    medicare_rate: Optional[float] = Field(description="What Medicare pays for the whole service in an office.")
    medicare_facility_rate: Optional[float] = Field(
        description="What Medicare pays a hospital for its part: a labelled estimate, never part of a price.")


class Part(BaseModel):
    """One bill at a place, with the highest rate the plan lists for it there,
    since the file doesn't say which applies. Empty when the place has no usable
    price for that bill: a hospital's own fee, or the scan at a group whose
    doctors read scans done elsewhere."""
    part: PartName
    max: Optional[float]


class Provider(BaseModel):
    """A clinician Medicare shows billing a service priced at a place on the
    plan, so someone with a referral can find the place by their name."""
    npi: int
    name: str
    specialty: str
    score: Optional[float] = Field(description="Medicare's clinician quality score (MIPS), 0 to 100.")


class Place(BaseModel):
    """A practice or hospital at one address that does the service on the plan.

    The most you'd pay is its parts added up, and it's empty when any part has
    no price, because then nobody can say what the whole bill could be.

    A hospital is listed because the groups reading scans there are on the
    plan. Nothing on file confirms the hospital itself is, so the screen says
    so for every hospital.
    """
    place_id: str = Field(description="The CCN for a hospital, the lowest billing NPI for a practice.")
    name: str
    kind: PlaceKind
    address: str
    city: str
    lat: float
    lon: float
    phone: Optional[str]
    stars: Optional[int] = Field(ge=1, le=5, description="Medicare's hospital star rating. Hospitals only.")
    distance_mi: float = Field(ge=0)
    parts: list[Part] = Field(min_length=1)
    several_rates: bool = Field(
        description="The plan lists more than one rate for a bill here without saying which applies, "
                    "so someone should ask which they'd get.")
    billers: list[str] = Field(min_length=1, description="Who bills for the service here.")
    providers: list[Provider]

    @model_validator(mode="after")
    def _stars_are_for_hospitals(self):
        if self.stars is not None and self.kind != "hospital":
            raise ValueError("only a hospital has stars")
        return self

    @computed_field(description="The most you'd pay: every part's highest rate, added up.")
    @property
    def max_rate(self) -> Optional[float]:
        if any(p.max is None for p in self.parts):
            return None
        return round(sum(p.max for p in self.parts), 2)


class Origin(BaseModel):
    """Where the search measures from: the center of the ZIP code."""
    zip: str = Field(pattern=r"^\d{5}$")
    lat: float
    lon: float


class FindCareQuery(BaseModel):
    """GET /find-care's query string."""
    service_id: str
    plan_id: str
    zip: str = Field(pattern=r"^\d{5}$")
    radius_mi: Optional[RadiusMi] = Field(
        None, description="Leave it out to get the tightest radius with at least 5 places.")
    sort: Sort = "price"


def _by_price(p):
    return (p.max_rate is None, p.max_rate or 0, p.distance_mi, p.place_id)


def _by_distance(p):
    return (p.distance_mi, p.max_rate is None, p.max_rate or 0, p.place_id)


class FindCare(BaseModel):
    """Every place within `radius_mi` that does the service on the plan.

    The results come back in `sort` order whatever order they were built in:
    `price` puts the lowest `max_rate` first and places without one last, and
    `distance` puts the nearest first. Either breaks a tie on the other. The
    cheapest, the closest and the median come from the results, so they can't
    disagree with them.
    """
    service_id: str
    plan_id: str
    origin: Origin
    radius_mi: RadiusMi = Field(description="The radius asked for, or the one chosen when none was.")
    sort: Sort
    results: list[Place]
    wider_radius_mi: Optional[RadiusMi] = Field(
        description="When no result has a `max_rate`, the smallest radius that reaches the nearest place with one, "
                    "or with no results at all, the nearest place. Empty when that is beyond every radius.")
    closest_outside: Optional[Place] = Field(
        description="When there are no results: the nearest place on the plan that does the service.")

    @model_validator(mode="after")
    def _in_order(self):
        self.results.sort(key=_by_price if self.sort == "price" else _by_distance)
        priced = any(p.max_rate is not None for p in self.results)
        if self.closest_outside is not None and self.results:
            raise ValueError("closest_outside is only for a search with no results")
        if self.wider_radius_mi is not None and priced:
            raise ValueError("wider_radius_mi is only for a search with no priced result")
        return self

    @computed_field(description="The result with the lowest `max_rate`, nearest on a tie.")
    @property
    def cheapest_place_id(self) -> Optional[str]:
        cheapest = min(self.results, key=_by_price, default=None)
        return cheapest.place_id if cheapest and cheapest.max_rate is not None else None

    @computed_field(description="The nearest result, cheapest on a tie.")
    @property
    def closest_place_id(self) -> Optional[str]:
        closest = min(self.results, key=_by_distance, default=None)
        return closest.place_id if closest else None

    @computed_field(description="The median `max_rate` across the results that have one.")
    @property
    def median_max_rate(self) -> Optional[float]:
        rates = sorted(p.max_rate for p in self.results if p.max_rate is not None)
        if not rates:
            return None
        mid = len(rates) // 2
        return rates[mid] if len(rates) % 2 else round((rates[mid - 1] + rates[mid]) / 2, 2)
