"""The find-care API contract: the typed shape of every route the real screen calls.

  GET /api/plans       every plan a person can pick
  GET /api/services    every service, with what the screen says about it
  GET /api/find-care   one service on one plan near one ZIP code, one row per place

FastAPI publishes these models at /api/docs and /api/openapi.json and checks every
response against them. The routes read the serving tables on #135 as they are,
so every rule (who is on the plan, what a price is, how places sort) lives in
the build or here, never in the screen. Money is US dollars to the cent, and
distance is straight-line miles to one decimal place.
"""
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, BeforeValidator, Field, computed_field, model_validator

# "hospital" is the one place kind the contract and the screen treat specially;
# every other kind, and every bill's name, comes from the build's tables.
Sort = Literal["price", "distance"]
RadiusMi = Literal[5, 10, 25, 50]
RADIUS_OPTIONS_MI = (5, 10, 25, 50)
# The exchange's metal tiers, the richest coverage first: the order a plan list runs in.
MetalTier = Literal["Platinum", "Gold", "Silver", "Bronze", "Catastrophic"]
METAL_TIERS = ("Platinum", "Gold", "Silver", "Bronze", "Catastrophic")


class Problem(BaseModel):
    """The body of every 4xx this API sends on purpose, and of a 500, as
    FastAPI's `detail`."""
    code: Literal["unknown_service", "unknown_plan", "unknown_zip", "internal_error"]
    message: str


class Plan(BaseModel):
    """One thing a person can pick, priced by one contract. A person picks their
    carrier, then their plan by its tier and deductible, which they're likeliest
    to know, with the detail that tells two such plans apart."""
    plan_id: str
    name: str
    carrier: str
    metal_level: str = Field(description="The exchange's metal level, such as Gold or Expanded Bronze.")
    needs_referral: bool
    deductible: float = Field(ge=0, description="The in-network deductible for one person.")

    @computed_field(description="The metal level's tier: an Expanded Bronze plan is a Bronze plan.")
    @property
    def tier(self) -> MetalTier:
        return self.metal_level.split()[-1]

    @computed_field(description="The name without the carrier, the tier and the deductible, which are shown "
                                "on their own; what's left tells plans apart, such as their network and copays.")
    @property
    def detail(self) -> str:
        words = self.name.split()
        for word in (self.carrier.split()[0], self.tier, f"{self.deductible:.0f}"):
            if word in words:
                words.remove(word)
        return " ".join(words)


def plan_order(plan: Plan):
    """Plans by tier, the richest first, then the lowest deductible."""
    return (METAL_TIERS.index(plan.tier) if plan.tier in METAL_TIERS else len(METAL_TIERS), plan.deductible, plan.name)


class CostSharing(BaseModel):
    """What the plan charges the member for a service in network, as its
    standard version does. With both a copay and a coinsurance, the member pays
    the copay and then the coinsurance."""
    copay: Optional[float] = Field(ge=0)
    coinsurance: Optional[float] = Field(ge=0, le=100, description="A percent of the rate.")
    after_deductible: bool = Field(description="The member pays the whole rate until the deductible is met.")

    @model_validator(mode="after")
    def _charges_something(self):
        if self.copay is None and self.coinsurance is None:
            raise ValueError("a share has a copay, a coinsurance or both")
        return self


class Share(CostSharing):
    """What the plan charges for one benefit the service is billed under, and
    which of the service's bills fall under it."""
    benefit: str
    label: str = Field(description="What the plan's summary calls the benefit, such as a specialist visit.")
    parts: list[str] = Field(min_length=1, description="The service's bills under this benefit.")


class Family(BaseModel):
    """A group of services that the service picker browses by."""
    family: str
    name: str


class Service(BaseModel):
    """A service someone can search for, and what the screen says about it."""
    service_id: str = Field(description="Our own slug, such as `mri-lower-back`.")
    family: str = Field(description="The `family` of the group it belongs to.")
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


class Catalog(BaseModel):
    """Every service, and the families they are grouped in."""
    families: list[Family] = Field(description="In the order the picker lists them.")
    services: list[Service] = Field(description="By name.")


