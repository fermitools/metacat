# The nodes for the dimension parse tree

import re
from pyparsing import ParseResults

#from dimension_query.exc import DimParseTreeError

# use instead for standlone testing
class DimParseTreeError:
    pass

def meta_render_dimensions_tree(tree):
    """ Pretty print the dimensions tree """
    lines = []
    line = []
    linelen = 0
    for token in tree.meta_render():
        if not line:
            line.append(token)
            linelen+=len(token)
        elif linelen + len(token) > 150:
            lines.append(''.join(line))
            line = [ token ]
            linelen = len(token)
        else:
            # add spaces appropriately
            # there should be no space between ( and the following char
            # and no space between a char and a following )
            # plus if a token starts with space or the previous token ended with it we don't want to add any more
            lasttoken = line[-1]
            lastchar = lasttoken[-1]
            if not (token == ',' or token == ')' or lasttoken == '(' or token.startswith(' ') or lastchar==' ' ):
                # prepend a space
                line.append(' ')
                linelen+=1
            line.append(token)
            linelen+=len(token)
    if line:
        lines.append(''.join(line))
    return '\n'.join(lines)


def render_dimensions_tree(tree):
    """ Pretty print the dimensions tree """
    lines = []
    line = []
    linelen = 0
    for token in tree.render():
        if not line:
            line.append(token)
            linelen+=len(token)
        elif linelen + len(token) > 150:
            lines.append(''.join(line))
            line = [ token ]
            linelen = len(token)
        else:
            # add spaces appropriately
            # there should be no space between ( and the following char
            # and no space between a char and a following )
            # plus if a token starts with space or the previous token ended with it we don't want to add any more
            lasttoken = line[-1]
            lastchar = lasttoken[-1]
            if not (token == ',' or token == ')' or lasttoken == '(' or token.startswith(' ') or lastchar==' ' ):
                # prepend a space
                line.append(' ')
                linelen+=1
            line.append(token)
            linelen+=len(token)
    if line:
        lines.append(''.join(line))
    return '\n'.join(lines)

class NodeBase(object):
    precedence = None
    nodes = [] # No children by default
    def __str__(self):
        return formatTree(self)
    def meta_render(self):
        raise NotImplementedError('%s.render' % self.__class__.__name__)
    def render(self):
        raise NotImplementedError('%s.render' % self.__class__.__name__)
    def __eq__(self, other):
        return NotImplemented
    def __hash__(self):
        return hash(tuple(self.nodes))
    def __ne__(self, other):
        return not (self == other)
    def get_name(self):
        return self.__class__.__name__

class NegatableNode(NodeBase):
    """ Class for nodes which behave differently when negated """
    negated = False
    def __hash__(self):
        return hash((self.nodes, self.negated))

class ListNodeBase(NodeBase):
    @classmethod
    def fromTokens(cls, instr, loc, tokens):
        return cls(*tokens)
    def __init__(self, *nodes):
        self.nodes = list(nodes)

    def __str__(self):
        return formatTree(self)
    def __repr__(self):
        return '%s(%s)' % (self.__class__.__name__, self.nodes)
    def __eq__(self, other):
        if self.__class__ != other.__class__: return NotImplemented
        return self.nodes == other.nodes
    def __hash__(self):
        return hash(tuple(self.nodes))

# set ops have left associativity; everything else is right
_associativity = { 4: -1 }

def _meta_infix_render(op, nodes, precedence):
    # render infix operator output, taking into account precedence and associativity
    tokens = []
    if op == 'minus':
        op = '-'   # mwm -- XXX not exactly right here...
    for i, n in enumerate(nodes):
        if i > 0:
            yield op
        r = n.meta_render()
        associativity = _associativity.get(precedence,0)
        addparens = _determine_needs_parens(i, n, precedence, associativity)
        if addparens:
            yield '('
        for t in r: yield t
        if addparens:
            yield ')'

def _infix_render(op, nodes, precedence):
    # render infix operator output, taking into account precedence and associativity
    tokens = []
    for i, n in enumerate(nodes):
        if i > 0:
            yield op
        r = n.render()
        associativity = _associativity.get(precedence,0)
        addparens = _determine_needs_parens(i, n, precedence, associativity)
        if addparens:
            yield '('
        for t in r: yield t
        if addparens:
            yield ')'

