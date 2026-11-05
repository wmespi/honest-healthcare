"""networks.parquet: one row per network the payer rates price, with Georgia's
exchange name for it."""
from integration.inputs.datasets import EXCHANGE_NETWORKS, PAYER_RATES
from integration.table import Table


class Networks(Table):
    """Every network the payer rates price has to be one of Georgia's."""
    name = "networks"
    key = ["network_id"]
    columns = {"network_id": "VARCHAR", "name": "VARCHAR"}

    def select(self, data_dir):
        return f"""
            SELECT network_id, name
            FROM {EXCHANGE_NETWORKS.read(data_dir)}
            WHERE network_id IN (SELECT network_id FROM {PAYER_RATES.read(data_dir)})
        """

    def check(self, con, data_dir):
        unknown = con.execute(f"""
            SELECT DISTINCT network_id FROM {PAYER_RATES.read(data_dir)}
            WHERE network_id NOT IN (SELECT network_id FROM networks) ORDER BY network_id
        """).fetchone()
        if unknown:
            raise SystemExit(f"networks: the payer rates price {unknown[0]}, which isn't a network "
                             f"in Georgia's exchange files")
