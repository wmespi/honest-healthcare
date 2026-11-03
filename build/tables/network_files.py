"""Build-only: for each target network, the parsed insurer file whose rates
price it and the labels those rates carry."""
from build.tables.inputs import INSURER_RATES
from build.tables.table import Table


class NetworkFiles(Table):
    """Labels match a target's patterns the way the parse probe matches them
    (etl/extraction/targets.go): trimmed, case-insensitive, and `*` is the only
    wildcard. Exactly one file must carry them (etl/mrf-model.md, "One plan,
    one contract")."""
    name = "network_files"
    key = ["network_id", "file_id", "network_name"]
    columns = {"network_id": "VARCHAR", "file_id": "BIGINT", "network_name": "VARCHAR"}
    written = False

    def select(self, data_dir):
        return f"""
            WITH labels AS (SELECT DISTINCT file_id, network_name FROM {INSURER_RATES.read(data_dir)}),
                 likes AS (SELECT network_id, unnest(likes) AS label_like FROM targets)
            SELECT DISTINCT k.network_id, l.file_id, l.network_name
            FROM likes k
            JOIN labels l ON lower(trim(l.network_name)) LIKE k.label_like ESCAPE '!'
        """

    def check(self, con, data_dir):
        bad = con.execute("""
            SELECT t.target, t.network_id, t.patterns,
                   list(DISTINCT f.file_id ORDER BY f.file_id) FILTER (WHERE f.file_id IS NOT NULL)
            FROM targets t LEFT JOIN network_files f USING (network_id)
            GROUP BY t.target, t.network_id, t.patterns
            HAVING count(DISTINCT f.file_id) != 1
            ORDER BY t.network_id
        """).fetchone()
        if bad:
            target, net, patterns, files = bad
            if not files:
                raise SystemExit(f"{target}: no parsed insurer file carries a label matching "
                                 f"{patterns}, so nothing prices {net}; run `make parse`")
            raise SystemExit(f"{target}: files {files} all carry a label matching {patterns}, "
                             f"but one file prices {net} (etl/mrf-model.md#one-plan-one-contract). "
                             f"Delete the Parquet under data/anthem/ of any that don't, "
                             f"whether an older copy or another contract")
