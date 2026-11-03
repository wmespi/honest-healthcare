"""networks.parquet: one row per network a target prices, with Georgia's
exchange name for it."""
from build.inputs.datasets import EXCHANGE_NETWORKS
from build.table import Table


class Networks(Table):
    name = "networks"
    key = ["network_id"]
    columns = {"network_id": "VARCHAR", "name": "VARCHAR"}

    def select(self, data_dir):
        return f"""
            SELECT network_id, name
            FROM {EXCHANGE_NETWORKS.read(data_dir)}
            WHERE network_id IN (SELECT network_id FROM network_files)
        """
