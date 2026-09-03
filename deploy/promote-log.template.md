# Promote log — the tailnet tier

`scripts/promote.sh` copies this file to `deploy/promote-log.md` (gitignored, one
per machine) on the first promote and appends an entry each time. Each entry is
the date, the `old → new` short shas, the ref promoted, the `tailnet-<date>` tag,
and a `git shortlog` of what went out.

The shareable record is the pushed `tailnet-<date>` tags (`git push origin
tailnet-20260903-1412`); this file is the local, human-readable ledger.

---
<!-- entries are appended below this line, newest last -->
