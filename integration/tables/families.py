"""families.parquet: the families of services the screen lists, in order."""
from integration.table import Table


class Families(Table):
    """Every family in integration/families.yaml with a service in services, with
    what the screen calls it (`name`) and where it lists it (`position`, 1 first)."""
    name = "families"
    key = ["family"]
    columns = {"family": "VARCHAR", "name": "VARCHAR", "position": "INTEGER"}

    def select(self, data_dir):
        return "SELECT family, name, position FROM family_list WHERE family IN (SELECT family FROM services)"
