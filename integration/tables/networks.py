"""networks.parquet: one row per network ingestion delivers rates for, with
Georgia's exchange name for it and how fresh its prices are."""
from integration.inputs.datasets import EXCHANGE_NETWORKS, PAYER_NETWORKS, PAYER_RATES
from integration.table import Table

STATUSES = ("current", "stale", "missing")


class Networks(Table):
    """Every network the payer rates list has to be one of Georgia's.
    `prices_status` is `current` when its prices are from the payer's latest
    copy of its file, `stale` when they're from an earlier one, and `missing`
    when it has none, so no plan on it is offered. `prices_as_of` is the day
    (UTC) the payer updated the copy they're from, and `prices_reason` says why
    they aren't current."""
    name = "networks"
    key = ["network_id"]
    columns = {"network_id": "VARCHAR", "name": "VARCHAR", "prices_status": "VARCHAR", "prices_as_of": "DATE",
               "prices_reason": "VARCHAR"}

    def select(self, data_dir):
        return f"""
            SELECT p.network_id, g.name, p.status AS prices_status,
                   CAST(timezone('UTC', p.file_updated_at) AS DATE) AS prices_as_of, p.reason AS prices_reason
            FROM {PAYER_NETWORKS.read(data_dir)} p JOIN {EXCHANGE_NETWORKS.read(data_dir)} g USING (network_id)
        """

    def check(self, con, data_dir):
        unknown = con.execute(f"""
            SELECT network_id FROM {PAYER_NETWORKS.read(data_dir)}
            WHERE network_id NOT IN (SELECT network_id FROM networks) ORDER BY network_id
        """).fetchone()
        if unknown:
            raise SystemExit(f"networks: the payer rates list {unknown[0]}, which isn't a network "
                             f"in Georgia's exchange files")
        odd = con.execute(f"SELECT network_id, prices_status FROM networks "
                          f"WHERE prices_status NOT IN {STATUSES} ORDER BY network_id").fetchone()
        if odd:
            raise SystemExit(f"networks: {odd[0]}'s prices are {odd[1]!r}, not one of {', '.join(STATUSES)}")
        priced = f"(SELECT DISTINCT network_id FROM {PAYER_RATES.read(data_dir)})"
        unlisted = con.execute(f"""
            SELECT r.network_id, n.prices_status FROM {priced} r LEFT JOIN networks n USING (network_id)
            WHERE n.prices_status IS NULL OR n.prices_status = 'missing' ORDER BY r.network_id
        """).fetchone()
        if unlisted:
            raise SystemExit(f"networks: the payer rates price {unlisted[0]}, but "
                             + ("say it has none" if unlisted[1] else "don't list it in networks.parquet"))
        unpriced = con.execute(f"""
            SELECT network_id, prices_status FROM networks
            WHERE prices_status != 'missing' AND network_id NOT IN {priced} ORDER BY network_id
        """).fetchone()
        if unpriced:
            raise SystemExit(f"networks: the payer rates say {unpriced[0]}'s prices are {unpriced[1]}, "
                             f"but have none for it")
