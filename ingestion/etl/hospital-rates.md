# `make reference STEP=hospital-rates` - hospitals' own price files

*Read this when working on the rates hospitals publish themselves, or adding a hospital.*

CMS's hospital price transparency rule has every hospital publish a machine-readable file of its standard charges: each item's gross charge and cash price, and the rate it has negotiated with each payer and plan.
`make reference STEP=hospital-rates` → `etl hospital-rates` (package `ingestion/etl/hospitalrates`) streams the file of each hospital [hospitals.yaml](hospitals.yaml) lists, and writes its rates for the payers the list keeps to `data/ingestion/hospital_rates/<ccn>.parquet`.

**In Go, by design** ([language principle](../../AGENTS.md#the-language-principle)): a hospital's file can run to gigabytes, and the work is a single-pass stream over CSV or JSON.

## The list - `ingestion/etl/hospitals.yaml`

- `payers` are case-insensitive regular expressions: a rate whose payer name matches one is kept, and every other payer's is dropped.
- Each hospital gives its CCN, which names its output and matches `ccn` in `hospitals_ga.parquet` ([hospitals.md](../reference/hospitals.md)), and its everyday name, for the log.
- It then says where its file is: its system's `cms-hpt.txt` (`index`) and its `location-name` there (`location`), or, for a hospital with no usable index, the file's own `url`.
- A `cms-hpt.txt` sits at the root of a system's website and lists each location's `mrf-url`, and a run fetches each one once.
- The location is matched case-insensitively once trimmed, and a relative `mrf-url` is resolved against the index's address.

To add a hospital, find its `location-name` in its system's `cms-hpt.txt`, add an entry with its CCN, and run `make reference STEP=hospital-rates ARGS="-only <ccn>"`.

## The formats

Each file is downloaded to `data/raw/hospital_rates/<ccn>.download`, with the project's User-Agent, and deleted once read; at the end of a run the folder goes too, unless something else is in it.
A download that stops sending part way fails rather than waits.
Its first bytes say what it is, not its name, which some hospitals serve with no extension.
A zip is read at its first `.csv` or `.json` entry, else its largest; a gzip stream is inflated; a UTF-8 byte-order mark is dropped.
Then text that starts with `{` is JSON, and anything else is CSV.

- **CSV**, CMS's v2 and v3 templates: the header is the first row near the top that names `description` and `code|1`, below the two rows of the hospital's details, and columns are read by name.
  The tall layout has a row per payer and plan (`payer_name`, `plan_name`, `standard_charge|negotiated_dollar`, `standard_charge|negotiated_percentage`, `standard_charge|methodology`).
  The wide layout has a column group per payer and plan (`standard_charge|<payer>|<plan>|negotiated_dollar` and the rest), read from the header once.
- **JSON**, v2 and v3: the decoder walks past every top-level key but `standard_charge_information`, and decodes that array one item at a time.
  An item's codes are its `code_information`, and each of its `standard_charges` is read as one CSV row is, with its `payers_information`.
  Numbers may come as JSON numbers or as strings.

A file with no header or no `standard_charge_information`, or that yields no rows, fails.

## Output - `data/ingestion/hospital_rates/<ccn>.parquet`

The columns are in [schema.md](../../docs/schema.md), with the other datasets `make reference` writes.
An item is a CSV row or a JSON `standard_charges` entry.

- It gives one row per distinct kept code for each kept payer and plan with a dollar amount or a percentage, carrying the item's gross charge and cash price too.
- An item no kept payer prices gives one row per kept code with its gross charge and cash price, and `payer` and `plan` null, when it has either.
- A row the file has already given is dropped, and the log counts them.
  A tall file repeats an item's charges on every payer's line, often far apart, and many files list one code and price under several items, which the rows can't tell apart.
- `code_type` is `CPT`, `HCPCS` or `MS-DRG`, which `DRG`, `MS DRG` and `MSDRG` are read as; every other type, APR-DRG among them, is dropped.
  Some hospitals label their CPT codes `HCPCS`, which is kept as they give it.
- `code` is upper-cased and trimmed, and a code of the wrong shape is dropped.
  A CPT or HCPCS code is five letters or digits as the file gives it.
  A shorter number is dropped, not padded: it could be an anesthesia code that lost its leading zeros, or a revenue code or DRG labelled CPT, and a missing price beats a wrong one.
  An MS-DRG is digits, after any `MS-` or `DRG` prefix, padded to three.
- `setting`, `billing_class` and `methodology` are lower-cased, and `billing_class` is null in a file that has no such column.
- `modifiers` are upper-cased, sorted and joined with `|`, as the payer rates' `modifier` is.
- `payer` and `plan` are as the file spells them, trimmed.
- Numbers read as `1234.5`, `1,234.50` or `$1,234.50`, and anything else is null.

No price is judged here: the find-care build decides which rates to show ([build.md](../../integration/build.md)).

## Flags

| Flag | Effect |
|---|---|
| `-test` | write under `data-test/` |
| `-list <path>` | another list (default `ingestion/etl/hospitals.yaml`) |
| `-only <ccn,ccn>` | only these hospitals from the list |
| `-missing` | skip a hospital whose rates file exists; a plain `make reference` passes it |
| `-file <path> -ccn <ccn>` | extract a local file for that CCN, with no download |

## When a hospital fails

Each file is written beside its final name and moved into place once complete, so a reader sees the old file or the new one.
A hospital whose index, download or file fails keeps its previous rates, and a closing warning names it with the reason.
The run fails only when every hospital it tried failed, or when the one `-file` did.
The log has a line per hospital with the rows written, the repeats dropped, and how many of the payer and plan names in its file were kept.

## Dev loop

- **`make test`** runs `tests/e2e/hospital-rates.sh`, which extracts the tall CSV, wide CSV and JSON fixtures in `ingestion/etl/hospitalrates/testdata/` in test mode and checks the rows with DuckDB.
- The package's unit tests build the zip, gzip and byte-order-mark variants themselves, and serve a `cms-hpt.txt` and its files locally.
