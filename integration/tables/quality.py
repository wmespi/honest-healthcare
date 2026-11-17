"""quality.parquet: CMS's quality measures for the places and clinicians the screen shows."""
from integration.inputs.datasets import (
    ASC_MEASURES, ASC_MEASURES_NATIONAL, CLINICIAN_VOLUME, HOSPITAL_MEASURES, HOSPITAL_MEASURES_NATIONAL,
)
from integration.inputs.measures import FITS, SOURCES
from integration.table import Table


def number(x, unit):
    """SQL for the number `x` the way a measure of `unit` reads it, such as "93%"."""
    return f"""CASE {unit}
        WHEN 'percent' THEN printf('%g%%', round({x}, 1))
        WHEN 'per_1000' THEN printf('%g per 1,000', {x})
        WHEN 'ratio' THEN printf('%g times the expected rate', {x})
        WHEN 'stars' THEN printf('%g of 5 stars', {x})
        END"""


def plain(label):
    """SQL for CMS's comparison `label` in plain words ("Better than the national
    rate"), nothing for a label that says the cases are too few to compare."""
    return (f"CASE WHEN {label} ILIKE '%too small%' THEN NULL "
            f"ELSE upper(left({label}, 1)) || lower(substr({label}, 2)) END")


def years(start, end):
    """SQL for the span of years from the date `start` to the date `end`, such as "2022 to 2024"."""
    return f"CASE WHEN year({start}) = year({end}) THEN year({start})::VARCHAR " \
           f"ELSE year({start}) || ' to ' || year({end}) END"


class Quality(Table):
    """One row per subject and measure in integration/measures.yaml: a hospital's by
    its CCN where it is one of `places`, a surgery center's on the place that its
    NPI bills from (`biller_places`, never a hospital's, and one row where several
    NPIs bill from one place: the one with a score, then the lowest NPI and CCN), and a
    clinician's procedure volume by NPI where CMS displays it and the clinician is
    in `providers`. A measure with neither a score nor a CMS comparison for the subject
    has no row.

    `shown` is how the value reads, `compared` CMS's own comparison in plain words or,
    where CMS has none, the national value, and `period` the years it covers (volume
    has none). For a volume, `score` is the count, or the low end of CMS's band, and
    `shown` says which. `name`, `about`, `source` and `position` come from the measure,
    and `services` is the service ids it fits, empty while it fits none."""
    name = "quality"
    key = ["subject", "subject_kind", "measure"]
    columns = {"subject": "VARCHAR", "subject_kind": "VARCHAR", "measure": "VARCHAR", "name": "VARCHAR",
               "about": "VARCHAR", "source": "VARCHAR", "score": "DOUBLE", "shown": "VARCHAR",
               "compared": "VARCHAR", "period": "VARCHAR", "position": "INTEGER", "services": "VARCHAR[]"}

    def select(self, data_dir):
        labels = "CASE m.source " + " ".join(f"WHEN '{k}' THEN '{v[0]}'" for k, v in SOURCES.items()) + " END"
        return f"""
            WITH fits AS ({FITS}),
            found AS (
                SELECT h.ccn AS subject, 'place' AS subject_kind, h.measure, h.score, h.compared AS label,
                       n.score AS national, {years("h.start_date", "h.end_date")} AS period, NULL::VARCHAR AS count
                FROM {HOSPITAL_MEASURES.read(data_dir)} h
                LEFT JOIN {HOSPITAL_MEASURES_NATIONAL.read(data_dir)} n USING (measure)
                WHERE h.ccn IN (SELECT place_id FROM places)
                UNION ALL
                SELECT subject, subject_kind, measure, score, label, national, period, count FROM (
                    SELECT p.place_id AS subject, 'place' AS subject_kind, a.measure, a.score, a.compared AS label,
                           n.national, a.period, NULL::VARCHAR AS count, a.npi
                    FROM {ASC_MEASURES.read(data_dir)} a
                    LEFT JOIN {ASC_MEASURES_NATIONAL.read(data_dir)} n USING (measure)
                    JOIN biller_places b ON b.biller = a.npi::VARCHAR
                    JOIN places p ON p.place_id = b.place_id AND p.kind != 'hospital'
                    QUALIFY row_number() OVER (PARTITION BY p.place_id, a.measure
                                               ORDER BY a.score IS NULL, a.npi, a.ccn) = 1)
                UNION ALL
                SELECT v.npi::VARCHAR, 'clinician', m.measure, coalesce(try_cast(v.count AS DOUBLE),
                       try_cast(split_part(v.count, '-', 1) AS DOUBLE)), NULL, NULL, NULL, v.count
                FROM {CLINICIAN_VOLUME.read(data_dir)} v
                JOIN measures m ON m.category = v.category
                WHERE v.displayed AND v.npi IN (SELECT npi FROM providers)
            )
            SELECT f.subject, f.subject_kind, m.measure, m.name, m.about, {labels} AS source, f.score,
                   CASE WHEN f.count IS NOT NULL
                        THEN replace(f.count, '-', ' to ') || CASE WHEN f.count = '1' THEN ' procedure' ELSE ' procedures' END
                        WHEN f.score IS NOT NULL THEN {number("f.score", "m.unit")} END AS shown,
                   coalesce({plain("f.label")},
                            CASE WHEN f.national IS NOT NULL
                                 THEN 'The national average is ' || {number("f.national", "m.unit")} END) AS compared,
                   f.period, m.position,
                   coalesce((SELECT list(x.service_id ORDER BY x.service_id) FROM fits x
                             WHERE x.measure = m.measure), []::VARCHAR[]) AS services
            FROM found f JOIN measures m ON m.measure = f.measure AND m.subject = f.subject_kind
            WHERE f.score IS NOT NULL OR {plain("f.label")} IS NOT NULL
        """
