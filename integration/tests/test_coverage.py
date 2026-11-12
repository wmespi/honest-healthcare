"""`make coverage` (integration/coverage.py): what the build keeps at each step."""
import os

from integration.coverage import STEPS, coverage, show

BV = "45334-GAN001"


def funnel(corpus, by_service=False):
    corpus.flush()
    return coverage(corpus.root, by_service, corpus.services_file)


def test_the_funnel_counts_what_each_step_keeps_and_writes_nothing(corpus):
    # A second fee schedule over Midtown with no Medicare evidence on its roster is
    # still one pair, and an MRI center Medicare has no record of is a new one.
    corpus.roster(BV, 30, 30, "1000000001", 2000000030)
    corpus.scan_price(BV, 30, "72148", all_in=640)
    corpus.roster(BV, 10, 10, "1000000010", 2000000010)
    corpus.scan_price(BV, 10, "72148", all_in=450)
    corpus.npi(1000000010, "CENTER 10", "10 MAIN ST", "ATLANTA", "303082233", taxonomy="261QM1200X")
    assert funnel(corpus) == [{
        "network_id": BV,
        "priced": 9,           # the eight billers and the center
        "evidenced": 8,        # 1000000008 has no Medicare billing and isn't a center
        "medicare_roster": 7, "medicare_biller": 0, "registered": 1,
        "placed": 6,
        "on_map": 4,           # the center's address and South Georgia Radiology's aren't geocoded
        "shown": 4, "several_rates": 0, "with_clinician": 1, "places": 4}]
    assert not os.path.exists(f"{corpus.root}/serving")


def test_the_funnel_by_service_prints_one_line_each(corpus, capsys):
    rows = funnel(corpus, by_service=True)
    assert [(r["network_id"], r["service_id"], r["priced"], r["shown"]) for r in rows] == [
        (BV, "mri-lower-back", 8, 4)]
    show(rows, by_service=True)
    assert capsys.readouterr().out.splitlines()[-1].split() == ["mri-lower-back", "8", "7", "4", "4", "0"]
    show(funnel(corpus))
    out = capsys.readouterr().out
    assert all(label in out for label in STEPS.values())
