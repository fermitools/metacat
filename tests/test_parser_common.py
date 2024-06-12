from env import env

from metacat.mql.meta_evaluator import MetaEvaluator
from metacat.mql.mql10 import _Parser, MQLQuery

# routine to make short tests: evaluate an expression against some metadata

def eval_expr(e, md):
    """ evaluate an expression for the given metadata ... """
    class mock_file:
        def __init__(self, md):
            self.md = md
        def metadata(self):
            return self.md
    f = mock_file(md)      # fake file with this metadata
    me = MetaEvaluator()   # metadata expression evaluator

    # we parse a whole "files where ..." expression, then pick out the where clause to evaluate

    pe = MQLQuery.parse(f"files where {e}")
    return me( f, pe.Tree.D['query'].Wheres )


# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
# testing == and != 

def test_exp_1():
    assert( eval_expr("c.n1 == 10", {"c.n1": 10, "c.n2": 20} ) )

def test_exp_2():
    assert( not eval_expr("c.n1 != 10", {"c.n1": 10, "c.n2": 20} ) )

def test_exp_3():
    assert( eval_expr("c.n2 == 20", {"c.n1": 10, "c.n2": 20} ) )

def test_exp_4():
    assert( not eval_expr("c.n2 != 20", {"c.n1": 10, "c.n2": 20} ) )

# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
# testing various "in" combinations

def test_exp_5():
    assert( eval_expr("c.n1 in (9,10,11)", {"c.n1": 10, "c.n2": 20} ) )

def test_exp_6():
    assert( eval_expr("c.n1 in 9:11", {"c.n1": 10, "c.n2": 20} ) )

def test_exp_5n():
    assert( not eval_expr("c.n1 not in (9,10,11)", {"c.n1": 10, "c.n2": 20} ) )

def test_exp_6n():
    assert( not eval_expr("c.n1 not in 9:11", {"c.n1": 10, "c.n2": 20} ) )

# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
# test 'present'

def test_exp_7():
    assert( eval_expr("c.n1 present", {"c.n1": 10, "c.n2": 20} ) )

def test_exp_8():
    assert( not eval_expr("c.n1 not present", {"c.n1": 10, "c.n2": 20} ) )

# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
# test constant in metadata-field

def test_exp_9():
    assert( eval_expr("10 in c.n1", {"c.n1": [8,9,10], "c.n2": 20} ) )

def test_exp_10():
    assert( not eval_expr("6 in c.n1", {"c.n1": [8,9,10], "c.n2": 20} ) )

def test_exp_10_1():
    assert( eval_expr("6 not in c.n1", {"c.n1": [8,9,10], "c.n2": 20} ) )

def test_exp_10_2():
    assert( not eval_expr("6 in c.n1", {"c.n1": [8,9,10], "c.n2": 20} ) )

# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
# test less/greater/equal

def test_exp_11():
    assert( eval_expr("c.n1 <= 10", {"c.n1": 10, "c.n2": 20} ) )

def test_exp_12():
    assert( eval_expr("c.n1 >= 10", {"c.n1": 10, "c.n2": 20} ) )

def test_exp_13():
    assert( eval_expr("c.n1 < 11", {"c.n1": 10, "c.n2": 20} ) )

def test_exp_14():
    assert( eval_expr("c.n1 > 9", {"c.n1": 10, "c.n2": 20} ) )

# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
# test regex match

def test_exp_15():
    assert( eval_expr("c.s1 ~ 'a.*b'", {"c.s1": "xaxyzby", "c.n2": 20} ) )

# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
# test and, or...

def test_exp_20():
    assert( eval_expr("(c.n1 < 11) and (c.n2 < 21)", {"c.n1": 10, "c.n2": 20} ) )

def test_exp_21():
    assert( eval_expr("(c.n1 < 11) or (c.n2 < 10)", {"c.n1": 10, "c.n2": 20} ) )

def test_exp_22():
    assert( eval_expr("(c.n1 < 9) or (c.n2 < 21)", {"c.n1": 10, "c.n2": 20} ) )

def test_exp_23():
    assert( eval_expr("(c.n1 in 8:11) or (c.n2 < 19)", {"c.n1": 10, "c.n2": 20} ) )

def test_exp_24():
    assert( eval_expr("(c.n1 in (8,9,10)) or (c.n2 < 19)", {"c.n1": 10, "c.n2": 20} ) )

# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
# bug cases from Steven Timm

def test_exp_30():
    assert( eval_expr(
        "core.runs[1]>=26785 and core.run_type=hd-protodune and core.data_tier=raw",
        {"core.runs": [1, 26785], "core.run_type": "hd-protodune", "core.data_tier": "raw"},
    ))

def test_exp_31():
    assert( eval_expr(
        "core.runs[1] in (26785,26789,26790,26791,26792,26793,26794) and core.run_type=hd-protodune and core.data_tier=raw",
        {"core.runs": [1, 26785], "core.run_type": "hd-protodune", "core.data_tier": "raw"},
    ))

def test_exp_32():
    assert( eval_expr(
        "core.runs[1] in 26785:26794  and core.run_type=hd-protodune and core.data_tier=raw",
        {"core.runs": [1, 26785], "core.run_type": "hd-protodune", "core.data_tier": "raw"},
    ))

