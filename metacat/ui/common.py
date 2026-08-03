#
# not used
#

from metacat.webapi import MCError
from metacat.webapi.webapi import NotFoundError,WebAPIError,InvalidMetadataError
from metacat.util import ObjectSpec
import sys, json, os.path

def exit_code_from_exception(e):
    if isinstance(e, NotFoundError):
        msg = f"Exception: not found: {e.args}"
        print(msg, file=sys.stderr)
        error_code=12
    elif isinstance(e, InvalidMetadataError):
        msg = f"Exception: {e.__class__.__name__}: {str(e)}"
        print(msg, file=sys.stderr)
        error_code = sum([ord(c) for c in msg[:20]]) % 63 + 32
    elif isinstance(e, WebAPIError):
        msg = f"Exception: {e.__class__.__name__}: {e.Message}"
        print(msg, file=sys.stderr)
        error_code = sum([ord(c) for c in msg[:20]]) % 63 + 32
    elif isinstance(e, Exception):
        msg = f"Exception: {e.__class__.__name__} {' '.join([str(x) for x in e.args])}"
        print(msg, file=sys.stderr)
        error_code = sum([ord(c) for c in msg[:20]]) % 63 + 32
    else:
        msg = str(e)
        error_code = sum([ord(c) for c in msg[:20]]) % 63 + 32
    return error_code

def catch_mc_errors(method):
    def decorated(*params, **args):
        try:
            return method(*params, **args)
        except MCError as e:
            sys.exit(exit_code_from_exception(e))
    return decorated

def load_text(arg):
    text = arg
    if arg:
        file_path = None
        if arg.startswith('@'):             # accept "@<file>" for backward compatibility
            file_path = arg[1:]
        elif os.path.isfile(arg):
            file_path = arg
        if file_path:
            text = open(file_path, "r").read()
        elif arg == "-":
            text = sys.stdin.read()
    return text

def load_json(arg):
    data = None
    text = load_text(arg)
    if text:
        data = json.loads(text)
    return data

def parse_file_spec(spec):
    if isinstance(spec, dict) and "did" in spec:
        ns, n = spec["did"].split(':', 1)
        return {"namespace":ns, "name":n}

def load_file_list(arg):
    text = load_text(arg)
    data = []
    try:
        data = json.loads(text)
    except:
        #print(f"load_file_list: received text: [{text}]")
        for line in text.split("\n"):
            line = line.strip()
            if line:
                for item in line.split(","):
                    item = item.strip()
                    if item:
                        data.append(item)
    #print("load_file_list: data:", data)
    return [ObjectSpec(item).as_dict() for item in data]
        
