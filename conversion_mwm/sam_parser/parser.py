
""" The dimensions parser module """

import threading

#import samutil.parser
from pyparsing import *

from parse_tree import *

#from dimension_query.exc import DimParserError, ForbiddenQuery
class DimParserError(Exception):
     pass
class ForbiddenQuery(Exception):
     pass

import string

_special_operators = { 
    "defname: <definition name>" : "Include the existing definition with this name",
    "isparentof: ( <dimensions> )" : "Returns files that are the immediate parent of any file matching the given sub-query",
    "ischildof: ( <dimensions> )" : "Returns files that are the immediate child of any file matching the given sub-query",
    "isancestorof: ( <dimensions> )" : "Returns files that are an ancestor of any file matching the given sub-query",
    "isdescendantof: ( <dimensions> )" : "Returns files that are an descendant of any file matching the given sub-query",
    "with availability <value>[,<value>[,...]]" : "Alters the availability constraints applied to the query",
    "with limit n" : "Limit the number of results to n",
    "with offset n" : "Skip the first n results",
    "with stride n" : "Return every n'th result",
}

def get_special_operators():
    """ Return descriptions of the special operators for help text """
    return _special_operators

def toInt(instr, loc, tokens):
    return int(tokens[0])

def optionalParens(parserexp):
    """ Optionally allow parentheses around an expression """
    return Suppress('(') + parserexp - Suppress(')') | parserexp

in_ = CaselessKeyword('in')
not_ = CaselessKeyword('not')
and_ = CaselessKeyword('and')
or_ = CaselessKeyword('or')
minus = CaselessKeyword('minus')
intersect = CaselessKeyword('intersect')
union = CaselessKeyword('union')
set_op = minus | intersect | union
like = CaselessKeyword('like')
defname = CaselessKeyword('defname')
isparentof = CaselessKeyword('isparentof')
isancestorof = CaselessKeyword('isancestorof')
ischildof = CaselessKeyword('ischildof')
isdescendantof = CaselessKeyword('isdescendantof')
availability = CaselessKeyword('availability')
with_ = CaselessKeyword('with')
limit = CaselessKeyword('limit')
offset = CaselessKeyword('offset')
stride = CaselessKeyword('stride')

listop = (in_ | Combine( not_ + in_, adjacent=False )).setName("list operator")
equalop = Literal('=') | '!='
singleop = (Literal('<=') | '>=' | '<' | '>' | Combine( Optional(not_) + like, adjacent=False )).setName("operator")

name = Word( alphas,alphanums+"_.-+").setParseAction(downcaseTokens).setName("name") + ~FollowedBy(':').setName(':')

# some of the complication here is that we want to parse number or date ranges, but allow unquoted
# strings containing a dash. So even though numbers and dates also match the unquotedvalue parser, we need
# to keep them separate
unquotedvalue = Word( alphanums+"_.%?-+" ).setName("unquoted value")
number = (Combine(Optional('-') + Word(nums) + Optional(Literal('.') + Word(nums)))).setName("number")
quotedvalue = quotedString.setParseAction(removeQuotes).setName("quoted value")
date = Regex( r'[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9][0-9]-[a-zA-Z]{3}-(?:[0-9]{4}|[0-9]{2})' ).setName("date")

singlevalue = (quotedvalue | unquotedvalue).setName("single value")
valuerange = ( (date |number | quotedvalue) + Suppress('-') + ( date | number | quotedvalue) ).setParseAction(RangeNode.fromTokens).setName("value range")
valuelist = Group(singlevalue + OneOrMore( Suppress(',') + singlevalue)).setName("value list")

# List operators must be followed by a list, so use group to make a single value into a list of tokens
# The - operator means throw exception and stop parsing if no match following. In this case we reject range and list operators
# that can't possibly follow a single value
# equal operators can be followed either by a list, range, or single item.
argument = (listop + optionalParens( valuelist | valuerange | Group(singlevalue) ) ) | singleop + optionalParens(singlevalue) - ~(Literal('-') | Literal(',')).setName('- or ,') | Optional(equalop) + optionalParens( valuelist | valuerange | singlevalue)

paren_clause = Forward()
dimension_rule = (name + argument).setParseAction(DimNode.fromTokens).setName("dimension")
definition_rule = (Suppress(defname + ':') - singlevalue).setParseAction(DefinitionNode.fromTokens).setName("definition")
isrelative_rule = ( (isparentof | ischildof | isancestorof | isdescendantof) + Suppress(':') - paren_clause ).setParseAction(IsRelativeOfNode.fromTokens).setName("isrelative")
availability_rule = (Suppress(availability + ':') - optionalParens(delimitedList(singlevalue))).setParseAction(AvailabilityNode.fromTokens).setName("availability")

constraint = (dimension_rule | definition_rule | isrelative_rule  ).setName("constraint")

expression, not_clause = Forward(), Forward()
paren_clause << (Suppress('(') - expression - Suppress(')')).setName("parenthesized clause")
base_term = (paren_clause | constraint)
not_clause << ( (not_.suppress() - not_clause).setParseAction(NotNode.fromTokens).setName("not expression") | base_term )

