
import sys
import os
import re
from parse_tree import *
from random import random, randint, choice, seed
from parser import DimParserError
import parser

leading_re = re.compile(r'^([^,]*,){3}')
trailing_re =  re.compile(r'(,[^,]*){4}$')

 
if __name__ == "__main__":
    for dims in sys.stdin.readlines():
        m = leading_re.match( dims )
        if m:
            print("saw leading: ", m.group(0))
            leading_cols = m.group(0)
            dims = leading_re.sub('', dims)
            m = trailing_re.search( dims )
            if m:
                print("saw trailing: ", m.group(0))
                trailing_cols = m.group(0)
                dims = trailing_re.sub('', dims)
            else:
                trailing_cols = ''
        else:
            leading_cols = ''


        print("=-=-=-=-=-=-=-=-=-=")
        print(dims) 
        print("===================")
        t = parser.parse_string(dims)
        print("parse tree: ", str(t), "\n\n")
        print("-------------------")
        mt = MetaCatTransformer().visit(t)
        print("meta tree: ", str(mt), "\n\n")
        meta = meta_render_dimensions_tree(mt)
        print(leading_cols, meta, trailing_cols)