class Part(BaseModel):
    """One bill at a place, with the rate the plan lists for it there that has
    the strongest evidence behind it, and the highest of those when several tie,
    since the file doesn't say which applies. Empty when the place has no usable
    price for that bill: the reading at a hospital no reading group on the plan
    is placed at, or the scan at a group whose doctors read scans done
    elsewhere. A hospital's own fee is priced where the plan's file or the
    hospital's own price list gives it, and `source` says which."""
    part: str = Field(description="The bill's name, such as `all_in`, `scan`, `reading` or `facility`.")
    label: str
    billing_class: Literal["professional", "institutional"]
    max: Optional[float]
    source: Optional[Literal["plan", "hospital"]] = Field(
        description="Where the rate comes from: the plan's own file, or a hospital's own price list. "
                    "Empty exactly when there is no rate.")
    missing_note: Optional[str] = Field(description="Said of a part with no rate: what is missing.")
    missing_explain: Optional[str] = Field(description="Said of a part with no rate: why, and who bills it.")

    @model_validator(mode="after")
    def _source_goes_with_a_rate(self):
        if (self.source is None) != (self.max is None):
            raise ValueError("a part has a source exactly when it has a rate")
        return self

    @model_validator(mode="after")
    def _a_rate_has_nothing_missing(self):
        if self.max is not None and (self.missing_note is not None or self.missing_explain is not None):
            raise ValueError("a part with a rate has no missing text")
        return self


class Provider(BaseModel):
    """A clinician Medicare shows billing the service at a place on the plan,
    so someone with a referral can find the place by their name."""
    npi: int
    name: str
    specialty: str
    score: Optional[float] = Field(description="Medicare's clinician quality score (MIPS), 0 to 100.")


class Place(BaseModel):
    """A practice or hospital at one address that does the service on the plan.

    The most you'd pay is its parts added up, and it's empty when any part has
    no price, because then nobody can say what the whole bill could be.

    A hospital is listed when the groups reading scans there are on the plan
    or when it has its own fee. Only where the fee is empty does nothing on
    file confirm the hospital itself is in network.
    """
    place_id: str = Field(description="The CCN for a hospital, the lowest billing NPI for a practice.")
    name: str
    kind: str
    kind_name: str = Field(description="What to call the kind of place, such as Imaging center.")
    address: str
    city: str
    lat: float
    lon: float
    phone: Optional[str]
    stars: Optional[int] = Field(ge=1, le=5, description="Medicare's hospital star rating. Hospitals only.")
    distance_mi: float = Field(ge=0)
    parts: list[Part] = Field(min_length=1)
    several_rates: bool = Field(
        description="The plan lists more than one rate that could apply to a bill here without saying which, "
                    "so someone should ask which they'd get.")
    billers: list[str] = Field(description="The groups that bill for the service here, by name: "
                                           "at a hospital, the groups reading scans there. Empty at a hospital "
                                           "no reading group on the plan is placed at.")
    cash_price: Optional[float] = Field(
        ge=0, description="What the hospital's own price list says it charges for the service without "
                          "insurance. Hospitals only.")
    providers: list[Provider] = Field(
        description="Who Medicare shows billing the service here, never the whole roster. "
                    "Empty when Medicare shows no one, as at an imaging center it has no record of.")

    @model_validator(mode="after")
    def _stars_are_for_hospitals(self):
        if self.stars is not None and self.kind != "hospital":
            raise ValueError("only a hospital has stars")
        return self

    @model_validator(mode="after")
    def _cash_prices_are_for_hospitals(self):
        if self.cash_price is not None and self.kind != "hospital":
            raise ValueError("only a hospital has a cash price")
        return self

    @computed_field(description="The most you'd pay: every part's rate, added up.")
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


def _whole_number(v):
    """A query string's radius is text, such as "10", which a Literal won't read."""
    return int(v) if isinstance(v, str) and v.isdigit() else v


class FindCareQuery(BaseModel):
    """GET /api/find-care's query string."""
    service_id: str
    plan_id: str
    zip: str = Field(pattern=r"^\d{5}$")
    radius_mi: Optional[Annotated[RadiusMi, BeforeValidator(_whole_number)]] = Field(
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
    cost_sharing: list[Share] = Field(
        min_length=1, description="What the plan charges the member for the service: one share per benefit "
                                  "its bills fall under, in the order the bills come.")
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
