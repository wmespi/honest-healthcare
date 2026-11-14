"""integration/families.yaml and the build-only tables made from it
(integration/inputs/families.py)."""
import json
import os

import pytest
import yaml

from integration.inputs.families import read_families
from integration.inputs.service_list import SERVICES_PATH, read_services
from integration.tests.corpus import FAMILIES


def _family(model, name="mri"):
    return next(f for f in model["families"] if f["family"] == name)


def _bill(model, part, name="mri"):
    return next(b for b in _family(model, name)["bills"] if b["part"] == part)


def _set(model, which, **fields):
    _family(model, which).update(fields)


BROKEN = [
    ("a section it doesn't use", lambda m: m.update(extra=1), "has sections the build doesn't use"),
    ("a benefit key", lambda m: m["benefits"].update({"Bad Key": {"name": "n", "label": "l"}}),
     "benefit 'Bad Key' needs"),
    ("a benefit field", lambda m: m["benefits"]["preventive"].update(extra="x"), "benefit 'preventive' needs"),
    ("a kind label", lambda m: m["kinds"].update(lab=""), "kind 'lab' needs"),
    ("no hospital kind", lambda m: m["kinds"].pop("hospital"), r"kinds doesn't declare \['hospital'\]"),
    ("a family key", lambda m: _set(m, "mri", family="MRI"), "family 'MRI' isn't a lower-case key"),
    ("the family shared", lambda m: _set(m, "mri", family="shared"), "family 'shared' isn't"),
    ("a family field", lambda m: _set(m, "mri", colour="red"), r"mri has fields the build doesn't use: \['colour'\]"),
    ("a repeated family", lambda m: _set(m, "mammograms", family="mri"), "family mri repeats"),
    ("no name", lambda m: _set(m, "mri", name=""), "mri has no name"),
    ("groups that aren't a list", lambda m: _set(m, "mri", groups="Magnetic Resonance"),
     "mri's groups must be a list of quoted codes"),
    ("a bill field", lambda m: _bill(m, "scan").update(colour="red"), "mri's scan bill has fields"),
    ("a bill part", lambda m: _bill(m, "scan").update(part="Scan"), "whose part 'Scan' isn't a lower-case key"),
    ("a repeated bill", lambda m: _bill(m, "scan").update(part="all_in"), "mri has two all_in bills"),
    ("a bill label", lambda m: _bill(m, "scan").update(label=""), "mri's scan bill has no label"),
    ("a professional bill with no modifier", lambda m: _bill(m, "scan").pop("modifier"),
     "needs a quoted modifier"),
    ("two bills with one modifier", lambda m: _bill(m, "scan").update(modifier=""),
     "both have modifier ''"),
    ("two bills sharing one of several modifiers", lambda m: _bill(m, "scan").update(modifier=["TC", ""]),
     "both have modifier ''"),
    ("a bill that repeats a modifier", lambda m: _bill(m, "scan").update(modifier=["TC", "TC"]),
     "or a list of them, none repeated"),
    ("an institutional bill with a modifier", lambda m: _bill(m, "facility").update(modifier=""),
     "has no modifier or on_site"),
    ("two institutional bills", lambda m: _family(m)["bills"].append({**_bill(m, "facility"), "part": "again"}),
     "two institutional bills"),
    ("a billing class", lambda m: _bill(m, "scan").update(billing_class="other"), "not professional or institutional"),
    ("a missing without an explain", lambda m: _bill(m, "scan").update(missing={"note": "n"}),
     "missing needs a note and an explain"),
    ("no bills", lambda m: _set(m, "mri", bills=[], hospital=None, elsewhere=[]), "mri has no bills"),
    ("a bill with no benefits", lambda m: _family(m).pop("benefits"), "names no benefits"),
    ("an undeclared benefit", lambda m: _set(m, "mri", benefits=["colour"]),
     r"names benefits \['colour'\], which benefits doesn't declare"),
    ("a hospital list with a repeat", lambda m: _set(m, "mri", hospital=["reading", "reading"]),
     "mri's hospital needs a list of bills, none repeated"),
    ("a hospital part that isn't a bill", lambda m: _set(m, "mri", hospital=["colour"]),
     "mri's hospital names 'colour'"),
    ("the hospital's fee elsewhere", lambda m: _set(m, "mri", elsewhere=[["facility"]]),
     "elsewhere names facility, the hospital's own fee"),
    ("a center of an undeclared kind", lambda m: _family(m)["centers"].update({"261QM1200X": "lab"}),
     "center 261QM1200X is a 'lab', which kinds doesn't declare"),
    ("a hospital center", lambda m: _family(m)["centers"].update({"261QM1200X": "hospital"}),
     "center 261QM1200X is a hospital"),
    ("a center of two kinds", lambda m: (m["kinds"].update(lab="Lab"),
                                         _family(m).update(centers={**_family(m)["centers"], "261QR0200X": "lab"})),
     "center 261QR0200X is a lab for mri but a imaging_center for mammograms"),
    ("evidence codes that aren't codes", lambda m: _set(m, "mri", evidence_codes="72148"),
     "evidence_codes must be a list of quoted codes"),
    ("an unquoted place of service", lambda m: _set(m, "mri", place_of_service=[11]),
     "place_of_service must be a list of quoted codes"),
    ("no place of service", lambda m: _set(m, "mri", place_of_service=[]),
     "place_of_service must be a list of quoted codes"),
    ("no families", lambda m: m.update(families=[]), "lists no families"),
]


