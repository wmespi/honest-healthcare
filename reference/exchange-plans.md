# `make reference STEP=exchange-plans`: Georgia's individual-market plans

*Read this when working on which plans we price, a plan's name or network, or premiums.*

**Builds** three files from the [Georgia Access public use files](https://georgiaaccess.gov/public-use-files/),
module `reference/exchange_plans.py`:

- `data/reference/exchange_plans_ga.parquet`: one row per plan, keyed on its HIOS plan ID
  (`45334GA0020002`), with its name, carrier, metal level, plan type, referral rule and network.
- `data/reference/exchange_networks_ga.parquet`: one row per provider network those plans use,
  keyed on issuer and network ID (`45334-GAN001`).
- `data/reference/exchange_premiums_ga.parquet`: the monthly premium for one person, by plan,
  rating area and age, with and without tobacco use.

**Why.** Every plan anyone can buy on Georgia's exchange is here, keyed on the same HIOS plan
IDs the insurers' indexes list, so it says which plans exist and which network each one uses.
Anthem's index names a plan only with an internal label ("GAINDNTWK OA RQ"), and Georgia runs
its own exchange, so its plans aren't in CMS's federal files. This is the only source of a
plan's real name, such as Anthem Silver Blue Value HMO 4000.

`etl parse` reads `exchange_plans_ga.parquet` to turn the exchange networks in
[`etl/targets.yaml`](../etl/targets.yaml) into plan IDs and parse the files that price
them ([etl/parse.md](../etl/parse.md#target-selection)). Nothing else reads these files yet.

## What is kept

- **Medical plans only.** Dental-only plans are dropped.
- **One row per plan.** A plan's cost-sharing versions (`-01` to `-06`, for people with extra
  subsidies) change what the member pays, not the network or the rates, so they share a row
  named after the standard version (`-01`).
- **Premiums for every age.** The file prices children as one band (`0-14`) and adults 64 and
  over as one, so each age gets its own row and a lookup uses `min(age, 64)`. A plan with no
  tobacco surcharge repeats its premium as `tobacco_premium`.

The builder stops if a key repeats, if a plan's network isn't in the network file, or if a file
is for a different plan year than `--year`.

## Not covered

- **Only the individual market sold on the exchange.** Employer plans, Medicare, Medicaid and
  plans sold only off the exchange aren't in these files.
- **Rating areas are numbers.** Georgia defines its 16 areas by county, and nothing here turns a
  ZIP code or county into a rating area yet.
- **Other files on the page aren't read yet:** each plan's cost sharing by service (`BENCS`),
  service areas (`SA`), last year's plan IDs mapped to this year's (`CW`), and each insurer's
  machine-readable provider directory (`MR`).

## Re-run each plan year

Georgia publishes next year's files before open enrollment and republishes a file under a new
timestamped name when it changes. The builder reads the page and takes the newest file for
`--year`, which defaults to the current calendar year:

`make reference STEP=exchange-plans ARGS="--year 2027"`

Downloads are cached under `data/reference/.cache/` by their published name, so a republished
file is fetched again and an unchanged one isn't. The site refuses Python's default User-Agent,
so the builder sends its own.