# availability only makes sense combined with and
and_term = optionalParens(availability_rule) | not_clause
and_clause = ((and_term + OneOrMore(and_.suppress() - and_term)).setParseAction(AndNode.fromTokens) | not_clause)
or_clause = ((and_clause + OneOrMore(or_.suppress() - and_clause)).setParseAction(OrNode.fromTokens) | and_clause)
# minus should be left associative, but this creates a flattened list
set_clause = ((or_clause + OneOrMore(set_op - or_clause)).setParseAction(SetNode.fromTokens) | or_clause)

def handleList(i, l, t):
    return [list(t[0])]

with_rule = OneOrMore( limit - Word(nums).setParseAction(toInt) | offset - Word(nums).setParseAction(toInt) | stride - Word(nums).setParseAction(toInt) | availability - Group(delimitedList(singlevalue)).setParseAction(handleList) | (NotAny(with_) + name - singlevalue) ) # last term is a generic that can be anything but "with"
expression << (( set_clause + OneOrMore(with_.suppress() - with_rule)).setParseAction(WithNode.fromTokens) | set_clause)

parser = expression - StringEnd()

# flatten out strings of And, Or operations
# This is needed because very long combinations can blow up
# the stack if we try to traverse the tree recursively
class FlattenTree(ParseTreeTransformer):

    def __init__(self):
        # keep track if we've found an availability node
        self.found_availability = []

    def _flatten_op(self, node):
        newnodes = []
        for n in node.nodes:
            n = self.visit(n)
            if n is not None:
                if type(n) == type(node):
                    # same operation type, so hoist to this level
                    # and drop the child node
                    newnodes.extend(n.nodes)
                else:
                    newnodes.append(n)
        node.nodes = newnodes
        return node

    def visit_AndNode(self, node):
        node = self._flatten_op(node)
        if self.found_availability:
            # rewrite Availability into WithNode
            availability = []
            oldnodes = node.nodes
            node.nodes = []
            for n in oldnodes:
                if isinstance(n, AvailabilityNode):
                    availability.extend(f for f in n.flags if f not in availability)
                    self.found_availability.pop()
                else:
                    node.nodes.append(n)
            if availability:
                # if AndNode is reduced to One element, remove it
                if len(node.nodes) == 1:
                    node = node.nodes[0]
                node = self.visit(WithNode(node, {'availability': list(availability)}))
        return node

    def visit_OrNode(self, node):
        return self._flatten_op(node)

    def visit_SetNode(self, node):
        # minus needs special handling
        # can flatten the left only for minus
        # intersect can be completely flattened
        newnodes = []
        for i, n in enumerate(node.nodes):
            n = self.visit(n)
            if (node.op != 'minus' or i==0) and type(n) == SetNode and n.op == node.op:
                newnodes.extend(n.nodes)
            else:
                newnodes.append(n)
        node.nodes = newnodes
        return node
            

    def visit_AvailabilityNode(self, node):
        # just flag we've found one, so it can be rewritten
        self.found_availability.append(True)
        return node

    def visit_WithNode(self, node):
        node = self.generic_visit(node)
        # If the child is a with node only handling availability can combine them
        if isinstance(node.node, WithNode):
            if node.node.params.keys() == set(['availability']):
                avail = node.params.setdefault('availability', set())
                avail.update(node.node.params['availability'])
                node.node = node.node.node

        return node

class ValidateTree(ParseTreeVisitor):
    """ Check for certain error conditions """

    def __init__(self):
        self.flags = set()

    def visit_IsRelativeOfNode(self, node):
        # check if isancestorof or isdescendant of appear more than once or in conjunction with isparentof/ischildof
        if node.relation in ('isancestorof', 'isdescendantof'):
            if 'recursive_lineage' in self.flags:
                raise ForbiddenQuery('Query forbidden - more than one ancestor or descendant operator in a single query')
            self.flags.add('recursive_lineage')
        else:
            self.flags.add('single_lineage')

        if {'recursive_lineage','single_lineage'}.issubset(self.flags):
            raise ForbiddenQuery('Query forbidden - mixing ancestor or descendant with child or parent is not allowed')

        return self.generic_visit(node)


# The parser isn't really threadsafe (primarily because of memoization),
# so serialize access
def parse_string(s):
    if not s:
        raise DimParserError("Dimensions string is empty")
    try:
        s = s.encode('ascii').decode('UTF-8')
    except UnicodeEncodeError:
        raise DimParserError("Dimensions contain invalid (non-ascii) characters")
    #with samutil.parser.lock:
    if True:
        import sys
        # complicated expressions can exceed the standard recursion limit
        recursionlimit = sys.getrecursionlimit()
        sys.setrecursionlimit(10000)
        try:
            try:
                r = parser.parseString(s, True)[0]
            except ParseBaseException as ex:
                msg = "Parse error at line %d, column %d: %s\n" % (ex.lineno, ex.column, ex.msg)
                msg += ex.line
                if msg[-1] != '\n': msg+='\n'
                msg += " "*(ex.column-1) + "^"
                raise DimParserError(msg)
            except RuntimeError as ex:
                raise DimParserError("Unable to parse dimensions: %s" % ex)
        finally:
            # The cache can grow without limit, so make sure it is reset
            parser.resetCache()
            sys.setrecursionlimit(recursionlimit)
    r = FlattenTree().visit(r)
    ValidateTree().visit(r)
    return r

__all__ = ['parse_string']

if __name__ == '__main__':
    import sys
    r = parse_string(' '.join(sys.argv[1:]))
    print(r)
    print()
    print(render_dimensions_tree(r))