@pytest.mark.parametrize("change, error", [(c, e) for _, c, e in BROKEN], ids=[i for i, _, _ in BROKEN])
def test_a_families_file_that_breaks_a_rule_stops_the_build(tmp_path, change, error):
    model = json.loads(json.dumps(FAMILIES))
    change(model)
    path = tmp_path / "families.yaml"
    path.write_text(yaml.safe_dump(model, sort_keys=False))
    with pytest.raises(SystemExit, match=error):
        read_families(str(path))


def test_the_corpus_families_read_with_their_defaults(corpus):
    model = read_families(corpus.families_file)
    mri, visits = (next(f for f in model["families"] if f["family"] == n) for n in ("mri", "office_visits"))
    assert mri["place_of_service"] == ["11"] and mri["hospital"] == ["facility", "reading"]
    assert [b["part"] for b in visits["bills"]] == ["visit"] and visits["hospital"] is None
    assert visits["bills"][0]["benefits"] == ["primary_care", "specialist"]


def test_a_code_nucc_no_longer_lists_stops_the_build(corpus):
    corpus.taxonomies[:] = [t for t in corpus.taxonomies if t[0] != "261QR0207X"]
    corpus.taxonomy("261QP2000X", "Clinic/Center", "Physical Therapy")  # rewrites the file without it
    with pytest.raises(SystemExit, match=r"NUCC no longer lists \['261QR0207X'\]"):
        corpus.build()


def test_the_centers_are_each_family_s_codes_with_their_kind(corpus):
    rows = corpus.inputs().execute("SELECT family, taxonomy_code, kind FROM centers ORDER BY 1, 2").fetchall()
    assert ("mri", "261QM1200X", "imaging_center") in rows
    assert ("mammograms", "261QR0206X", "imaging_center") in rows
    assert not [r for r in rows if r[0] == "mri" and r[1] == "261QR0206X"]


def test_the_real_families_and_services_files_load_together():
    model = read_families()
    ids = [f["family"] for f in model["families"]]
    assert len(set(ids)) == len(ids)
    services = read_services()
    assert services and all(s["family"] in ids for s in services)
    service_ids = [s["service_id"] for s in services]
    assert len(set(service_ids)) == len(service_ids)
    assert os.path.exists(SERVICES_PATH)
