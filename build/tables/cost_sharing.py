"""cost_sharing.parquet: what each plan charges the member for the kinds of
service the find-care screen offers."""
from build.inputs.datasets import EXCHANGE_COST_SHARING
from build.table import Table

# Georgia's wording for the member's share in network: a copay such as "$65.00" or
# "$300.00 Copay after deductible", a coinsurance such as "40.00% Coinsurance after
# deductible", or "No Charge", with or without " after deductible".
COPAY = r"^(?:\$([0-9,]+(?:\.[0-9]+)?)(?: Copay)?|No Charge)( after deductible)?$"
COINSURANCE = r"^(?:([0-9]+(?:\.[0-9]+)?)%(?: Coinsurance)?|No Charge)( after deductible)?$"


def _worded(data_dir):
    """Each plan's wording for each service category the services use, empty
    where Georgia's file has no row for it."""
    return f"""
        SELECT p.plan_id, c.service_category, g.covered,
               nullif(g.copay, 'Not Applicable') AS copay, nullif(g.coinsurance, 'Not Applicable') AS coinsurance
        FROM plans p
        CROSS JOIN (SELECT DISTINCT service_category FROM service_list) c
        LEFT JOIN {EXCHANGE_COST_SHARING.read(data_dir)} g
          ON g.plan_id = p.plan_id AND g.benefit = c.service_category
    """


def _amount(text, pattern):
    """SQL for the dollars or percent in `text`, 0 for "No Charge"."""
    return (f"CASE WHEN {text} IS NOT NULL THEN coalesce(TRY_CAST(replace(regexp_extract({text}, '{pattern}', 1), "
            f"',', '') AS DOUBLE), 0) END")


class CostSharing(Table):
    """One row per plan and service category a service uses, from Georgia's
    benefits and cost-sharing file, for the plan's standard version
    (reference/exchange-plans.md). `copay` is in dollars and `coinsurance` is a
    percent of the rate, each empty when the plan doesn't charge one, and "No
    Charge" is a $0 copay. With both, the member pays the copay and then the
    coinsurance, as the plans' summaries of benefits word it. `after_deductible`
    says the member pays the whole rate until the deductible is met."""
    name = "cost_sharing"
    key = ["plan_id", "service_category"]
    columns = {"plan_id": "VARCHAR", "service_category": "VARCHAR", "copay": "DOUBLE",
               "coinsurance": "DOUBLE", "after_deductible": "BOOLEAN"}

    def select(self, data_dir):
        return f"""
            SELECT plan_id, service_category, {_amount("copay", COPAY)} AS copay,
                   {_amount("coinsurance", COINSURANCE)} AS coinsurance,
                   coalesce(copay LIKE '% after deductible' OR coinsurance LIKE '% after deductible', false)
                       AS after_deductible
            FROM ({_worded(data_dir)})
        """

    def check(self, con, data_dir):
        """The build stops unless every plan covers every category and words its
        share in a way this table reads, with the deductible applying to both
        the copay and the coinsurance or to neither."""
        rules = [
            ("covered IS NULL", "has no row in Georgia's cost-sharing file for"),
            ("NOT covered", "doesn't cover"),
            ("copay IS NULL AND coinsurance IS NULL", "lists no copay or coinsurance for"),
            (f"NOT regexp_matches(coalesce(copay, 'No Charge'), '{COPAY}') "
             f"OR NOT regexp_matches(coalesce(coinsurance, 'No Charge'), '{COINSURANCE}')",
             "words its share in a way the build doesn't read, for"),
            ("copay IS NOT NULL AND coinsurance IS NOT NULL "
             "AND (copay LIKE '% after deductible') != (coinsurance LIKE '% after deductible')",
             "applies the deductible to only one of the copay and the coinsurance, for"),
        ]
        for where, why in rules:
            bad = con.execute(f"""
                SELECT plan_id, service_category, copay, coinsurance FROM ({_worded(data_dir)})
                WHERE {where} ORDER BY plan_id, service_category LIMIT 1
            """).fetchone()
            if bad:
                raise SystemExit(f"cost_sharing: {bad[0]} {why} {bad[1]!r} (copay {bad[2]!r}, "
                                 f"coinsurance {bad[3]!r})")