def _determine_needs_parens(i, n, precedence, associativity):
    t1 = None
    if n.precedence is None:
        t1 = False
    else:
        t1 = (precedence < n.precedence)

    t2 = (
        precedence == n.precedence and 
        (
            associativity < 0 and i > 0 or 
            associativity > 0 and i == 0
        )    
    )

    addparens = t1 or t2
    return addparens

class UnaryNode(NodeBase):
    """ Base class for nodes with a single child """
    @property
    def nodes(self):
        return [self.node]
    @nodes.setter
    def nodes(self, newnodes):
        self.node = newnodes[0]

class BinaryOperatorNode(ListNodeBase):

    def meta_render(self):
        return _meta_infix_render(self.op, self.nodes, self.precedence)
    def render(self):
        return _infix_render(self.op, self.nodes, self.precedence)
    def __eq__(self, other):
        rval=  ListNodeBase.__eq__(self, other)
        return rval if rval is NotImplemented else rval and self.op == other.op
    def __hash__(self):
        return hash( (self.op, tuple(self.nodes)) )

class AndNode(BinaryOperatorNode):
    op = 'and'
    precedence = 2

class OrNode(BinaryOperatorNode):
    op = 'or'
    precedence = 3

class SetNode(BinaryOperatorNode, NegatableNode):
    precedence = 4
    @classmethod
    def fromTokens(cls, instr, loc, tokens):
        # build up left associative tree
        tokens = list(reversed(tokens))
        left, op, right = tokens.pop(), tokens.pop(), tokens.pop()
        node = cls(op, left, right)
        while tokens:
            op, right = tokens.pop(), tokens.pop()
            if op != node.op:
                node = cls(op, node, right)
            else:
                node.nodes.append(right)
        return node

    def __init__(self, op, *nodes):
        BinaryOperatorNode.__init__(self, *nodes)
        self.op = op

    def __repr__(self):
        return '%s%s(%s %s)' % (("Not" if self.negated else ""), self.__class__.__name__, self.op, self.nodes)
    def __eq__(self, other):
        rval = BinaryOperatorNode.__eq__(self, other)
        return rval if rval is NotImplemented else rval and self.negated == other.negated
    def __hash__(self):
        return hash( (BinaryOperatorNode.__hash__(self), self.negated) )
    def meta_render(self):
        if self.negated:
            yield 'not'
            yield '('
        if self.op in ('union', 'intersect'):
           if (self.op == 'intersect'):
               m_op = 'join'
           else: 
               m_op = self.op
           yield m_op
           yield '('
           for n in self.nodes:
               for t in n.meta_render():
                   yield t
           yield ')'
        else:
            for t in BinaryOperatorNode.meta_render(self): yield t
        if self.negated:
            yield ')'
    def render(self):
        if self.negated:
            yield 'not'
            yield '('
        for t in BinaryOperatorNode.render(self): yield t
        if self.negated:
            yield ')'

class WithNode(UnaryNode):
    precedence = 5
    @classmethod
    def fromTokens(cls, instr, loc, tokens):
        # split the tokens into key:value pairs
        # combine lists together
        params = {}
        for k,v in zip(*[iter(tokens[1:])]*2):
            k = k.lower()
            if isinstance(v, (list, tuple, set)):
                params.setdefault(k, set()).update(v)
            else:
                params[k] = v
        return cls(tokens[0], params)
    def __init__(self, node, params):
        self.node = node
        self.params = params
        for param, val in self.params.items():
            if isinstance(val, (list, tuple)):
                self.params[param] = set(val)
    def __repr__(self):
        return '%s(%s, %s)' % (self.__class__.__name__, self.node, self.params)
    def __eq__(self, other):
        if self.__class__ != other.__class__: return NotImplemented
        return self.node == other.node and self.params == other.params
    def __hash__(self):
        return hash( (self.node, tuple(self.params)) )
    def format_params(self):
        # format the params for display
        r = []
        for k in sorted(self.params):
            v = self.params[k]
            if isinstance(v, (set, list)):
                v = '{%s}' % ( ', '.join(str(i) for i in v ) )
            else:
                v = str(v)
            r.append('%s=%s' % (k, v))
        return ', '.join(r)
    def meta_render(self):
        if self.node.precedence is not None and self.node.precedence >= self.precedence:
            yield '('
        for t in self.node.meta_render(): yield t
        if self.node.precedence is not None and self.node.precedence >= self.precedence:
            yield ')'
        # yield 'with'
        for k in sorted(self.params):
            v = self.params[k]
            if v is None: continue
            if isinstance(v, set):
                if not v: continue
                v = ','.join(_quote_value(str(e)) for e in v)
            else: v = _quote_value(str(v))
            yield '%s %s' % (k, v)
    def render(self):
        if self.node.precedence is not None and self.node.precedence >= self.precedence:
            yield '('
        for t in self.node.render(): yield t
        if self.node.precedence is not None and self.node.precedence >= self.precedence:
            yield ')'
        yield 'with'
        for k in sorted(self.params):
            v = self.params[k]
            if v is None: continue
            if isinstance(v, set):
                if not v: continue
                v = ','.join(_quote_value(str(e)) for e in v)
            else: v = _quote_value(str(v))
            yield '%s %s' % (k, v)

