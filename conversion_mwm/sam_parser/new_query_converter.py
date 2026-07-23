#!/usr/bin/python3

import sys
import os
import re
from parser import DimParserError
from parse_tree import *
from random import random, randint, choice, seed
import parser
import logging

logger = logging.getLogger(__name__)

leading_re = re.compile(r'^([^,]*,){3}')
trailing_re =  re.compile(r'(,[^,]*){4}$')

 
if __name__ == "__main__":

    loglevel = logging.INFO
    if "-d" in sys.argv:
        loglevel = logging.DEBUG

    logging.basicConfig(level = loglevel )

    for dims in sys.stdin.readlines():
        m = leading_re.match( dims )
        if m:
            #print("saw leading: ", m.group(0))
            leading_cols = m.group(0)
            dims = leading_re.sub('', dims)
            m = trailing_re.search( dims )
            if m:
                #print("saw trailing: ", m.group(0))
                trailing_cols = m.group(0)
                dims = trailing_re.sub('', dims)
            else:
                trailing_cols = ''
        else:
            leading_cols = ''
            trailing_cols = ''


        #print("=-=-=-=-=-=-=-=-=-=")
        #print(dims) 
        #print("===================")
        try:
            t = parser.parse_string(dims)
            #print("parse tree: ", str(t), "\n\n")
            #print("-------------------")
            mt = MetaCatTransformer().visit(t)
            mt = MetaCatTransformerPart2().visit(mt)
            #print("meta tree: ", str(mt), "\n\n")
            meta = meta_render_dimensions_tree(mt)
        except DimParserError:
            meta = dims.strip()
        print(leading_cols, meta, trailing_cols)
