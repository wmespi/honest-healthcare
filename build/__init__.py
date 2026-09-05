"""The build step — raw + reference Parquet into the serving tables.

See build/build.md. Product decisions that used to live in serving-layer SQL
strings (the sentinel-price rule, the Medicare benchmark, service-line
allowlists, the browse rollup) live here now.
"""
