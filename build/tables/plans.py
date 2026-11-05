"""plans.parquet: one row per plan on a network a target prices, from Georgia's
exchange plan file."""
from build.inputs.datasets import EXCHANGE_PLANS, INDEX_LINKS
from build.table import Table


class Plans(Table):
    """A plan's cost-sharing versions share its row, and its name is Georgia's,
    as published (reference/exchange-plans.md). `deductible` is the standard
    version's in-network deductible for one person, in dollars. A plan is priced
    by its network's file, so the index must link each plan to that file
    (etl/mrf-model.md, "One plan, one contract")."""
    name = "plans"
    key = ["plan_id"]
    columns = {"plan_id": "VARCHAR", "name": "VARCHAR", "carrier": "VARCHAR",
               "metal_level": "VARCHAR", "needs_referral": "BOOLEAN", "network_id": "VARCHAR",
               "deductible": "DOUBLE"}

    def select(self, data_dir):
        return f"""
            SELECT plan_id, name, carrier, metal_level, needs_referral, network_id, deductible
            FROM {EXCHANGE_PLANS.read(data_dir)}
            WHERE network_id IN (SELECT network_id FROM networks)
        """

    def check(self, con, data_dir):
        unlinked = con.execute(f"""
            SELECT DISTINCT p.plan_id, p.network_id, f.file_id
            FROM plans p JOIN network_files f USING (network_id)
            WHERE NOT EXISTS (SELECT 1 FROM {INDEX_LINKS.read(data_dir)} l
                              WHERE l.file_id = f.file_id AND l.plan_id = p.plan_id)
            ORDER BY p.plan_id
        """).fetchone()
        if unlinked:
            plan, net, file_id = unlinked
            raise SystemExit(f"plans: the index doesn't link {plan} to file {file_id}, which prices "
                             f"its network {net}, so that file's rates aren't its contract "
                             f"(etl/mrf-model.md#one-plan-one-contract)")
