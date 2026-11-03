"""The build-only targets table (build/tables/targets.py) and etl/targets.yaml."""
import pytest

from build.tables.inputs import read_targets


def test_two_targets_naming_one_network_stop_the_build(corpus):
    corpus.add_target('  - name: Blue Value again\n    exchange_networks: ["45334-GAN001"]\n'
                      '    network_patterns: ["GA Blue Value HIX*"]\n')
    with pytest.raises(SystemExit, match=r"targets: the key \(network_id\) repeats"):
        corpus.build()


def test_a_blank_network_in_the_targets_file_stops_the_build(corpus):
    corpus.add_target('  - name: Blank\n    exchange_networks:\n      -\n'
                      '    network_patterns: ["GA Blue Value HIX*"]\n')
    with pytest.raises(SystemExit, match=r"targets: 1 rows with an empty key \(network_id\)"):
        corpus.build()


def test_a_network_georgia_doesnt_list_stops_the_build(corpus):
    corpus.add_target('  - name: Typo\n    exchange_networks: ["45334-GAN009"]\n'
                      '    network_patterns: ["GA Blue Value HIX*"]\n')
    with pytest.raises(SystemExit, match="45334-GAN009 isn't a network in Georgia's exchange files"):
        corpus.build()


def test_every_target_in_the_targets_file_names_networks_and_labels():
    """etl/targets.yaml itself: every target pairs exchange networks with labels."""
    for name, nets, patterns in read_targets():
        assert nets and patterns, name
