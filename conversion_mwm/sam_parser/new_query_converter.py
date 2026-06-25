
import sys
import os
from parse_tree import *
from random import random, randint, choice, seed
from parser import DimParserError
import parser

 
if __name__ == "__main__":
    for dims in sys.stdin.readlines():
        print("=-=-=-=-=-=-=-=-=-=")
        print(dims) 
        print("===================")
        t = parser.parse_string(dims)
        print("parse tree: ", str(t), "\n\n")
        print("-------------------")
        mt = MetaCatTransformer().visit(t)
        print("meta tree: ", str(mt), "\n\n")
        meta = meta_render_dimensions_tree(mt)
        print(meta)
