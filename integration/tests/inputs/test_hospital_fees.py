"""The build-only hospital_fees table (integration/inputs/hospital_fees.py): a
hospital's own fee for a service on a network, from the plan's own file, else the
hospital's own price file."""
BV, PATHWAY = "45334-GAN001", "49046-GAN008"
GRADY, HAMILTON = "110079", "110001"
MRI, MAMMOGRAM = "mri-lower-back", "mammogram-screening"
PLANS = {"hospitals": [GRADY], "networks": {BV: ["ANTHEM BCBS | BLUE VALUE HMO"], PATHWAY: ["ANTHEM BCBS | PATHWAY"]},
         "other": ["ANTHEM BCBS | MEDICARE ADVANTAGE"]}


def fees(corpus, service_id=None):
    rows = corpus.inputs().execute("SELECT * FROM hospital_fees ORDER BY ALL").fetchall()
    return [r for r in rows if service_id in (None, r[1])]


def hospital_npi(corpus, npi, group_set, address, city, postal_code, taxonomy="282N00000X"):
    """An organization NPPES registers as a hospital (taxonomy 28x) on `group_set` on Pathway."""
    corpus.npi(npi, f"HOSPITAL {npi}", address, city, postal_code, taxonomy=taxonomy)
    corpus.roster(PATHWAY, group_set, group_set, str(npi), npi)


def test_the_default_corpus_prices_gradys_mammogram_fee_from_each_source_and_hamiltons_from_one(corpus):
    assert fees(corpus) == [(BV, MAMMOGRAM, GRADY, 210.0, 1, "hospital"), (PATHWAY, MAMMOGRAM, HAMILTON, 165.0, 1, "plan"),
                            (PATHWAY, MAMMOGRAM, GRADY, 180.0, 1, "plan")]


def test_the_plans_file_prices_a_hospital_its_hospital_npi_lists_an_address_in(corpus, capsys):
    # Hamilton's system NPI has a billing office on its record and lists the
    # hospital as another address. An imaging center at Hamilton's address isn't a
    # hospital, and a hospital NPI listing no hospital's building prices none.
    hospital_npi(corpus, 1000000051, 51, "9 BILLING OFFICE WAY", "DALTON", "30720")
    corpus.location(1000000051, "1200 MEMORIAL DRIVE", "DALTON", "30720")
    hospital_npi(corpus, 1000000052, 52, "1200 MEMORIAL DR", "DALTON", "30720", taxonomy="261QM1200X")
    hospital_npi(corpus, 1000000053, 53, "5 NOWHERE RD", "MACON", "31201")
    for group_set, rate in ((51, 900), (52, 950), (53, 990)):
        corpus.facility_price(PATHWAY, group_set, "72148", rate)
    assert fees(corpus, MRI) == [(PATHWAY, MRI, HAMILTON, 900.0, 1, "plan")]
    assert ("hospital billing NPIs with an institutional rate but no address in a hospital's building, so they "
            "price no hospital's fee (1): 1000000053") in capsys.readouterr().out


def test_the_plans_file_prices_only_fee_for_service_dollar_amounts_on_an_outpatient_bill_with_no_modifier(corpus):
    for rate, fields in [(1100, {"setting": "both", "negotiated_type": "negotiated"}),
                         (1000, {}),
                         (60, {"negotiated_type": "percentage"}),
                         (2000, {"negotiated_type": "per diem"}),
                         (1500, {"arrangement": "bundle"}),
                         (1800, {"setting": "inpatient"}),
                         (1700, {"modifier": "TC"}),
                         (1, {})]:
        corpus.facility_price(PATHWAY, 50, "72148", rate, **fields)
    corpus.scan_price(PATHWAY, 50, "72148", all_in=2500)  # a professional rate isn't the hospital's fee
    assert fees(corpus, MRI) == [(PATHWAY, MRI, GRADY, 1100.0, 2, "plan")]


def test_the_hospitals_own_file_prices_its_fee_under_a_name_the_list_gives_the_network(corpus):
    corpus.hospital_plans([PLANS])
    blue_value = "ANTHEM BCBS", "BLUE VALUE HMO"
    corpus.hospital_price(GRADY, "72148", *blue_value, negotiated=1200.0)
    corpus.hospital_price(GRADY, "72148", *blue_value, negotiated=1300.0, setting=None, billing_class=None)
    corpus.hospital_price(GRADY, "72148", *blue_value, negotiated=1250.0, setting="both", billing_class="both",
                          code_type="HCPCS")
    for left_out in [dict(billing_class="professional", negotiated=300.0),
                     dict(setting="inpatient", negotiated=5000.0),
                     dict(modifiers="TC", negotiated=800.0),
                     dict(code_type="MS-DRG", negotiated=7000.0),
                     dict(negotiated=999999999.0),  # a placeholder
                     dict(negotiated=1.0),
                     dict(negotiated=7500.0, methodology="percent of total billed charges"),  # the hospital's estimate
                     dict(negotiated=1600.0, methodology="per diem"),
                     dict(negotiated=1650.0, methodology=None),
                     dict(cash=1400.0)]:  # a cash price isn't the plan's rate
        corpus.hospital_price(GRADY, "72148", *blue_value, **left_out)
    corpus.hospital_price(GRADY, "72148", "ANTHEM BCBS", "MEDICARE ADVANTAGE", negotiated=1350.0)
    assert fees(corpus, MRI) == [(BV, MRI, GRADY, 1300.0, 3, "hospital")]


def test_the_plans_file_beats_the_hospitals_own(corpus):
    # Grady's file prices both services on Pathway, whose own file prices only
    # Grady's mammogram fee.
    corpus.hospital_plans([PLANS])
    corpus.hospital_price(GRADY, "77067", "ANTHEM BCBS", "PATHWAY", negotiated=500.0)
    corpus.hospital_price(GRADY, "72148", "ANTHEM BCBS", "PATHWAY", negotiated=1250.0)
    assert [r for r in fees(corpus) if r[0] == PATHWAY and r[2] == GRADY] == [
        (PATHWAY, MAMMOGRAM, GRADY, 180.0, 1, "plan"), (PATHWAY, MRI, GRADY, 1250.0, 1, "hospital")]
