"""The build-only hospital_cash table (integration/inputs/hospital_cash.py): what a
hospital's own price file says it charges for a service when no plan pays."""
GRADY = "110079"


def cash(corpus):
    return corpus.inputs().execute("SELECT * FROM hospital_cash ORDER BY ALL").fetchall()


def test_a_hospitals_cash_price_is_the_highest_on_its_rows_for_the_facility_bill(corpus):
    assert cash(corpus) == [("mammogram-screening", GRADY, 320.0), ("mri-lower-back", GRADY, 1450.0)]
    # A row for a plan counts, and rows for another bill or with a placeholder don't.
    corpus.hospital_price(GRADY, "72148", "ANTHEM BCBS", "BLUE VALUE HMO", negotiated=1200.0, cash=1500.0)
    for left_out in [dict(billing_class="professional", cash=2000.0), dict(setting="inpatient", cash=3000.0),
                     dict(modifiers="26|TC", cash=1600.0), dict(cash=99999.0), dict(cash=0.5)]:
        corpus.hospital_price(GRADY, "72148", **left_out)
    assert cash(corpus) == [("mammogram-screening", GRADY, 320.0), ("mri-lower-back", GRADY, 1500.0)]
