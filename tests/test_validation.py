"""
  Unit tests for metacat.util.validation.validate_metadata. These do not need a
  running server.
"""
import pytest

from metacat.util.validation import validate_metadata

ARRAY_TYPES = ["int[]", "float[]", "text[]", "boolean[]"]


@pytest.mark.parametrize("typ", ARRAY_TYPES)
@pytest.mark.parametrize("value", [5, 1.5, True, None, {"a": 1}])
def test_array_type_reports_a_scalar_instead_of_raising(typ, value):
    errors = validate_metadata({"p": {"type": typ}}, False, {"p": value})
    assert len(errors) == 1
    assert errors[0][0] == "p"


@pytest.mark.parametrize(
    "typ,value",
    [
        ("int[]", [1, 2]),
        ("float[]", [1.5, 2.5]),
        ("text[]", ["a", "b"]),
        ("boolean[]", [True, False]),
    ],
)
def test_array_type_accepts_a_matching_list(typ, value):
    assert validate_metadata({"p": {"type": typ}}, False, {"p": value}) == []


@pytest.mark.parametrize(
    "typ,value",
    [
        ("int[]", [1, "x"]),
        ("float[]", [1.5, "x"]),
        ("text[]", ["a", 1]),
        ("boolean[]", [True, 1.5]),
    ],
)
def test_array_type_rejects_a_bad_member(typ, value):
    errors = validate_metadata({"p": {"type": typ}}, False, {"p": value})
    assert len(errors) == 1
    assert errors[0][0] == "p"
