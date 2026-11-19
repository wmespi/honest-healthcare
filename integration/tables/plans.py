"""plans.parquet: one row per plan on a network with prices, from Georgia's
exchange plan file."""
from integration.inputs.datasets import EXCHANGE_COST_SHARING, EXCHANGE_PLANS, without_company_form
from integration.table import Table


class Plans(Table):
    """A plan as a person picks it: Georgia gives one plan design a plan ID in each
    service area it's sold in, so plans on one network with the same `name`,
    `metal_level`, `deductible` and `needs_referral` are one row, keyed on the
    lowest of their IDs, and `plan_ids` has every ID the row stands for, sorted.
    A plan's cost-sharing versions share its row, and its name is Georgia's,
    as published (ingestion/reference/exchange-plans.md). `carrier` is Georgia's
    marketing name for the insurer, or only the name after "DBA" where it gives
    the company that does business under it ("Centene Corporation DBA Ambetter
    from Peach State Health Plan" is "Ambetter from Peach State Health Plan"),
    without a company form at its end ("Alliant Health Plans, Inc." is "Alliant
    Health Plans"). `deductible` is the standard
    version's in-network deductible for one person, in dollars. A plan is priced
    by its network's rates."""
    name = "plans"
    key = ["plan_id"]
    columns = {"plan_id": "VARCHAR", "name": "VARCHAR", "carrier": "VARCHAR",
               "metal_level": "VARCHAR", "needs_referral": "BOOLEAN", "network_id": "VARCHAR",
               "deductible": "DOUBLE", "plan_ids": "VARCHAR[]"}

    def select(self, data_dir):
        return f"""
            SELECT min(plan_id) AS plan_id, name,
                   min({without_company_form("regexp_replace(carrier, '^.+\\s(DBA|D/B/A)\\s+', '', 'i')")})
                       AS carrier,
                   metal_level, needs_referral,
                   network_id, deductible, list_sort(list(plan_id)) AS plan_ids
            FROM {EXCHANGE_PLANS.read(data_dir)}
            WHERE network_id IN (SELECT network_id FROM networks WHERE prices_status != 'missing')
            GROUP BY network_id, name, metal_level, deductible, needs_referral
        """

    def check(self, con, data_dir):
        """The build stops if the plan IDs one row stands for differ in Georgia's
        cost sharing for any benefit, or not all of them list it."""
        bad = con.execute(f"""
            SELECT p.name, g.benefit FROM plans p, unnest(p.plan_ids) AS u(id)
            LEFT JOIN {EXCHANGE_COST_SHARING.read(data_dir)} g ON g.plan_id = u.id
            GROUP BY p.plan_id, p.name, p.plan_ids, g.benefit
            HAVING count(DISTINCT (g.covered, g.copay, g.coinsurance)) > 1 OR count(*) != len(p.plan_ids)
            ORDER BY p.plan_id, g.benefit LIMIT 1
        """).fetchone()
        if bad:
            raise SystemExit(f"plans: the plan IDs of {bad[0]!r} differ in Georgia's cost sharing "
                             f"for {bad[1]!r}")
