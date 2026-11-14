"""`make coverage` (integration/coverage.py): what the build keeps at each step."""
import os

from integration.coverage import STEPS, coverage, show

BV, PATHWAY = "45334-GAN001", "49046-GAN008"


def funnel(corpus, by_service=False):
    corpus.flush()
    return coverage(corpus.root, by_service, corpus.services_file, corpus.hospital_plans_file,
                    corpus.families_file)


def test_the_funnel_counts_what_each_step_keeps_and_writes_nothing(corpus):
    # A second fee schedule over Midtown with no Medicare evidence on its roster is
    # still one pair, and an MRI center Medicare has no record of is a new one.
    corpus.roster(BV, 30, 30, "1000000001", 2000000030)
    corpus.scan_price(BV, 30, "72148", all_in=640)
    corpus.roster(BV, 10, 10, "1000000010", 2000000010)
    corpus.scan_price(BV, 10, "72148", all_in=450)
    corpus.npi(1000000010, "CENTER 10", "10 MAIN ST", "ATLANTA", "303082233", taxonomy="261QM1200X")
    # Pathway's file prices Hamilton's own fee for an MRI, where no group has a rate.
    corpus.npi(1000000051, "HAMILTON HEALTH CARE SYSTEM", "1200 MEMORIAL DR", "DALTON", "30720",
               taxonomy="282N00000X")
    corpus.roster(PATHWAY, 52, 52, "1000000051", 1000000051)
    corpus.facility_price(PATHWAY, 52, "72148", 900)
    assert funnel(corpus) == [{
        "network_id": BV,
        "priced": 10,          # the eight billers and the center for the MRI, and Grady's readers for a mammogram
        "evidenced": 9,        # 1000000008 has no Medicare billing and isn't a center
        "medicare_roster": 8, "medicare_biller": 0, "registered": 1,
        "placed": 7,
        "on_map": 5,           # the center's address and South Georgia Radiology's aren't geocoded
        "shown": 5, "several_rates": 0, "with_clinician": 1, "places": 4,
        "at_hospital": 2,      # Grady's MRI, with no fee, and its mammogram
        "fee_and_doctor": 1, "fee_only": 0, "doctor_only": 1,
        "fee_plan": 0, "fee_hospital": 1, "cash_price": 2,
    }, {
        "network_id": PATHWAY,
        "priced": 1, "evidenced": 1, "medicare_roster": 1, "medicare_biller": 0, "registered": 0,
        "placed": 1, "on_map": 1, "shown": 3, "several_rates": 0, "with_clinician": 0, "places": 2,
        "at_hospital": 3,      # Grady's mammogram, and Hamilton's mammogram and MRI, which no group reads
        "fee_and_doctor": 1, "fee_only": 2, "doctor_only": 0,
        "fee_plan": 3, "fee_hospital": 0, "cash_price": 1,
    }]
    # Pathway lists no professional rate for the MRI, and its line still shows Hamilton's row.
    assert [(r["priced"], r["shown"], r["fee_only"]) for r in funnel(corpus, by_service=True)
            if (r["network_id"], r["service_id"]) == (PATHWAY, "mri-lower-back")] == [(0, 1, 1)]
    assert not os.path.exists(f"{corpus.root}/serving")


def test_the_funnel_by_service_prints_one_line_each(corpus, capsys):
    rows = funnel(corpus, by_service=True)
    assert [(r["network_id"], r["service_id"], r["priced"], r["shown"]) for r in rows] == [
        (BV, "mammogram-screening", 1, 1), (BV, "mri-lower-back", 8, 4), (PATHWAY, "mammogram-screening", 1, 2)]
    show(rows, by_service=True)
    assert capsys.readouterr().out.splitlines()[-1].split() == ["mammogram-screening", "1", "1", "1", "2", "0"]
    show(funnel(corpus))
    out = capsys.readouterr().out
    assert all(label in out for label in STEPS.values())
