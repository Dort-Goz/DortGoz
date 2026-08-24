from __future__ import annotations

import json

from dortgoz.services import category_rules as cr


def _rule(cat="silahli_olay", crit="Karede silah görünüyorsa bu sınıf önceliklidir.",
          approved=True, evidence=("a", "b")):
    return cr.CategoryRule(category=cat, criterion=crit,
                           approved=approved, evidence=list(evidence))


def test_unapproved_rules_never_reach_the_prompt():
    assert cr.prompt_block([_rule(approved=False)]) == ""


def test_thin_evidence_never_reaches_the_prompt():
    assert cr.prompt_block([_rule(evidence=("a",))]) == ""


def test_approved_rule_is_rendered_for_its_category():
    block = cr.prompt_block([_rule()])

    assert "silahli_olay" in block
    assert "silah görünüyorsa" in block
    assert "olay olup olmadığını" in block


def test_empty_ruleset_adds_nothing():
    assert cr.prompt_block([]) == ""


def test_merge_keeps_approved_rules_and_skips_duplicates():
    existing = [_rule(approved=True)]
    proposed = [_rule(approved=False), _rule(cat="kavga", crit="Karşılıklı vuruş.",
                                             approved=False)]

    merged = cr.merge(existing, proposed)

    assert len(merged) == 2
    assert merged[0].approved is True
    assert merged[1].category == "kavga"


def test_round_trip(tmp_path):
    cr.save(tmp_path, [_rule()])

    loaded = cr.load(tmp_path)

    assert loaded[0].category == "silahli_olay"
    assert loaded[0].approved is True
    assert cr.load(tmp_path / "yok") == []


def test_corrections_reads_only_mismatched_confirmations(tmp_path):
    ledger = tmp_path / "d.jsonl"
    ledger.write_text("\n".join(json.dumps(r) for r in [
        {"key": "k1", "verdict": "anomali", "model_category": "kavga",
         "operator_category": "silahli_olay", "decision_id": "abc123456"},
        {"key": "k2", "verdict": "anomali", "model_category": "kavga",
         "operator_category": "kavga", "decision_id": "d2"},
        {"key": "k3", "verdict": "sorun_degil", "model_category": "kavga",
         "operator_category": "", "decision_id": "d3"},
    ]), encoding="utf-8")

    out = cr.corrections(ledger)

    assert len(out) == 1
    assert out[0]["model"] == "kavga"
    assert out[0]["operator"] == "silahli_olay"


def test_revised_correction_supersedes_the_earlier_line(tmp_path):
    ledger = tmp_path / "d.jsonl"
    ledger.write_text("\n".join(json.dumps(r) for r in [
        {"key": "k1", "verdict": "anomali", "model_category": "kavga",
         "operator_category": "silahli_olay", "decision_id": "d1"},
        {"key": "k1", "verdict": "anomali", "model_category": "kavga",
         "operator_category": "kavga", "decision_id": "d2"},
    ]), encoding="utf-8")

    assert cr.corrections(ledger) == []


def test_seed_rules_need_no_evidence_but_induced_ones_do():
    seed = cr.CategoryRule(category="yangin", criterion="Alev veya duman.",
                           approved=True, source="seed")
    induced = cr.CategoryRule(category="yangin", criterion="X.",
                              approved=True, source="induced", evidence=["a"])

    assert seed.supported is True
    assert induced.supported is False


def test_induced_rule_wins_over_seed_for_the_same_category():
    seed = cr.CategoryRule(category="silahli_olay", criterion="TABAN",
                           approved=True, source="seed")
    induced = cr.CategoryRule(category="silahli_olay", criterion="CIKARILMIS",
                              approved=True, source="induced",
                              evidence=["a", "b"])

    block = cr.prompt_block([seed, induced])

    assert "CIKARILMIS" in block
    assert "TABAN" not in block
    assert block.count("`silahli_olay`") == 1


def test_each_category_appears_at_most_once():
    rules = [
        cr.CategoryRule(category="kavga", criterion="A", approved=True,
                        source="seed"),
        cr.CategoryRule(category="kavga", criterion="B", approved=True,
                        source="seed"),
    ]

    assert cr.prompt_block(rules).count("`kavga`") == 1


def test_seed_file_covers_every_confirmable_category(tmp_path):
    from pathlib import Path

    from dortgoz.services.triage import CATEGORIES

    seeds = cr.seed_rules(Path(__file__).resolve().parents[2] / "defaults")

    assert {r.category for r in seeds} == set(CATEGORIES)
    assert all(r.source == "seed" for r in seeds)
