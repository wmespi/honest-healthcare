"""Anthem's rates by Georgia network (ingestion/anthem_rates.py): which parsed
file and labels price each target network, what the step publishes, and how
fresh it says each network's rates are."""
import os
import subprocess
import sys

import pytest

from ingestion.anthem_rates import read_targets, warning
from ingestion.tests.conftest import _RECORD

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

BLUE_VALUE_LABEL = "GA Blue Value HIX Individual Network"
BLUE_VALUE_CURRENT = ("45334-GAN001", "current", "GA_BV.json.gz", "2026-10-31", None)
NOV1 = "2026-11-01 04:00:00+00"


def test_each_network_gets_its_files_rates_and_the_groups_they_use(anthem):
    assert anthem.publish() == {"rates": 2, "rate_groups": 2, "rosters": 3, "networks": 2}
    # Pathway's file also prices an untargeted network, whose rate and group stay out.
    assert [(r[0], r[1]) for r in anthem.rows("rates")] == [("45334-GAN001", 1), ("49046-GAN008", 5)]
    assert anthem.rows("rate_groups") == [("45334-GAN001", 1, 10), ("49046-GAN008", 5, 50)]
    assert anthem.rows("rosters") == [
        ("45334-GAN001", 10, 2000000001, "npi", "1000000001"),
        ("45334-GAN001", 10, 2000000002, "npi", "1000000001"),
        ("49046-GAN008", 50, 2000000005, "npi", "1000000005")]
    assert anthem.networks() == [BLUE_VALUE_CURRENT,
                                 ("49046-GAN008", "current", "GA_PW.json.gz", "2026-10-31", None)]
    assert warning(anthem.out) == ""


def test_the_same_files_publish_the_same_bytes(anthem):
    anthem.publish()
    names = ("rates", "rate_groups", "networks")
    first = {n: open(f"{anthem.out}/{n}.parquet", "rb").read() for n in names}
    anthem.publish()
    assert {n: open(f"{anthem.out}/{n}.parquet", "rb").read() for n in names} == first


def test_a_network_whose_file_failed_its_first_parse_is_left_out_and_says_why(anthem):
    anthem.remove_parquet(701)
    anthem.record(701, "failed", "malformed MRF: in_network[0]: unexpected EOF", rates=None)
    assert anthem.publish() == {"rates": 1, "rate_groups": 1, "rosters": 2, "networks": 2}
    assert {r[0] for r in anthem.rows("rates")} == {"45334-GAN001"}
    assert anthem.networks() == [BLUE_VALUE_CURRENT, (
        "49046-GAN008", "missing", None, None,
        "the latest parse of GA_PW.json.gz, on 2026-11-07 18:14:12, failed: malformed MRF: in_network[0]: unexpected EOF")]
    assert "49046-GAN008 has no rates, so nothing is published for it: the latest parse of GA_PW" in warning(anthem.out)


def test_a_network_whose_newer_copy_failed_keeps_its_rates_and_says_they_are_stale(anthem):
    anthem.record(701, "failed", "short read: got 10 of 20 compressed bytes", read=NOV1)
    assert anthem.publish()["rates"] == 2
    assert anthem.networks()[1] == (
        "49046-GAN008", "stale", "GA_PW.json.gz", "2026-10-31",
        "the latest parse of GA_PW.json.gz, on 2026-11-07 18:14:12, failed: short read: got 10 of 20 compressed bytes")
    assert ("49046-GAN008's rates are from the copy of GA_PW.json.gz Anthem updated on 2026-10-31: "
            "the latest parse") in warning(anthem.out)


def test_a_network_whose_newer_copy_the_probe_skipped_is_stale(anthem):
    # Anthem renamed the network, so the probe skipped its next copy.
    anthem.record(21057, "skipped", "probe: no wanted providers - file carries {GA Blue Value Renamed}", read=NOV1)
    anthem.publish()
    assert anthem.networks()[0] == (
        "45334-GAN001", "stale", "GA_BV.json.gz", "2026-10-31",
        "the latest parse of GA_BV.json.gz, on 2026-11-07 18:14:12, skipped it: probe: no wanted providers - "
        "file carries {GA Blue Value Renamed}")


def test_a_parsed_record_from_before_rates_updated_at_dates_the_copy_it_read(anthem):
    old = {c: t for c, t in _RECORD.items() if c != "rates_updated_at"}
    for file_id in (21057, 701):
        anthem.record(file_id, "parsed", columns=old)
    anthem.publish()
    assert anthem.networks()[0] == BLUE_VALUE_CURRENT


