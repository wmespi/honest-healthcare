"""Anthem's rates by Georgia network (ingestion/anthem_rates.py): which parsed
file and labels price each target network, and what the step publishes."""
import os
import subprocess
import sys

import pytest

from ingestion.anthem_rates import read_targets

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

BLUE_VALUE_LABEL = "GA Blue Value HIX Individual Network"


def test_each_network_gets_its_files_rates_and_the_groups_they_use(anthem):
    assert anthem.publish() == {"rates": 2, "rate_groups": 2, "rosters": 3}
    # Pathway's file also prices an untargeted network, whose rate and group stay out.
    assert [(r[0], r[1]) for r in anthem.rows("rates")] == [("45334-GAN001", 1), ("49046-GAN008", 5)]
    assert anthem.rows("rate_groups") == [("45334-GAN001", 1, 10), ("49046-GAN008", 5, 50)]
    assert anthem.rows("rosters") == [
        ("45334-GAN001", 10, 2000000001, "npi", "1000000001"),
        ("45334-GAN001", 10, 2000000002, "npi", "1000000001"),
        ("49046-GAN008", 50, 2000000005, "npi", "1000000005")]


def test_the_same_files_publish_the_same_bytes(anthem):
    anthem.publish()
    first = {n: open(f"{anthem.out}/{n}.parquet", "rb").read() for n in ("rates", "rate_groups")}
    anthem.publish()
    assert {n: open(f"{anthem.out}/{n}.parquet", "rb").read() for n in first} == first


def test_labels_match_the_way_the_parse_probe_matches_them(anthem):
    # Trimmed and case-insensitive, with `*` the only wildcard: `?` is a literal.
    anthem.remove_rate(21057, BLUE_VALUE_LABEL)
    anthem.rate(21057, "  ga blue value hix individual network ")
    assert anthem.publish()["rates"] == 2
    with open(anthem.targets) as f:
        text = f.read()
    with open(anthem.targets, "w") as f:
        f.write(text.replace("Pathway PCP Copay Choice*", "Pathway?PCP Copay Choice*"))
    with pytest.raises(SystemExit, match="nothing prices 49046-GAN008"):
        anthem.publish()


def test_a_file_the_index_doesnt_link_to_the_networks_plans_is_left_out(anthem):
    # GA_JBKEMED0001 carries Blue Value's label, but the index links it only to
    # 45334GA0010112, a plan Georgia's files no longer list.
    anthem.rate(31628, BLUE_VALUE_LABEL, group_set=9)
    anthem.link(31628, "45334GA0010112")
    assert anthem.publish()["rates"] == 2


def test_a_second_linked_file_with_the_networks_label_stops_the_step(anthem):
    anthem.rate(21000, BLUE_VALUE_LABEL)
    anthem.link(21000, "45334GA0020008")
    with pytest.raises(SystemExit, match=r"links files \[21000, 21057\] to plans on 45334-GAN001.*"
                                         r"one file prices 45334-GAN001"):
        anthem.publish()


def test_a_parsed_file_the_index_stopped_linking_stops_the_step(anthem):
    anthem.unlink(21057)
    with pytest.raises(SystemExit, match=r"files \[21057\] carry a label matching .* but the index "
                                         r"doesn't link them to a plan on 45334-GAN001"):
        anthem.publish()


def test_a_network_no_parsed_file_prices_stops_the_step(anthem):
    anthem.remove_rate(701, "Pathway PCP Copay Choice")
    with pytest.raises(SystemExit, match="nothing prices 49046-GAN008; run `make parse`"):
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
                      '    network_patterns: ["GA Blue Value HIX*"]\n')
    with pytest.raises(SystemExit, match=r"targets \['Blue Value', 'Blue Value again'\] each name 45334-GAN001"):
        anthem.publish()


def test_a_blank_network_in_the_targets_file_stops_the_step(anthem):
    anthem.add_target('  - name: Blank\n    exchange_networks:\n      -\n'
                      '    network_patterns: ["GA Blue Value HIX*"]\n')
    with pytest.raises(SystemExit, match="target 'Blank' names a blank exchange network"):
        anthem.publish()


def test_a_network_georgia_doesnt_list_stops_the_step(anthem):
    anthem.add_target('  - name: Typo\n    exchange_networks: ["45334-GAN009"]\n'
                      '    network_patterns: ["GA Blue Value HIX*"]\n')
    with pytest.raises(SystemExit, match="45334-GAN009 isn't a network in Georgia's exchange files"):
        anthem.publish()


def test_a_network_with_no_plans_stops_the_step(anthem):
    anthem.add_target('  - name: Untargeted\n    exchange_networks: ["58081-GAN001"]\n'
                      '    network_patterns: ["Individual Georgia HMO*"]\n')
    anthem.remove_plans("58081-GAN001")
    with pytest.raises(SystemExit, match="no plan in Georgia's exchange plan file is on 58081-GAN001"):
        anthem.publish()


def test_every_target_in_the_targets_file_names_networks_and_labels():
    """ingestion/etl/targets.yaml itself: every target pairs exchange networks with labels."""
    for name, nets, patterns in read_targets():
        assert nets and patterns, name


def test_ingestion_imports_nothing_from_the_layers_after_it():
    r = subprocess.run([sys.executable, "-c",
                        "import importlib, pkgutil, sys, ingestion.anthem_rates, ingestion.reference; "
                        "[importlib.import_module(f'ingestion.reference.{m.name}') "
                        " for m in pkgutil.iter_modules(ingestion.reference.__path__) if m.name != 'tests']; "
                        "print(sorted(m for m in sys.modules if m.split('.')[0] in ('integration', 'serving')))"],
                       cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "[]"
