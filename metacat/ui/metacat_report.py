import sys, getopt, os, json, pprint
from urllib.request import urlopen, Request
from urllib.parse import quote_plus, unquote_plus
from metacat.util import to_bytes, to_str
from metacat.webapi import MetaCatClient, MCServerError, MCWebAPIError, MCError
from metacat.ui.cli import CLICommand, InvalidArguments, InvalidOptions

class ReportMetadata(CLICommand):

    GNUStyle = False    
    Opts = (
        "jlpst:v:", 
        [ "list", "json", "summary", "pretty", "timeout=", "values=" ]
    )
    Usage = """[<options>]

        Options:
            -l|--list                           - list all metadata keys
            -j|--json                           - print raw JSON output
            -p|--pretty                         - output with Python pprint
            -s|--summary                        - print summary data (counts, max, min) 
            -t|--timeout <timeout in seconds>   - request timeout (default 1200)
            -v|--values <key>[,<key>...]        - list all values for named keys
    """
    
    def __call__(self, command, client, opts, args):
        with_summary = "-s" in opts or "--summary" in opts
        list_keys  = "-l" in opts or "--list" in opts
        json_out = "-j" in opts or "--json" in opts
        pretty = "-p" in opts or "--pretty" in opts
        timeout = int(opts.get("-t", opts.get("--timeout", 1200)))
        values = opts.get("-v", opts.get("--values", ""))

        if values:
            values = values.split(",")
         
        client.Timeout = timeout

        jval = {}

        if list_keys or with_summary:

            metacat_keys = client.report_metadata_keys()
            if with_summary:
                summary_values = client.report_metadata_counts_ranges(metacat_keys)

            if json_out or pretty:
               jval['keys'] =  list(metacat_keys)
               if with_summary:
                   jval['summary'] = summary_values
            else:
                if with_summary:
                    print("key\t\t\tcount\tmin\t\t\t\tmax")
                    print("--------\t\t-------\t---\t\t\t\t---")
                else:
                    print("key")
                    print("---")
                #print(f"{summary_values=}")
                for key in metacat_keys:
                    if with_summary:
                        print(f"{key:23} {summary_values[key+'.count']:7} {summary_values[key+'.min']:31} {summary_values[key+'.max']}")
                    else:
                        print(key)
        
        if values:
            if json_out or pretty:
               jval["values"] = {}
            else:
               print("\nall values table:\n")
               print("key\t\tvalues")
               print("---\t\t------")

            for key in values:

                kvals = client.report_metadata_values(key)

                if json_out or pretty:
                    jval["values"][key] = list(kvals)
                else:
                    pkey = key
                    for v in kvals:
                        print(f"{pkey:15} {v}") 
                        pkey=" "

        if pretty:
            pprint.pprint(jval)

        if json_out:
            print(json.dumps(jval))
                       
ReportMetadataCommand = ReportMetadata()