class AvailabilityNode(NodeBase):
    @classmethod
    def fromTokens(cls, instr, loc, tokens):
        return cls(*tokens)
    def __init__(self, *flags):
        self.flags = list(flags)
    def __str__(self):
        return 'Availability(%s)' % ', '.join(self.flags)
    def __eq__(self, other):
        if self.__class__ != other.__class__: return NotImplemented
        return self.flags == other.flags
    def __hash__(self):
        return hash(tuple(self.flags))
    def meta_render(self):
        yield 'availability:'
        yield ','.join(_quote_value(v) for v in self.flags)
    def render(self):
        yield 'availability:'
        yield ','.join(_quote_value(v) for v in self.flags)

class NotNode(UnaryNode):
    precedence = 1
    @classmethod
    def fromTokens(cls, instr, loc, tokens):
        return cls(tokens[0])

    def __init__(self, node):
        self.node = node
    def __repr__(self):
        return '%s(%s)' % (self.__class__.__name__, self.node)
    def __eq__(self, other):
        if self.__class__ != other.__class__:
            # if the other node has a negated attribute, invert our child
            # and compare them
            if isinstance(self.node, NegatableNode):
                from copy import copy
                thisnode = copy(self.node)
                thisnode.negated = (not thisnode.negated)
                return thisnode == other
            else:
                # else mismatched types
                return NotImplemented
        return self.node == other.node
    def __hash__(self):
        if isinstance(self.node, NegatableNode):
            from copy import copy
            thisnode = copy(self.node)
            thisnode.negated = (not thisnode.negated)
            return hash(thisnode)
        else:
            return hash( ('not', self.node) )
    def meta_render(self):
        r = list(self.node.meta_render())
        if self.node.precedence is None:
            addparen = False
        else:
            addparen = self.precedence < self.node.precedence
        yield 'not'
        if addparen:
            yield '('
        for t in r: yield t
        if addparen:
            yield ')'

    def render(self):
        r = list(self.node.render())
        if self.node.precedence is None:
            addparen = False
        else:
            addparen = self.precedence < self.node.precedence
        yield 'not'
        if addparen:
            yield '('
        for t in r: yield t
        if addparen:
            yield ')'

_quotechars = re.compile('[^0-9a-zA-Z_.+%?]')
def _quote_value(val):
    """ Determine if a value should be quoted
    (This function quotes more than is really necessary """
    quote = None
    if "'" in val:
        quote='"'
    elif (val == '' or '"' in val or ' ' in val or
      _quotechars.search(val)):
        quote = "'"

    if quote:
        return "%s%s%s" % (quote, val, quote)
    else:
        return val

