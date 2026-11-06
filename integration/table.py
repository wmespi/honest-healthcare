"""What every table gets, whether a serving table in integration/tables/ or a
build-only input in integration/inputs/. A table states its name, key and columns and
the SQL that makes its rows, and `Table.make` makes it, then stops the build
unless

1. its columns are `columns`, in order;
2. its key is never empty;
3. its key never repeats;
4. its own `check`, if it has one, passes.

`Table.stage` writes a serving table in key order beside where it goes, so the
same rows always make the same bytes, and the build moves every table into
place only once all of them are written (integration/tables/__init__.py).
"""
import os


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

    def path(self, out_dir):
        """Where the table goes: `<out_dir>/<name>.parquet`."""
        return f"{out_dir}/{self.name}.parquet"

    def staged_path(self, out_dir):
        """The temp file `stage` writes beside `path`."""
        return f"{self.path(out_dir)}.tmp.{os.getpid()}.parquet"

    def stage(self, con, out_dir):
        """Write the table in key order to `staged_path`, for the build to move
        into place once every table is written. Returns its row count."""
        os.makedirs(out_dir, exist_ok=True)
        con.execute(f"COPY (SELECT * FROM {self.name} ORDER BY {', '.join(self.key)}) "
                    f"TO '{self.staged_path(out_dir)}' (FORMAT parquet, COMPRESSION zstd)")
        return con.execute(f"SELECT count(*) FROM {self.name}").fetchone()[0]
