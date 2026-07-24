"""Unit tests for /people search query helpers."""
import os

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017/test")
os.environ.setdefault("DB_NAME", "test")
os.environ.setdefault("JWT_SECRET", "test-secret")

from routers.people import _flex_id_regex, _merge_mongo_filters, _search_filter


def test_flex_id_regex_allows_space_dash_variants():
    assert _flex_id_regex("APL 204") == r"APL[\s\-]*204"
    assert _flex_id_regex("204") == "204"


def test_search_filter_includes_roster_fields():
    filt = _search_filter("cricket")
    fields = {next(iter(clause.keys())) for clause in filt["$or"] if "$expr" not in clause}
    assert "sport" in fields
    assert "centre" in fields
    assert "player_type" in fields
    assert "group" in fields
    assert "pws_class" in fields


def test_merge_mongo_filters_preserves_both_or_clauses():
    search = _search_filter("darshit")
    entity = {"$or": [{"entities": "ALPHA"}, {"kind": "player"}]}
    merged = _merge_mongo_filters({"kind": "player"}, search, entity)
    assert "$and" in merged
    assert len(merged["$and"]) == 3
    assert merged["$and"][0] == {"kind": "player"}
    assert "$or" in merged["$and"][1]
    assert "$or" in merged["$and"][2]