def test_rates_with_no_parse_record_are_stale_since_nothing_dates_them(anthem):
    anthem.remove_record(701)
    anthem.publish()
    assert anthem.networks()[1] == ("49046-GAN008", "stale", "GA_PW.json.gz", None,
                                    "GA_PW.json.gz has no parse record, so nothing says which copy its rates are from")
    assert "Anthem updated on a date it did not give" in warning(anthem.out)


def test_a_network_whose_file_was_never_parsed_is_left_out(anthem):
    anthem.remove_parquet(701)
    anthem.remove_record(701)
    anthem.publish()
    assert anthem.networks()[1] == ("49046-GAN008", "missing", None, None, "GA_PW.json.gz hasn't been parsed")


def test_a_run_that_prices_no_network_stops_the_step(anthem):
    anthem.publish()
    before = open(f"{anthem.out}/networks.parquet", "rb").read()
    for file_id in (21057, 701):
        anthem.remove_parquet(file_id)
        anthem.record(file_id, "failed", "HTTP 503", read=None, rates=None)
    with pytest.raises(SystemExit, match=r"(?s)no target network has rates, so there's nothing to publish:\n"
                                         r"  45334-GAN001: the latest parse of GA_BV.json.gz, .* failed: HTTP 503\n"
                                         r"  49046-GAN008: .*GA_PW.json.gz"):
        anthem.publish()
    assert open(f"{anthem.out}/networks.parquet", "rb").read() == before


def test_labels_match_the_way_the_parse_probe_matches_them(anthem):
    # Trimmed and case-insensitive, with `*` the only wildcard: `?` is a literal.
    anthem.remove_rate(21057, BLUE_VALUE_LABEL)
    anthem.rate(21057, "  ga blue value hix individual network ")
    assert anthem.publish()["rates"] == 2
    with open(anthem.targets) as f:
        text = f.read()
    with open(anthem.targets, "w") as f:
        f.write(text.replace("Pathway PCP Copay Choice*", "Pathway?PCP Copay Choice*"))
    assert anthem.publish()["rates"] == 1
    assert anthem.networks()[1] == (
        "49046-GAN008", "missing", None, None,
        "GA_PW.json.gz carries no label matching ['Pathway?PCP Copay Choice*'], "
        "only Pathway PCP Copay Choice, TRADITIONAL HEALTH PLAN")


def test_a_facility_label_adds_only_its_institutional_rates_and_their_groups(anthem):
    # Pathway's file prices its hospitals under another label, which also carries
    # doctors' rates the target doesn't claim.
    label = "EXCHANGES SPECIALIST  GATEKEEPER  ON INDIVIDUAL"
    anthem.add_target("    facility_patterns: [\"Exchanges Specialist*Gatekeeper*On Individual\"]\n")
    anthem.rate(701, label, group_set=7, rate=900.0, billing_class="institutional")
    anthem.rate(701, label, group_set=8, rate=60.0)
    anthem.group(701, 7, 70, "1000000007", 2000000007)
    anthem.group(701, 8, 80, "1000000008", 2000000008)
    assert anthem.publish() == {"rates": 3, "rate_groups": 3, "rosters": 4, "networks": 2}
    assert [(r[1], r[6], r[9]) for r in anthem.rows("rates") if r[0] == "49046-GAN008"] == [
        (5, 250.0, "professional"), (7, 900.0, "institutional")]
    assert ("49046-GAN008", 70, 2000000007, "npi", "1000000007") in anthem.rows("rosters")
    # The facility label alone doesn't make a file the network's.
    anthem.remove_rate(701, "Pathway PCP Copay Choice")
    assert anthem.publish()["rates"] == 1
    assert anthem.networks()[1][1] == "missing"


def test_a_file_the_target_doesnt_name_is_left_out(anthem):
    # GA_JBKEMED0001 carries Blue Value's label, but the index links it only to
    # 45334GA0010112, a plan Georgia's files no longer list.
    anthem.rate(31628, BLUE_VALUE_LABEL, group_set=9)
    anthem.link(31628, "45334GA0010112")
    # And a file someone parsed by name, which the index does link to Blue Value.
    anthem.rate(21000, BLUE_VALUE_LABEL, group_set=8)
    anthem.link(21000, "45334GA0020008")
    anthem.file(21000, "GA_OTHER.json.gz")
    assert anthem.publish()["rates"] == 2
    assert anthem.networks()[0] == BLUE_VALUE_CURRENT


