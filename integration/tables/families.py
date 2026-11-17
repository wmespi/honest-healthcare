"""families.parquet: the families of services the screen lists, in order."""
from integration.table import Table


class Families(Table):
    """Every family in integration/families.yaml with a service in services, with
    what the screen calls it (`name`), where it lists it (`position`, 1 first)
    and, for a family that asks which kind of doctor, the kinds to pick from
    (`specialties`, in integration/specialties.yaml's order, with the benefit
    each one's visit falls under), empty for one that doesn't, and `measures`, the ids
    of the quality measures (integration/measures.yaml) that fit any of its services,
    in that file's order, empty when none does, and `empty_note`, what the screen
    says when the plan has no place with a price for one of its services, empty for
    the screen's own wording."""
    name = "families"
    key = ["family"]
    columns = {"family": "VARCHAR", "name": "VARCHAR", "position": "INTEGER",
               "specialties": 'STRUCT(specialty VARCHAR, "name" VARCHAR, benefit VARCHAR)[]',
               "measures": "VARCHAR[]", "empty_note": "VARCHAR"}

    def select(self, data_dir):
        return """
            SELECT f.family, f.name, f.position,
                   CASE WHEN f.asks_specialty
                        THEN (SELECT coalesce(list({'specialty': s.specialty, 'name': s.name, 'benefit': s.benefit}
                                                   ORDER BY s.position), [])
                              FROM specialties s)
                        ELSE []::STRUCT(specialty VARCHAR, name VARCHAR, benefit VARCHAR)[] END AS specialties,
                   coalesce((SELECT list(m.measure ORDER BY m.position) FROM measures m
                             WHERE m.measure IN (SELECT x.measure FROM measure_fits x WHERE x.family = f.family)),
                            []::VARCHAR[]) AS measures,
                   f.empty_note
            FROM family_list f
            WHERE f.family IN (SELECT family FROM services)
        """
