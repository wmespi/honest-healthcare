"""Build-only: for each target network, the parsed insurer file whose rates
price it and the labels those rates carry."""
from build.inputs.datasets import EXCHANGE_PLANS, INDEX_LINKS, INSURER_RATES
from build.table import Table


class NetworkFiles(Table):
    """A file prices a network when the index links it to one of the network's
    plans in Georgia's plan file and its rates carry one of the target's labels.
    Labels match the way the parse probe matches them
    (etl/extraction/targets.go): trimmed, case-insensitive, and `*` is the only
    wildcard. Exactly one file must price each network (etl/mrf-model.md, "One
    plan, one contract")."""
    name = "network_files"
    key = ["network_id", "file_id", "network_name"]
    columns = {"network_id": "VARCHAR", "file_id": "BIGINT", "network_name": "VARCHAR"}

    def select(self, data_dir):
        return f"""
            WITH labelled AS ({_labelled(data_dir)}),
                 linked AS (SELECT DISTINCT p.network_id, l.file_id
                            FROM {EXCHANGE_PLANS.read(data_dir)} p
                            JOIN {INDEX_LINKS.read(data_dir)} l USING (plan_id))
            SELECT network_id, file_id, network_name
            FROM labelled JOIN linked USING (network_id, file_id)
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
        if not bad:
            return
        target, net, patterns, files = bad
        if files:
            raise SystemExit(f"{target}: the index links files {files} to plans on {net}, and each "
                             f"carries a label matching {patterns}, but one file prices {net} "
                             f"(etl/mrf-model.md#one-plan-one-contract). Delete the Parquet under "
                             f"data/anthem/ of any that don't, whether an older copy or another contract")
        unlinked = con.execute(f"SELECT list(DISTINCT file_id ORDER BY file_id) "
                               f"FROM ({_labelled(data_dir)}) WHERE network_id = ?", [net]).fetchone()[0]
        if unlinked:
            raise SystemExit(f"{target}: files {unlinked} carry a label matching {patterns}, but the "
                             f"index doesn't link them to a plan on {net}, so nothing prices it. Name "
                             f"the file it links in etl/targets.yaml and run `make parse`")
        raise SystemExit(f"{target}: no parsed insurer file carries a label matching "
                         f"{patterns}, so nothing prices {net}; run `make parse`")


def _labelled(data_dir):
    """(network_id, file_id, network_name) for each parsed file whose rates carry
    a label matching one of a target network's patterns."""
    return f"""
        SELECT DISTINCT k.network_id, l.file_id, l.network_name
        FROM (SELECT network_id, unnest(likes) AS label_like FROM targets) k
        JOIN (SELECT DISTINCT file_id, network_name FROM {INSURER_RATES.read(data_dir)}) l
          ON lower(trim(l.network_name)) LIKE k.label_like ESCAPE '!'
    """