class DimNode(NegatableNode):

    @classmethod
    def fromTokens(cls, instr, loc, tokens):
        if len(tokens) == 2:
            dim, value = tokens
            op = '='
        else:
            dim, op, value = tokens
        if isinstance(value, ParseResults):
            value = value.asList()
        return cls(dim, op, value)
    def __init__(self, dim, op, value):
        self.dim, self.op, self.value = dim, op.replace(' ',''), value

        # if an alias, update with the real name
        #from ..dimensions import get_dimension_name
        #self.dim = get_dimension_name(self.dim)

    # special constructor for a negated dim
    @classmethod
    def Negated(cls, dim, op, value):
        self = cls(dim, op, value)
        self.negated = True
        return self
    def __str__(self):
        if isinstance(self.value, list):
            val = '( ' + ', '.join(self.value) + ' )'
        else:
            val = self.value
        s = "%sDimension(%s %s %s)" % (("Not" if self.negated else ""),self.dim, self.op, val)
        return s

    def __eq__(self, other):
        if self.__class__ != other.__class__: return NotImplemented
        return (self.dim == other.dim) and (self.op == other.op) and (self.value == other.value) and (self.negated == other.negated)
    def __hash__(self):
        if isinstance(self.value, list): v = tuple(self.value)
        else: v = self.value
        return hash((self.dim, self.op, v, self.negated))

    def meta_trans(self, name):
        name = re.sub( '^(data_stream|run_number|run_type|data_tier|end_time|event_count|file_content_status|file_format|file_partition|file_type|first_event_number|last_event_number|process_id|retired_date|runs|scope|start_time)$', 'core.\\1', name)
        name = re.sub( '^(appl_name|application)$', 'application.name', name)
        name = re.sub( '^(family|version)$', 'core.application.\\1', name)

        # map file_name, file_size 
        name = re.sub( '^file_(name|size)$', '\\1', name)
        name = re.sub( '^(create|update)_date$', '\\1d_timestamp', name)
        name = re.sub( '^project_name$', 'project.name', name)
        name = re.sub( '^consumer$', 'project.worker', name)
        name = re.sub( '^full_path$', 'rse.path', name)
        return '[' + name  + ']'

    def meta_render(self):
        if self.negated:
            yield 'not'
        if isinstance(self.value, list):
            def _gen():
                i = iter(self.value)
                yield '(' # mwm -- added parens
                yield _quote_value(next(i))
                for v in i:
                    yield ','
                    yield _quote_value(v)
                yield ')'# mwm -- added parens
            val = _gen()
        elif isinstance(self.value, NodeBase):
            val = self.value.render()
        else:
            val = [_quote_value(self.value)]

        if self.op == '=':
            yield self.meta_trans(self.dim)
            yield '='  # mwm -- kluge to get equals back
            for v in val: yield v
        else:
            if self.op.startswith('not'):
                op = 'not %s' % self.op[3:].strip()
            else:
                op = self.op
            yield self.meta_trans(self.dim)
            yield op
            for v in val: yield v

    def render(self):
        if self.negated:
            yield 'not'
        if isinstance(self.value, list):
            def _gen():
                i = iter(self.value)
                yield _quote_value(next(i))
                for v in i:
                    yield ','
                    yield _quote_value(v)
            val = _gen()
        elif isinstance(self.value, NodeBase):
            val = self.value.render()
        else:
            val = [_quote_value(self.value)]

        if self.op == '=':
            yield self.dim
            for v in val: yield v
        else:
            if self.op.startswith('not'):
                op = 'not %s' % self.op[3:].strip()
            else:
                op = self.op
            yield self.dim
            yield op
            for v in val: yield v

class RangeNode(NodeBase):
    @classmethod
    def fromTokens(cls, instr, loc, tokens):
        return cls(*tokens)
    def __init__(self, first, last):
        self.first, self.last = first, last
    def __str__(self):
        return "Range(%s to %s)" % (self.first, self.last)
    def __eq__(self, other):
        if self.__class__ != other.__class__: return NotImplemented
        return self.first == other.first and self.last == other.last
    def __hash__(self):
        return hash((self.first, self.last))
    def render(self):
        yield '%s-%s' % (_quote_value(self.first), _quote_value(self.last))
    def meta_render(self):
        yield '%s-%s' % (_quote_value(self.first), _quote_value(self.last))

class DefinitionNode(NodeBase):
    @classmethod
    def fromTokens(cls, instr, loc, tokens):
        return cls(tokens[0])
    def __init__(self, defname):
        self.defname = defname
    def __str__(self):
        return "Defname(%s)" % self.defname
    def __eq__(self, other):
        if self.__class__ != other.__class__: return NotImplemented
        return self.defname == other.defname
    def __hash__(self):
        return hash(self.defname)
    def meta_render(self):
        yield 'defname:'
        yield self.defname
    def render(self):
        yield 'defname:'
        yield self.defname

