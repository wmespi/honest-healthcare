"""What every table gets, whether a serving table in build/tables/ or a
build-only input in build/inputs/. A table states its name, key and columns and
the SQL that makes its rows, and `Table.make` makes it, then stops the build
unless

1. its columns are `columns`, in order;
2. its key is never empty;
3. its key never repeats;
4. its own `check`, if it has one, passes.

`Table.write` saves a serving table in key order, so the same rows always make
the same bytes.
"""
from reference._common import write_parquet_atomic


class Table:
    """A table the build makes. A subclass sets `name`, `key` and `columns`
    (name -> DuckDB type, in order) and writes `select`, plus `check` for any
    rule beyond its key."""
    name: str
    key: list
    columns: dict

    def select(self, data_dir):
        """The SELECT that makes this table's rows."""
        raise NotImplementedError

    def fill(self, con, data_dir):
        """Make the table in `con` from its SELECT."""
        con.execute(f"CREATE TEMP TABLE {self.name} AS {self.select(data_dir)}")

    def check(self, con, data_dir):
        """Stop the build if the rows break a rule of this table's."""

    def make(self, con, data_dir):
        """Make the table in `con`, then run checks 1-4 above."""
        self.fill(con, data_dir)
        got = {c: t for c, t, *_ in con.execute(f"DESCRIBE {self.name}").fetchall()}
        if list(got.items()) != list(self.columns.items()):
            raise SystemExit(f"{self.name}: columns {got}, but the table says {self.columns}")
        cols = ", ".join(self.key)
        empty = con.execute(f"SELECT count(*) FROM {self.name} WHERE "
                            + " OR ".join(f"{c} IS NULL" for c in self.key)).fetchone()[0]
        if empty:
            raise SystemExit(f"{self.name}: {empty} rows with an empty key ({cols})")
        dup = con.execute(f"SELECT {cols} FROM {self.name} GROUP BY {cols} "
                          f"HAVING count(*) > 1 LIMIT 3").fetchall()
        if dup:
            raise SystemExit(f"{self.name}: the key ({cols}) repeats, for example {dup}")
        self.check(con, data_dir)

    def write(self, con, out_dir):
        """Write the table to `<out_dir>/<name>.parquet` in key order. Returns
        its row count."""
        write_parquet_atomic(con, f"SELECT * FROM {self.name} ORDER BY {', '.join(self.key)}",
                             f"{out_dir}/{self.name}.parquet")
        return con.execute(f"SELECT count(*) FROM {self.name}").fetchone()[0]
