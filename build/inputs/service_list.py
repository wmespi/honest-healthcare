"""Build-only: one row per service in build/services.yaml, with what the screen
says about it."""
import os

import yaml

from build.table import Table

SERVICES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "services.yaml")


class ServiceList(Table):
    """A `service_id` is a lower-case slug, and a `billing_code` is five
    characters and belongs to one service. Every service has a name, keywords
    and text about it, and at least one thing that could change the bill and
    one question to ask."""
    name = "service_list"
    key = ["service_id"]
    columns = {"service_id": "VARCHAR", "billing_code": "VARCHAR", "name": "VARCHAR",
               "keywords": "VARCHAR", "about": "VARCHAR", "could_change": "VARCHAR[]",
               "asks": "VARCHAR[]", "preventive_note": "VARCHAR"}

    def __init__(self, path):
        self.path = path

    def fill(self, con, data_dir):
        """Made from build/services.yaml, not a SELECT; its values go in as parameters."""
        services = read_services(self.path)
        if not services:
            raise SystemExit(f"{self.path} lists no services")
        for s in services:
            unknown = sorted(set(s) - set(self.columns))
            if unknown:
                raise SystemExit(f"{self.path}: {s.get('service_id')} has fields the screen "
                                 f"doesn't use: {unknown}")
        con.execute(f"CREATE TEMP TABLE {self.name} ("
                    + ", ".join(f"{c} {t}" for c, t in self.columns.items()) + ")")
        con.executemany(f"INSERT INTO {self.name} VALUES ({', '.join('?' * len(self.columns))})",
                        [[s.get(c) for c in self.columns] for s in services])

    def check(self, con, data_dir):
        bad = con.execute("""
            SELECT service_id FROM service_list
            WHERE NOT regexp_full_match(service_id, '[a-z0-9]+(-[a-z0-9]+)*')
            ORDER BY service_id
        """).fetchone()
        if bad:
            raise SystemExit(f"service_list: {bad[0]!r} isn't a lower-case slug such as mri-lower-back")
        bad = con.execute("""
            SELECT service_id, billing_code FROM service_list
            WHERE NOT regexp_full_match(coalesce(billing_code, ''), '[0-9A-Z]{5}')
            ORDER BY service_id
        """).fetchone()
        if bad:
            raise SystemExit(f"service_list: {bad[0]}'s billing_code {bad[1]!r} isn't a five-character code")
        shared = con.execute("""
            SELECT billing_code, list(service_id ORDER BY service_id) FROM service_list
            GROUP BY billing_code HAVING count(*) > 1 ORDER BY billing_code
        """).fetchone()
        if shared:
            raise SystemExit(f"service_list: {shared[1]} all bill {shared[0]}, but a code is one service")
        empty = con.execute("""
            SELECT service_id, field FROM (
                SELECT service_id, 'name' AS field FROM service_list WHERE coalesce(trim(name), '') = ''
                UNION ALL SELECT service_id, 'keywords' FROM service_list WHERE coalesce(trim(keywords), '') = ''
                UNION ALL SELECT service_id, 'about' FROM service_list WHERE coalesce(trim(about), '') = ''
                UNION ALL SELECT service_id, 'could_change' FROM service_list
                    WHERE coalesce(len(could_change), 0) = 0
                       OR len(list_filter(could_change, x -> coalesce(trim(x), '') = '')) > 0
                UNION ALL SELECT service_id, 'asks' FROM service_list
                    WHERE coalesce(len(asks), 0) = 0
                       OR len(list_filter(asks, x -> coalesce(trim(x), '') = '')) > 0
                UNION ALL SELECT service_id, 'preventive_note' FROM service_list
                    WHERE preventive_note IS NOT NULL AND trim(preventive_note) = '')
            ORDER BY service_id, field
        """).fetchone()
        if empty:
            raise SystemExit(f"service_list: {empty[0]} has an empty {empty[1]}")


def read_services(path=SERVICES_PATH):
    """Each service in build/services.yaml, as a dict of its fields."""
    with open(path) as f:
        return (yaml.safe_load(f) or {}).get("services") or []