class IsRelativeOfNode(NegatableNode):
    precedence = 0 # always binds more tightly than its children
    @classmethod
    def fromTokens(cls, instr, loc, tokens):
        return cls(*tokens)
    def __init__(self,relation, subtree):
        self.relation = relation
        self.subtree = subtree
    @property
    def nodes(self):
        return [self.subtree]
    @nodes.setter
    def nodes(self, newnodes):
        self.subtree = newnodes[0]
    def __repr__(self):
        return "%s(%s, %s)" % (self.get_name(),self.relation, self.subtree)
    def get_name(self):
        return (("Not" if self.negated else "") + self.__class__.__name__)
    def __eq__(self, other):
        if self.__class__ != other.__class__: return NotImplemented
        return self.relation == other.relation and self.subtree == other.subtree and self.negated == other.negated
    def __hash__(self):
        return hash( (self.relation, self.subtree, self.negated) )
    def meta_render(self):
        if self.negated: yield "not"
        yield {'ischildof': 'children', 'isparentof': 'parents'}[self.relation]
        yield "(" 
        for t in self.subtree.meta_render():
            yield t
        yield " )"
    def render(self):
        if self.negated: yield "not"
        yield "%s:( " % self.relation
        for t in self.subtree.render():
            yield t
        yield " )"

class ParseTreeVisitor(object):

    """ Visitor base class for the AST """
    def visit(self, node):
        try:
            meth = self.__cache.get( node.__class__ )
        except AttributeError:
            meth = None
            self.__cache={}
        if not meth:
            for cls in node.__class__.mro():
                meth_name = 'visit_' + cls.__name__
                meth = getattr(self, meth_name, None)
                if meth: break
            if not meth:
                meth = self.generic_visit
            self.__cache[ node.__class__ ] = meth
        return meth(node)

    def generic_visit(self, node):
        for c in node.nodes:
            self.visit(c)

class ParseTreeTransformer(ParseTreeVisitor):
    """ Transform the syntax tree. In this case, each visitor must return the new value
    of the node, or None to delete it """
    def __init__(self):
        # subclasses should set the modified flag if they change anything
        self.modified = False

    def generic_visit(self, node):
        newnodes = [ self.visit(c) for c in node.nodes ]
        node.nodes = [ c for c in newnodes if c is not None ]
        return node

def _indenter(func):
    def wrapper(self, node):
        self.level+=1
        try:
            result = func(self, node)
            if self.level > 1:
                result = [ '  ' + r for r in result ]
        finally:
            self.level -= 1
        return result
    return wrapper

class TreeFormatter(ParseTreeVisitor):
    """ Format the parse tree, indenting the nodes appropriately """
    
    def __init__(self):
        self.level = 0

    @_indenter
    def generic_visit(self, node):
        return str(node).split('\n')

    @_indenter
    def visit_SetNode(self, node):
        result = [ "%s(%s)" % (node.get_name(), node.op) ]
        for n in node.nodes:
            result.extend(self.visit(n))
        return result

    @_indenter
    def visit_ListNodeBase(self, node):
        result = [node.get_name()]
        for n in node.nodes:
            result.extend(self.visit(n))
        return result

    @_indenter
    def visit_NotNode(self, node):
        result = [node.get_name()]
        result.extend(self.visit(node.node))
        return result

    @_indenter
    def visit_IsRelativeOfNode(self, node):
        result = [ node.get_name() + "(%s)" % node.relation ]
        result.extend(self.visit(node.subtree))
        return result

    @_indenter
    def visit_WithNode(self, node):
        result = [ "%s(%s)" % (node.get_name(), node.format_params()) ]
        result.extend(self.visit(node.node))
        return result

def formatTree(tree):
    formatter = TreeFormatter()
    return '\n'.join(formatter.visit(tree))

__all__ = [ 'DimNode', 'WithNode', 'NotNode', 'AndNode',
        'OrNode', 'SetNode', 'IsRelativeOfNode',
        'AvailabilityNode', 'RangeNode',
        'DefinitionNode', 'ParseTreeVisitor', 'ParseTreeTransformer', 'DimParseTreeError', 'formatTree',
        'render_dimensions_tree', 'meta_render_dimensions_tree',
        ]
