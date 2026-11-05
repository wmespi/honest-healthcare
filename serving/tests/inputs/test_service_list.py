"""The build-only service_list table (build/inputs/service_list.py) and
build/services.yaml."""
import duckdb
import pytest

from build.inputs.service_list import SERVICES_PATH, ServiceList, read_services


@pytest.mark.parametrize("service_id, billing_code, fields, error", [
    ("MRI Brain", "70551", {}, r"'MRI Brain' isn't a lower-case slug"),
    ("mri-brain", "7055", {}, r"mri-brain's billing_code '7055' isn't a five-character code"),
    ("mri-low-back", "72148", {}, r"\['mri-low-back', 'mri-lower-back'\] all bill 72148"),
    ("mri-brain", "70551", {"asks": []}, "mri-brain has an empty asks"),
    ("mri-brain", "70551", {"could_change": ["Contrast dye", " "]}, "mri-brain has an empty could_change"),
    ("mri-brain", "70551", {"about": ""}, "mri-brain has an empty about"),
    ("mri-brain", "70551", {"service_category": None}, "mri-brain has an empty service_category"),
    ("mri-brain", "70551", {"colour": "red"}, r"mri-brain has fields the screen doesn't use: \['colour'\]"),
])
def test_a_service_that_breaks_a_rule_stops_the_build(corpus, service_id, billing_code, fields, error):
    corpus.service(service_id, billing_code, **fields)
    with pytest.raises(SystemExit, match=error):
        corpus.build()


def test_the_services_file_passes_its_checks():
    """build/services.yaml itself: the 26 scans of the POC, without pregnancy
    ultrasound (#135)."""
    ServiceList(SERVICES_PATH).make(duckdb.connect(), data_dir=None)
    codes = [s["billing_code"] for s in read_services()]
    assert len(codes) == 26 and "76805" not in codes
