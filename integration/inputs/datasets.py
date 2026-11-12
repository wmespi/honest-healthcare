"""The datasets the build reads, which ingestion writes under data/ingestion/:
the contract between the two layers (docs/schema.md). Each states its path in
that folder, the command that makes it, the columns the build reads, with their DuckDB types, and its key, if it
has one. `check_inputs` stops the build unless every dataset has those columns
with those types, and a key that's never empty or repeated. A dataset may have
more columns than these."""
import glob


class Dataset:
    """Parquet files under the data directory, and the command that makes them."""

    def __init__(self, path, made_by, columns, key=(), options=""):
        self.path, self.made_by, self.columns, self.key, self.options = path, made_by, columns, list(key), options

    def read(self, data_dir):
        """A DuckDB table expression for the dataset, or stop the build with the
        command that makes it."""
        path = f"{data_dir}/{self.path}"
        if not glob.glob(path):
            raise SystemExit(f"no {path}: run `{self.made_by}`")
        return f"read_parquet('{path}'{self.options})"

    def check(self, con, data_dir):
        """Stop the build unless the dataset has its columns and its key holds."""
        source = self.read(data_dir)
        got = {c: t for c, t, *_ in con.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()}
        for column, kind in self.columns.items():
            if got.get(column) != kind:
                found = f"is {got[column]}" if column in got else "is missing"
                raise SystemExit(f"{self.path}: column {column} {found}, but the build reads it as {kind} "
                                 f"(integration/inputs/datasets.py); run `{self.made_by}`")
        if not self.key:
            return
        cols = ", ".join(self.key)
        empty = con.execute(f"SELECT count(*) FROM {source} WHERE "
                            + " OR ".join(f"{c} IS NULL" for c in self.key)).fetchone()[0]
        if empty:
            raise SystemExit(f"{self.path}: {empty} rows with an empty key ({cols})")
        dup = con.execute(f"SELECT {cols} FROM {source} GROUP BY {cols} HAVING count(*) > 1 LIMIT 3").fetchall()
        if dup:
            raise SystemExit(f"{self.path}: the key ({cols}) repeats, for example {dup}")


def check_inputs(con, data_dir):
    """Check every dataset, before the build reads any of them."""
    for dataset in DATASETS:
        dataset.check(con, data_dir)


V, I, B, D, F = "VARCHAR", "INTEGER", "BIGINT", "DOUBLE", "BOOLEAN"

# Every payer's rates by Georgia exchange network, which `make parse` writes, one
# folder per payer (data/ingestion/payer_rates/<payer>/). A rate's group set lists the
# provider groups it covers, and a group's roster lists its doctors (`npi`) and its
# billing ID (`tin_value`, an NPI or EIN by `tin_type`). The columns are CMS's
# Transparency in Coverage names, so every payer's rates read alike.
PAYER_RATES = Dataset("payer_rates/*/rates.parquet", "make parse", {
    "network_id": V, "group_set_id": B, "billing_code_type": V, "billing_code": V,
    "negotiation_arrangement": V, "negotiated_type": V, "negotiated_rate": D, "expiration_date": V,
    "service_code": V, "billing_class": V, "modifier": V, "setting": V})
PAYER_RATE_GROUPS = Dataset("payer_rates/*/rate_groups.parquet", "make parse", {
    "network_id": V, "group_set_id": B, "provider_group_id": B},
    key=["network_id", "group_set_id", "provider_group_id"])
PAYER_ROSTERS = Dataset("payer_rates/*/rosters.parquet", "make parse", {
    "network_id": V, "provider_group_id": B, "npi": B, "tin_type": V, "tin_value": V})
# Each target network the payer's rates are for, with or without rates: `status` says
# they're from the payer's latest copy of `file` (`current`), from an earlier copy
# (`stale`), or that it has none (`missing`), `file_updated_at` when the payer updated
# the copy they're from, and `reason` why they aren't current.
PAYER_NETWORKS = Dataset("payer_rates/*/networks.parquet", "make parse", {
    "network_id": V, "status": V, "file": V, "file_updated_at": "TIMESTAMP WITH TIME ZONE", "reason": V},
    key=["network_id"])
# Georgia's exchange networks (ingestion/reference/exchange-plans.md).
EXCHANGE_NETWORKS = Dataset("reference/exchange_networks_ga.parquet", "make reference STEP=exchange-plans", {
    "network_id": V, "issuer_id": V, "name": V}, key=["network_id"])
# Georgia's exchange plans, each with its network_id (ingestion/reference/exchange-plans.md).
EXCHANGE_PLANS = Dataset("reference/exchange_plans_ga.parquet", "make reference STEP=exchange-plans", {
    "plan_id": V, "network_id": V, "name": V, "carrier": V, "metal_level": V, "needs_referral": F,
    "deductible": D}, key=["plan_id"])
# Each plan's monthly premium by rating area and age (ingestion/reference/exchange-plans.md).
EXCHANGE_PREMIUMS = Dataset("reference/exchange_premiums_ga.parquet", "make reference STEP=exchange-plans", {
    "plan_id": V, "rating_area": I, "age": B, "premium": D, "tobacco_premium": D},
    key=["plan_id", "rating_area", "age"])
# What each plan charges the member in network, by benefit, in Georgia's wording
# (ingestion/reference/exchange-plans.md).
EXCHANGE_COST_SHARING = Dataset("reference/exchange_cost_sharing_ga.parquet",
                                "make reference STEP=exchange-plans", {
    "plan_id": V, "benefit": V, "covered": F, "copay": V, "coinsurance": V}, key=["plan_id", "benefit"])
