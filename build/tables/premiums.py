"""premiums.parquet: each plan's monthly premium for one person, by rating area
and age, from Georgia's premium file."""
from build.inputs.datasets import EXCHANGE_PREMIUMS
from build.table import Table

# Georgia prices every age up to 64, and age 64 stands for 64 and over
# (reference/exchange-plans.md).
AGES = 65


class Premiums(Table):
    """A plan has premiums in every rating area it's sold in, and each of those
    has one for every age from 0 to 64, so a lookup by min(age, 64) always finds
    one."""
    name = "premiums"
    key = ["plan_id", "rating_area", "age"]
    columns = {"plan_id": "VARCHAR", "rating_area": "INTEGER", "age": "INTEGER",
               "premium": "DOUBLE", "tobacco_premium": "DOUBLE"}

    def select(self, data_dir):
        return f"""
            SELECT plan_id, rating_area, age::INTEGER AS age, premium, tobacco_premium
            FROM {EXCHANGE_PREMIUMS.read(data_dir)}
            WHERE plan_id IN (SELECT plan_id FROM plans)
        """

    def check(self, con, data_dir):
        unpriced = con.execute("""
            SELECT plan_id FROM plans
            WHERE plan_id NOT IN (SELECT plan_id FROM premiums)
            ORDER BY plan_id
        """).fetchone()
        if unpriced:
            raise SystemExit(f"premiums: Georgia's premium file has no premium for {unpriced[0]}")
        gap = con.execute(f"""
            SELECT plan_id, rating_area, count(*) FROM premiums
            GROUP BY plan_id, rating_area
            HAVING count(*) != {AGES} OR min(age) != 0 OR max(age) != {AGES - 1}
            ORDER BY plan_id, rating_area
        """).fetchone()
        if gap:
            raise SystemExit(f"premiums: {gap[0]} in rating area {gap[1]} has {gap[2]} ages, "
                             f"not every age from 0 to {AGES - 1}")
