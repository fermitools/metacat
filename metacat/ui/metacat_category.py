import sys, getopt, os, json, fnmatch, pprint
from datetime import datetime, timezone
from urllib.parse import quote_plus, unquote_plus
from metacat.util import to_bytes, to_str
from metacat.webapi import MetaCatClient, MCError
from metacat.ui.cli import CLI, CLICommand, InvalidOptions, InvalidArguments
from metacat.ui.common import load_json

def print_category(data):
    print("Path:            ", data["path"])
    print("Description:     ", data.get("description") or "")
    print("Owner user:      ", data.get("owner_user", "") or "")
    print("Owner role:      ", data.get("owner_role", "") or "")
    print("Creator:         ", data.get("creator", ""))
    ct = data.get("created_timestamp") or ""
    if ct:
        ct = datetime.fromtimestamp(ct, timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")
    print("Created at:      ", ct)
    print("Restricted:      ", "yes" if data.get("restricted", False) else "no")
    print("Required:        ", "yes" if data.get("required", False) else "no")
    print("Constraints:")
    for name, constraint in sorted(data.get("definitions", {}).items()):
        required = str(constraint.get("required", False)).strip().lower() == "true"
        line = "  %-16s Required: %-24s %10s" % (name, "yes" if required else "no", constraint.get("type", "any"))
        if "values" in constraint:
            line += " %s" % (tuple(constraint["values"]),)
        rng = None
        if "min" in constraint:
            rng = [repr(constraint["min"]), ""]
        if "max" in constraint:
            if rng is None: rng = ["", ""]
            rng[1] = repr(constraint["max"])
        if rng is not None:
            line += " [%s - %s]" % tuple(rng)
        if "pattern" in constraint:
            line += " ~ '%s'" % (constraint["pattern"])
        print(line)

class ListCommand(CLICommand):

    GNUStyle = True
    Opts = "j json"
    Usage = """[options] [<root category>]
        -j|--json           - print as JSON
    """

    def __call__(self, command, client, opts, args):
        root = None if not args else args[0]
        as_json = "-j" in opts or "--json" in opts
        lst = client.list_categories(root)
        if as_json:
            print(json.dumps(lst, indent=4, sort_keys=True))
        else:
            for c in lst:
                print(c["path"])
                
class ShowCommand(CLICommand):
    
    GNUStyle = True
    Opts = "j json"
    MinArgs = 1
    Usage = """[-j|--json] <category>
        -j|--json           - print as JSON
    """
    
    def __call__(self, command, client, opts, args):
        data = client.get_category(args[0])
        if data:
            if "-j" in opts or "--json" in opts:
                print(json.dumps(data, indent=4, sort_keys=True))
            else:
                print_category(data)

class CreateCommand(CLICommand):
    Opts = "d:r:n:o:p:j"
    MinArgs = 1
    Usage = """ [options] <category>        --create a category
    -d                      - description
    -r                      - restricted (True/False), default False
    -n                      - required (True/False), default False
    -o                      - owner
    -p                      - parameter definitions (json file or dictionary)
    -j                      - print as JSON
    """

    def __call__(self, command, client, opts, args):
        catname = args[0]
        description = opts.get("-d")
        if "-r" in opts:
            if opts.get("-r") == "True":
                restricted = True
            elif opts.get("-r") == "False":
                restricted = False
            else:
                restricted = False
        else:
            restricted = False

        required = "-n" in opts
        if "-n" in opts:
           if opts.get("-n") == "True":
               required = True
           elif opts.get("-n") == "False":
               required = False
           else:
               required = False
        else:
            required = False

        owner = opts.get("-o")

        defs_file = opts.get("-p")
        if defs_file:
            defs = load_json(defs_file)
            if not isinstance(defs, dict):
                raise InvalidArguments("Definitions must be a dictionary")

        cat = client.create_category(path=catname, owner_role=owner, restricted=restricted, required=required, description=description, definitions=defs)
        if "-j" in opts:
            print(json.dumps(cat))
        else:
            print_category(cat)

class UpdateCommand(CLICommand):
    Opts = "d:r:n:o:p:m:j"
    MinArgs = 1
    Usage = """ [options] <category>        --update a category
    -d                      - description
    -r                      - restricted (True/False)
    -n                      - required (True/False)
    -o                      - owner
    -p                      - parameter definitions (json file or dictionary)
    -m                      - mode (update, replace), default update
    -j                      - print as JSON
    """

    def __call__(self, command, client, opts, args):
        catname = args[0]

        updates = {}
        if opts.get("-d") is not None:
            updates["description"] = opts.get("-d")
        if opts.get("-r") is not None:
            updates["restricted"] = bool(opts.get("-r"))
        if opts.get("-n") is not None:
            updates["required"] = bool(opts.get("-n"))
        if opts.get("-o") is not None:
            updates["owner_role"] = opts.get("-o")

        defs_file = opts.get("-p")
        if defs_file:
            defs = load_json(defs_file)
            if not isinstance(defs, dict):
                raise InvalidArguments("Definitions must be a dictionary")
            updates["definitions"] = defs

        updates["mode"] = opts.get("-m", "update")

        cat = client.update_category(path=catname, **updates)
        if "-j" in opts:
            print(json.dumps(cat))
        else:
            print_category(cat)

class RemoveCommand(CLICommand):
    Usage = """ <category>                  --remove a category"""
    MinArgs = 1

    def __call__(self, command, client, opts, args):
        cat_name = args[0]
        try:
            removed = client.remove_category(cat_name)
        except MCError as e:
            print(e)
            sys.exit(1)


CategoryCLI = CLI(
    "list",     ListCommand(),
    "show",     ShowCommand(),
    "create",   CreateCommand(),
    "update",   UpdateCommand(),
    "remove",   RemoveCommand()
)
 
