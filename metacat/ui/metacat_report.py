import sys, getopt, os, json, pprint
from urllib.request import urlopen, Request
from urllib.parse import quote_plus, unquote_plus
from metacat.util import to_bytes, to_str
from metacat.webapi import MetaCatClient, MCServerError, MCWebAPIError, MCError
from metacat.ui.cli import CLICommand, InvalidArguments, InvalidOptions

class ReportMetadataCommand(CLICommand):

    GNUStyle = False    
    Opts = (
        "jlpst:v:", 
        [ "list", "json", "summary", "pretty", "timeout=", "values=" ]
    )
    Usage = """[<options>] (-q <MQL query file>|"<MQL query>")

        Options:
            -l|--list                           - list all metadata keys
            -j|--json                           - print raw JSON output
            -p|--pprint                         - output with Python pprint
            -s|--summary                        - print summary data (counts, max, min) 
            -t|--timeout <timeout in seconds>   - request timeout (default 1200)
            -v|--values <key>[,<key>...]        - list all values for named keys
    """
    
    def __call__(self, command, client, opts, args):
        with_summary = "-s" in opts or "--summary" in opts
        list_keys  = "-l" in opts or "--list" in opts
        json_out = "-j" in opts or "--json" in opts
        timeout = int(opts.get("-t", opts.get("--timeout", 1200)))
        values = opts.get("-v", opts.get("--values", ""))

        if values:
            values = values.split(",")
         
        client.Timeout = timeout

        jval = {}

        if list_keys or summary:

            metacat_keys = client.report_metacat_keys()
            if with_summary:
                summary_values = client.report_metadata_counts_ranges(metacat_keys)

            if json_out or pprint:
               jval['keys'] =  list(metacat_keys)
               if with_summary:
                   jval['summary'] = summary_values
            else:
                if summary:
                    print("key\t\tcount\t\tmin\t\tmax")
                    print("---\t\t-----\t\t---\t\t---")
                else:
                    print("key")
                    print("---")
                for key in metacat_keys:
                    if summary:
                        print("{key:15} {summary_values[key+'.count']:15} {summary_values[key+'.min']:15} {summary_values[key+'.max']:15}")
                    else:
                        print(key)
        
        if values:
            if json_out or pprint:
               jval["values"] = {}
            else:
               print("\nall values table:\n")
               print("key\t\tvalues")
               print("---\t\t------")

            for key in values:

                kvals = client.report_metadata_values(key)

                pkey = key
                if json_out or pprint:
                    jval["values"][key] = list(kvals)
                else:
                    for v in kvals:
                        print("{pkey:15} {v}") 
                        pkey=" "

        if pprint:
            pprint.pprint(jval)

        if json_out:
            json.dumps(jval)
                       
QueryInterpreter = QueryCommand()
