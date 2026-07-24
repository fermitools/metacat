from metacat.util.generators import limited, strided, skipped


def test_limited_list_does_not_double_yield():
    assert list(limited([1, 2, 3, 4, 5], 3)) == [1, 2, 3]
    assert list(limited((10, 20, 30), 2)) == [10, 20]


def test_limited_none_returns_each_once():
    assert list(limited([1, 2, 3], None)) == [1, 2, 3]


def test_limited_generator_unchanged():
    assert list(limited((x for x in [1, 2, 3, 4, 5]), 3)) == [1, 2, 3]
    assert list(limited((x for x in [1, 2, 3]), None)) == [1, 2, 3]


def test_strided_none_yields_all():
    assert list(strided([0, 1, 2, 3], None)) == [0, 1, 2, 3]


def test_strided_step_unchanged():
    assert list(strided([0, 1, 2, 3, 4, 5], 2)) == [0, 2, 4]


def test_skipped_list_does_not_double_yield():
    assert list(skipped([1, 2, 3, 4, 5], 2)) == [3, 4, 5]


def test_skipped_none_yields_all():
    assert list(skipped([1, 2, 3], None)) == [1, 2, 3]