def test_two_named_files_with_the_networks_label_stop_the_step(anthem):
    anthem.rate(21000, BLUE_VALUE_LABEL)
    anthem.link(21000, "45334GA0020008")
    anthem.file(21000, "GA_OTHER.json.gz")
    with open(anthem.targets) as f:
        text = f.read()
    with open(anthem.targets, "w") as f:
        f.write(text.replace('files: ["GA_BV.json.gz"]', 'files: ["GA_BV.json.gz", "GA_OTHER.json.gz"]'))
    with pytest.raises(SystemExit, match=r"names GA_BV.json.gz, GA_OTHER.json.gz, which the index links to plans "
                                         r"on 45334-GAN001 .* but one file prices 45334-GAN001"):
        anthem.publish()


def test_a_named_file_the_index_stopped_linking_stops_the_step(anthem):
    anthem.unlink(21057)
    with pytest.raises(SystemExit, match=r"Blue Value: this month's index doesn't link GA_BV.json.gz to a plan "
                                         r"on 45334-GAN001, so nothing .* names prices it"):
        anthem.publish()


def test_a_plan_the_index_doesnt_link_to_its_networks_file_stops_the_step(anthem):
    anthem.plan("45334GA0020012", "45334-GAN001")
    with pytest.raises(SystemExit, match=r"doesn't link 45334GA0020012 to file 21057, which prices "
                                         r"its network 45334-GAN001"):
        anthem.publish()


def test_a_failed_check_leaves_the_last_rates_as_they_were(anthem):
    anthem.publish()
    before = open(f"{anthem.out}/rates.parquet", "rb").read()
    anthem.unlink(21057)
    with pytest.raises(SystemExit):
        anthem.publish()
    assert open(f"{anthem.out}/rates.parquet", "rb").read() == before


def test_two_targets_naming_one_network_stop_the_step(anthem):
    anthem.add_target('  - name: Blue Value again\n    exchange_networks: ["45334-GAN001"]\n'
                      '    files: ["GA_BV.json.gz"]\n    network_patterns: ["GA Blue Value HIX*"]\n')
    with pytest.raises(SystemExit, match=r"targets \['Blue Value', 'Blue Value again'\] each name 45334-GAN001"):
        anthem.publish()


def test_a_blank_network_in_the_targets_file_stops_the_step(anthem):
    anthem.add_target('  - name: Blank\n    exchange_networks:\n      -\n'
                      '    files: ["GA_BV.json.gz"]\n    network_patterns: ["GA Blue Value HIX*"]\n')
    with pytest.raises(SystemExit, match="target 'Blank' names a blank exchange network"):
        anthem.publish()


def test_a_target_that_names_no_files_stops_the_step(anthem):
    anthem.add_target('  - name: Fileless\n    exchange_networks: ["58081-GAN001"]\n'
                      '    network_patterns: ["Individual Georgia HMO*"]\n')
    with pytest.raises(SystemExit, match="target 'Fileless' names no files, so nothing can price its networks"):
        anthem.publish()


def test_a_network_georgia_doesnt_list_stops_the_step(anthem):
    anthem.add_target('  - name: Typo\n    exchange_networks: ["45334-GAN009"]\n'
                      '    files: ["GA_BV.json.gz"]\n    network_patterns: ["GA Blue Value HIX*"]\n')
    with pytest.raises(SystemExit, match="45334-GAN009 isn't a network in Georgia's exchange files"):
        anthem.publish()


def test_a_network_with_no_plans_stops_the_step(anthem):
    anthem.add_target('  - name: Untargeted\n    exchange_networks: ["58081-GAN001"]\n'
                      '    files: ["GA_PW.json.gz"]\n    network_patterns: ["Individual Georgia HMO*"]\n')
    anthem.remove_plans("58081-GAN001")
    with pytest.raises(SystemExit, match="no plan in Georgia's exchange plan file is on 58081-GAN001"):
        anthem.publish()


def test_every_target_in_the_targets_file_names_networks_files_and_labels():
    """ingestion/etl/targets.yaml itself: every target pairs exchange networks with files and labels."""
    for name, nets, files, patterns, _ in read_targets():
        assert nets and files and patterns, name


def test_ingestion_imports_nothing_from_the_layers_after_it():
    r = subprocess.run([sys.executable, "-c",
                        "import importlib, pkgutil, sys, ingestion.anthem_rates, ingestion.reference; "
                        "[importlib.import_module(f'ingestion.reference.{m.name}') "
                        " for m in pkgutil.iter_modules(ingestion.reference.__path__) if m.name != 'tests']; "
                        "print(sorted(m for m in sys.modules if m.split('.')[0] in ('integration', 'application')))"],
                       cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "[]"
