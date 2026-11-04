"""The build-only network_files table (build/inputs/network_files.py): which
parsed file prices each target network."""
import pytest


def test_labels_match_the_way_the_parse_probe_matches_them(corpus):
    # Trimmed and case-insensitive, with `*` the only wildcard: `?` is a literal.
    corpus.remove_rate(21057, "GA Blue Value HIX Individual Network")
    corpus.rate(21057, "  ga blue value hix individual network ")
    assert corpus.build() == {"networks": 2}
    with open(corpus.targets) as f:
        text = f.read()
    with open(corpus.targets, "w") as f:
        f.write(text.replace("Pathway PCP Copay Choice*", "Pathway?PCP Copay Choice*"))
    with pytest.raises(SystemExit, match="nothing prices 49046-GAN008"):
        corpus.build()


def test_a_file_the_index_doesnt_link_to_the_networks_plans_is_left_out(corpus):
    # GA_JBKEMED0001 carries Blue Value's label, but the index links it only to
    # 45334GA0010112, a plan Georgia's files no longer list.
    corpus.rate(31628, "GA Blue Value HIX Individual Network")
    corpus.link(31628, "45334GA0010112")
    assert corpus.build() == {"networks": 2}


def test_a_second_linked_file_with_the_networks_label_stops_the_build(corpus):
    corpus.rate(21000, "GA Blue Value HIX Individual Network")
    corpus.link(21000, "45334GA0020008")
    with pytest.raises(SystemExit, match=r"links files \[21000, 21057\] to plans on 45334-GAN001.*"
                                         r"one file prices 45334-GAN001"):
        corpus.build()


def test_a_parsed_file_the_index_stopped_linking_stops_the_build(corpus):
    corpus.unlink(21057)
    with pytest.raises(SystemExit, match=r"files \[21057\] carry a label matching .* but the index "
                                         r"doesn't link them to a plan on 45334-GAN001"):
        corpus.build()


def test_a_network_no_parsed_file_prices_stops_the_build(corpus):
    corpus.remove_rate(701, "Pathway PCP Copay Choice")
    with pytest.raises(SystemExit, match="nothing prices 49046-GAN008; run `make parse`"):
        corpus.build()
