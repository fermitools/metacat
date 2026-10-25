#!/usr/bin/python3

import sys
import os
import re
from query_converter.parser import DimParserError
from query_converter.parse_tree import *
from random import random, randint, choice, seed
import query_converter.parser as parser
import logging

logger = logging.getLogger(__name__)

leading_re = re.compile(r'^([^,]*,){3}')
trailing_re =  re.compile(r'(,[^,]*){4}$')

 
if __name__ == "__main__":

    loglevel = logging.INFO
    query_only = False
    if "-q" in sys.argv:
        query_only  = True
    if "-d" in sys.argv:
        loglevel = logging.DEBUG
    if "--db" in sys.argv:
        dbconninfo = sys.argv[-1]
    else:
        dbconninfo = None
         

    logging.basicConfig(level = loglevel )

    for dims in sys.stdin.readlines():
        m = leading_re.match( dims )
        if m and not query_only:
            logging.debug(f"saw leading: {m.group(0)}")
            leading_cols = m.group(0)
            dims = leading_re.sub('', dims)
            m = trailing_re.search( dims )
            if m:
                logging.debug(f"saw trailing: {m.group(0)}")
                trailing_cols = m.group(0)
                dims = trailing_re.sub('', dims)
            else:
                trailing_cols = ''
        else:
            leading_cols = ''
            trailing_cols = ''


        logging.debug("=-=-=-=-=-=-=-=-=-=")
        logging.debug(dims) 
        logging.debug("===================")
        try:
            t = parser.parse_string(dims)
            logging.debug(f"parse tree: {str(t)}\n\n")
            logging.debug("-------------------")
            mt = MetaCatTransformer().visit(t)
            mt = MetaCatTransformerPart2().visit(mt)
            logging.debug(f"meta tree: {str(mt)}\n\n")
            meta = meta_render_dimensions_tree(mt)
        except DimParserError:
            meta = dims.strip()
        print(f"{leading_cols}{meta}{trailing_cols}")
