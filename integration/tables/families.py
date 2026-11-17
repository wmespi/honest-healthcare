"""families.parquet: the families of services the screen lists, in order."""
from integration.table import Table


class Families(Table):
    """Every family in integration/families.yaml with a service in services, with
    what the screen calls it (`name`), where it lists it (`position`, 1 first)
    and, for a family that asks which kind of doctor, the kinds to pick from
    (`specialties`, in integration/specialties.yaml's order, with the benefit
    each one's visit falls under), empty for one that doesn't."""
    name = "families"
    key = ["family"]
    columns = {"family": "VARCHAR", "name": "VARCHAR", "position": "INTEGER",
               "specialties": 'STRUCT(specialty VARCHAR, "name" VARCHAR, benefit VARCHAR)[]'}

    def select(self, data_dir):
        return """
            SELECT f.family, f.name, f.position,
                   CASE WHEN f.asks_specialty
                        THEN (SELECT coalesce(list({'specialty': s.specialty, 'name': s.name, 'benefit': s.benefit}
                                                   ORDER BY s.position), [])
                              FROM specialties s)
                        ELSE []::STRUCT(specialty VARCHAR, name VARCHAR, benefit VARCHAR)[] END AS specialties
            FROM family_list f
            WHERE f.family IN (SELECT family FROM services)
        """
