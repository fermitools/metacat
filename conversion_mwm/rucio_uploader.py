#!/usr/bin/env python

"""
   upload stream of file dids or file replicas to Rucio
"""

import argparse
import os
import regex
import sys

def upload_replicas(fin, rse, scope, add_to_dataset=None, pfn_prefix=''):
    """ 
        upload_replicas: 
        * read csv filename, size, checksum data
        * add to rucio

        NOTE: this is an initial prototype which uses add_replica()
            (basically stolen from declad)
            we should probably instead batch up and do batch adds... 
            in groups of 500 or so...
    """

    fid_parse = re.parse(r'([^,]*),([^,]*),([^,]*),(.*)'))
    rclient = rucio.client.Client()

    for line in fin.readlines():
        m = fid_parse.match(line)
        if m:
             fname, fsize, fchksum, path = m.groups()
             pfn = os.path.join(pfn_prefix, path)

             rclient.add_replica(rse, scope, fname, int(fsize), adler32=fchksum, pfn=pfn) 

             if add_to_dataset:
                 rclient.rclient.attach_dids( 
                     dataset, 
                     [{"scope":scope, "name":fname}],
                 )

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file-replicas', default=False , action='store_true')
    ap.add_argument('--rse', default='FNAL_DCACHE')
    ap.add_argument('--scope', default='sam')
    ap.add_argument('--dataset', default='')
    ap.add_argument('--pfn-prefix', default='davs:fndcadoor.fnal.gov:2880')
    args = ap.parse_args()
    
    if file_replicas:
         upload_file_replicas(sys.stdin, args.rse, args.scope, args.dataset, args.pfn_prefix )
