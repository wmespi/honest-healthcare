"""The build-only service_list table (integration/inputs/service_list.py) and
integration/services.yaml."""
import re

import pytest

from integration.inputs.families import read_families
from integration.inputs.service_list import read_services


@pytest.mark.parametrize("service_id, billing_code, fields, error", [
    ("MRI Brain", "70551", {}, r"'MRI Brain' isn't a lower-case slug"),
    ("mri-brain", "7055", {}, r"mri-brain's billing_code '7055' isn't a five-character code"),
    ("mri-low-back", "72148", {}, r"\['mri-low-back', 'mri-lower-back'\] all bill 72148"),
    ("mri-brain", "70551", {"asks": []}, "mri-brain has an empty asks"),
    ("mri-brain", "70551", {"could_change": ["Contrast dye", " "]}, "mri-brain has an empty could_change"),
    ("mri-brain", "70551", {"about": ""}, "mri-brain has an empty about"),
    ("mri-brain", "70551", {"benefits": ["colour"]}, r"mri-brain names benefits \['colour'\]"),
    ("mri-brain", "99213", {}, r"mri-brain's billing_code 99213 is 'Office E&M - Established'"
                                r".*which isn't in mri"),
    ("mri-brain", "70551", {"colour": "red"}, r"mri-brain has fields the screen doesn't use: \['colour'\]"),
])
def test_a_service_that_breaks_a_rule_stops_the_build(corpus, service_id, billing_code, fields, error):
    corpus.service(service_id, billing_code, **fields)
    with pytest.raises(SystemExit, match=error):
        corpus.build()


def test_the_services_file_passes_its_checks():
    """integration/services.yaml itself: every service is in a declared family and
    has a code of its own there (the check against CMS's classification needs the
    corpus, so it runs in the build's tests)."""
    services = read_services()
    families = {f["family"] for f in read_families()["families"]}
    assert services and all(s["family"] in families for s in services)
    assert all(re.fullmatch("[0-9A-Z]{5}", s["billing_code"]) for s in services)
    assert len({s["service_id"] for s in services}) == len(services)
    assert len({(s["family"], s["billing_code"]) for s in services}) == len(services)


def bill_benefits(corpus, service_id):
    from integration.inputs.service_list import SERVICE_BILLS
    return dict(corpus.inputs().execute(
        f"SELECT part, benefits FROM ({SERVICE_BILLS}) WHERE service_id = ?", [service_id]).fetchall())


def test_a_services_benefits_beat_its_bills_which_beat_its_familys(corpus):
    corpus.family("mri", bills=[{**b, "benefits": ["specialist"]} if b["part"] == "scan" else b
                                for b in corpus.families["families"][-1]["bills"]])
    corpus.service("mri-brain", "70551", benefits=["preventive"])
    # The family's, except the bill that names its own.
    assert bill_benefits(corpus, "mri-lower-back") == {
        "all_in": ["advanced_imaging"], "scan": ["specialist"], "reading": ["advanced_imaging"],
        "facility": ["advanced_imaging"]}
    # The service's own, for every bill.
    assert set(map(tuple, bill_benefits(corpus, "mri-brain").values())) == {("preventive",)}
