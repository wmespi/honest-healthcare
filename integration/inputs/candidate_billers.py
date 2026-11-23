"""Build-only: the billers with a rate for a service that can appear for it."""
from integration.inputs.biller_rates import rate_evidence, rate_rosters
from integration.inputs.datasets import roster_biller
from integration.table import Table


class CandidateBillers(Table):
    """A biller with a rate for a service can appear for it only when a doctor on
    a roster its rates cover is one Medicare or Medicaid shows billing the service,
    or the biller is itself a candidate, billing the service or registered as a
    center of its family (biller_rates.rate_evidence). biller_rates.covered carries
    the rates of these billers alone: a large roster's other billers, most of
    those with a rate, never appear, and carrying their rates doctor by doctor
    took more memory than the build has."""
    name = "candidate_billers"
    key = ["network_id", "service_id", "biller"]
    columns = {"network_id": "VARCHAR", "service_id": "VARCHAR", "biller": "VARCHAR"}

    def select(self, data_dir):
        return f"""
            WITH {rate_evidence(data_dir)},
            billers AS (
                SELECT k.network_id, k.service_id, {roster_biller('p')} AS biller,
                       bool_or(coalesce(t.billed, false)) AS doctor
                {rate_rosters(data_dir)}
                GROUP BY ALL
            )
            SELECT b.network_id, b.service_id, b.biller
            FROM billers b
            LEFT JOIN candidates c ON c.npi = TRY_CAST(b.biller AS BIGINT) AND c.service_id = b.service_id
            WHERE b.doctor OR c.npi IS NOT NULL
        """