# Medicare's physician fee schedule in Georgia, by code, modifier, place of
# service and payment area (ingestion/reference/mpfs.md).
MPFS = Dataset("reference/mpfs_ga.parquet", "make reference STEP=mpfs", {
    "billing_code": V, "billing_code_type": V, "modifier": V, "pos": V, "locality": V,
    "medicare_allowed": D, "status": V}, key=["billing_code", "modifier", "pos", "locality"])
# Medicare's national hospital outpatient rate by code (ingestion/reference/opps.md).
OPPS = Dataset("reference/opps.parquet", "make reference STEP=opps", {
    "billing_code": V, "billing_code_type": V, "status_indicator": V, "apc": V, "payment_rate": D,
    "year": I}, key=["billing_code"])
# Each Georgia NPI's own record: name, specialty code, practice address and its
# phone (ingestion/etl/nppes.md).
NPPES = Dataset("nppes/ga_providers.parquet", "make reference STEP=nppes", {
    "npi": B, "entity_type": V, "org_name": V, "last_name": V, "first_name": V, "taxonomy_code": V,
    "taxonomy_group": V, "is_hospital": F, "is_clinic": F, "address_line1": V, "address_line2": V,
    "city": V, "state": V, "postal_code": V, "phone": V}, key=["npi"])
# The other Georgia addresses an NPI lists as places it practices, beside the one
# on its own record (ingestion/etl/nppes.md).
NPPES_LOCATIONS = Dataset("nppes/ga_locations.parquet", "make reference STEP=nppes", {
    "npi": B, "address_line1": V, "address_line2": V, "city": V, "state": V, "postal_code": V, "phone": V})
# Who billed Medicare for each code in Georgia, and whether in an office (O) or a
# facility (F) (ingestion/reference/cms-utilization.md).
MEDICARE_BILLING = Dataset("cms/ga_provider_service.parquet", "make reference STEP=cms-utilization", {
    "npi": B, "hcpcs_cd": V, "place_of_service": V, "tot_benes": I, "tot_srvcs": D, "tot_bene_day_srvcs": I,
    "avg_mdcr_alowd_amt": D, "provider_type": V, "hcpcs_drug_ind": V, "year": I},
    key=["npi", "hcpcs_cd", "place_of_service"])
# NUCC's specialty codes, with their classification and specialization
# (ingestion/reference/taxonomy-labels.md).
NUCC_TAXONOMY = Dataset("reference/nucc_taxonomy.parquet", "make reference STEP=taxonomy-labels", {
    "taxonomy_code": V, "grouping": V, "classification": V, "specialization": V, "display_name": V,
    "specialty": V, "is_individual": F}, key=["taxonomy_code"])
# Each clinician's CMS group, by NPI (ingestion/reference/doctors-clinicians.md).
DOCTORS_CLINICIANS = Dataset("reference/dac_ga.parquet", "make reference STEP=doctors-clinicians", {
    "npi": B, "last_name": V, "first_name": V, "credential": V, "primary_specialty": V, "org_pac_id": V,
    "org_name": V, "grad_year": I, "med_school": V, "gender": V}, key=["npi"])
# Each Georgia address CMS lists a clinician practicing at (ingestion/reference/doctors-clinicians.md).
CLINICIAN_LOCATIONS = Dataset("reference/dac_locations_ga.parquet", "make reference STEP=doctors-clinicians", {
    "npi": B, "address_line1": V, "address_line2": V, "city": V, "state": V, "postal_code": V},
    key=["npi", "address_line1", "address_line2", "city", "postal_code"])
# Each clinician's Medicare quality (MIPS) score, 0 to 100, by NPI (ingestion/reference/mips.md).
MIPS = Dataset("reference/mips_ga.parquet", "make reference STEP=mips", {
    "npi": B, "mips_score": D, "mips_source": V, "performance_year": I}, key=["npi"])
# Georgia's hospitals: CCN, name, address, phone and star rating (ingestion/reference/hospitals.md).
HOSPITALS = Dataset("reference/hospitals_ga.parquet", "make reference STEP=hospitals", {
    "ccn": V, "name": V, "address": V, "city": V, "state": V, "postal_code": V, "county": V, "phone": V,
    "hospital_type": V, "ownership": V, "emergency_services": F, "stars": I}, key=["ccn"])
# What each geocoder (`source`: census or openstreetmap) answered for each address
# it was sent. `located` says the answer is a point in Georgia, and only Census
# gives a standardized address (ingestion/reference/geocode.md).
GEOCODES = Dataset("reference/geocode_addresses.parquet", "make reference STEP=geocode", {
    "address_line1": V, "address_line2": V, "city": V, "state": V, "postal_code": V, "source": V,
    "matched_address": V, "latitude": D, "longitude": D, "located": F},
    key=["address_line1", "address_line2", "city", "state", "postal_code", "source"])
# Each ZIP's Census centroid: zip, lat, lon (ingestion/reference/zip-centroids.md).
ZIP_CENTROIDS = Dataset("reference/zip_centroids.parquet", "make reference STEP=zip-centroids", {
    "zip": V, "lat": D, "lon": D}, key=["zip"])

DATASETS = [PAYER_RATES, PAYER_RATE_GROUPS, PAYER_ROSTERS, PAYER_NETWORKS, EXCHANGE_NETWORKS, EXCHANGE_PLANS,
            EXCHANGE_PREMIUMS, EXCHANGE_COST_SHARING, MPFS, OPPS, NPPES, NPPES_LOCATIONS, MEDICARE_BILLING,
            NUCC_TAXONOMY, DOCTORS_CLINICIANS, CLINICIAN_LOCATIONS, MIPS, HOSPITALS, GEOCODES, ZIP_CENTROIDS]
