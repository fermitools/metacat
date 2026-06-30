
import sys
import os
from parse_tree import *
from random import random, randint, choice, seed
from parser import DimParserError

#def genTree(d):
#    print "genTree(%d)" % d
#    res = genTree2(d)
#    print "genTree(",d,"): returning: ",res
#    return res

def genTree(d, parentNodeType=None):
    """ 
       Generate a parse tree of maximum depth d
       there is a 1/d probability of generating a leaf {Definition,Dim} node
    """
    if d <= 0:
        raise Error("Ouch! should not get to d<= 0")

    isleaf = randint(1,2*d)
    plist = []
    if isleaf == 1 or isleaf == 2:
        leaftype = randint(1,10)
        if leaftype == 1: return DefinitionNode(_genDef())
        if leaftype == 2: return DimNode(_geniDim(), _genOp(), _geniValue())
        if leaftype == 3: return DimNode(_gendDim(), _genOp(), _gendValue())
        if leaftype == 4: return DimNode(_genfDim(), _genOp(), _genfValue())
        if leaftype == 5: return DimNode(_gensDim(), _genOp(), _gensValue())
        if leaftype == 6: return DimNode(_geniDim(), "in", _geniRange())
        if leaftype == 7: return DimNode(_gendDim(), "in", _gendRange())
        if leaftype == 8: return DimNode(_genfDim(), "in", _genfRange())
        if leaftype == 9: return DimNode(_gensDim(), "in", _gensList())
        if leaftype ==10: return DimNode(_gensDim(), "not in", _gensList())
    elif isleaf == 3:
        return NotNode(genTree(d-1))
    elif isleaf == 4:
        return IsRelativeOfNode(_genRelation(), genTree(d-1))
    elif isleaf == 5:
        params = {}
        if random() > 0.6:
            params['limit'] = randint(1,1000)
        if random() > 0.6:
            params['offset'] = randint(1,1000)
        if random() > 0.6:
            params['stride'] = randint(1,1000)
        if not params or random() > 0.8:
            params['availability'] = list(set(_genAvail() for i in range(randint(1,5) )))
        return WithNode(genTree(d-1), params)
    else:
        nodetype = randint(1,6)
        nkids = randint(2,20)
        #print "nkids %d nodetype %d\n" % ( nkids, nodetype )
        if nodetype in (1,2): ntype = AndNode
        if nodetype in (3,4): ntype = OrNode
        if nodetype == 5: ntype = lambda *args: SetNode('minus', *args)
        if nodetype == 6: ntype = lambda *args: SetNode('intersect', *args)
        for i in range(nkids):
            st = genTree(d-1, ntype)
            #print "got subtree:" , st
            plist.append(st)
        return ntype(*plist)
    # shouldn't get here, but don't return nothing
    return DimNode("wrong","=","wrong")
 

# generate random dminsions of various types
def _gensDim():
    return choice(["file_name","data_tier","family","version","application","full_path","table_label","project_name","consumer","consumed_status","dataset_def_name","run_type","datastream"])
def _gendDim():
     return choice(["start_time","end_time","create_date"])
def _geniDim():
     return choice(["file_id","file_partition","file_size","event_count","dataset_def_id","snapshot_id","snapshot_version","run_number","subrun_number"])
def _genfDim():
     return choice(["category.param1","category.param2"])

# generate random values of various types
def _gensValue():
     return choice(["thing1","thing2","xyzzy","foo","bar","baz","bleem"])

def _gendValue():
     return "%02d" % randint(1,29) + choice(["-Jan-","-Feb-","-Mar-","-Apr-","-Oct-"]) + choice(["2009","2010","2011","1970"])

def _geniValue():
     return "%d" % randint(1,8192)

# generate ranges/lists
def _geniRange():
     return RangeNode(_geniValue(),_geniValue())

def _gensList():
     res = []
     for i in 1, randint(2,15):
         res.append(_gensValue())
     return res

def _gendRange():
     return RangeNode(_gendValue(), _gendValue())

def _genfValue():
     return "%f" % (randint(1,65536) * 1.0 / randint(1,255))

def _genfRange():
     return RangeNode(_genfValue(), _genfValue())

def _genDef():
     return choice(["dataset1","dataset2","dataset3"])

def _genOp():
     return choice(["<", "=", ">", ">=", "<=", "!="])

def _genRelation():
     return choice(["ischildof","isparentof"])

def _genAvail():
     return choice(["any","virtual","bad","good"])

if __name__ == "__main__":
    import parser
    #import dimension_query.exc
    if len(sys.argv) > 1:
       print("using seed:", int(sys.argv[1]))
       seed(int(sys.argv[1]))
    else:
       print("picking seed:", os.getpid())
       seed(os.getpid())
    t = genTree(5)
    #parser.process_tree(t)
    print(t)
    print("-------")
    dims = render_dimensions_tree(t)
    print(dims)
    print("======")
    mt = MetaCatTransformer().visit(t)
    meta = meta_render_dimensions_tree(mt)
    print(meta)
    sys.exit(0)
    try:
        newt = parser.parse_string(dims)
    except DimParserError as ex:
        print("Parsing failed: %s" % ex)
        sys.exit(1)
    if newt != t:
        print("New tree:")
        print(newt)
        print()
        print("Mismatch!")
        sys.exit(1)




