"""plans.parquet: one row per plan on a network the payer rates price, from
Georgia's exchange plan file."""
from build.inputs.datasets import EXCHANGE_PLANS
from build.table import Table


class Plans(Table):
    """A plan's cost-sharing versions share its row, and its name is Georgia's,
    as published (reference/exchange-plans.md). `deductible` is the standard
    version's in-network deductible for one person, in dollars. A plan is priced
    by its network's rates."""
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
