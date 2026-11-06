"""The build step — raw + reference Parquet into the find-care serving tables.

See integration/build.md. Every product decision (who is on a plan, what a price is,
where a place is) lives here, so the API reads the tables as they are.
"""
