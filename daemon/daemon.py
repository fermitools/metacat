import json, requests, time, psycopg2, re
from pythreader import TaskQueue
from metacat.db import DBUser, DBNamespace, DBDataset, DBFile, DBRole
from metacat.logs import Logged, init as init_logs
from wsdbtools import ConnectionWithTransactions
import functools, traceback


def log_exceptions(f):
    """decorator logging exceptions and not letting them past"""

    @functools.wraps(f)
    def wrapper(self, *args, **kwargs):
        try:
            return f(self, *args, **kwargs)
        except Exception:
            self.log(traceback.format_exc())
            return None

    return wrapper


class MetaCatDaemon(Logged):
    def __init__(self, config):
        Logged.__init__(self, "MetaCatDaemon")

        ssl_config = config.get("ssl", {})
        self.CertFile = ssl_config.get("cert", None)
        self.KeyFile = ssl_config.get("key", self.CertFile)
        self.TokenFile = ssl_config.get("token", None)

        daemon_config = config["daemon"]
        self.FerryURL = daemon_config["ferry_url"]
        if self.FerryURL.lower().startswith("https:") and not (
            (self.CertFile and self.KeyFile) or self.TokenFile
        ):
            raise ValueError("Token file, or X.509 cert and key files are not in the configuration")

        self.FerryUpdateInterval = daemon_config.get("ferry_update_interval", 1 * 3600)
        self.CountsUpdateInterval = daemon_config.get("counts_update_interval", 1 * 3600)
        self.VO = daemon_config["vo"]

        role_pattern_txt = daemon_config.get("role_pattern", "")
        if role_pattern_txt: 
            self.role_pattern = re.compile(role_pattern_txt)
        else:
            self.role_pattern = None

        self.role_template = daemon_config.get("role_template", "")

        db_config = config["database"]
        self.DBConnect = "host=%(host)s port=%(port)s dbname=%(dbname)s user=%(user)s" % db_config
        if "password" in db_config:
            self.DBConnect += " password=%(password)s" % db_config


        self.Schema = db_config.get("schema")


        self.Queue = TaskQueue(5, delegate=self)
        self.Queue.append(self.ferry_update, interval=self.FerryUpdateInterval, after=time.time())
        self.Queue.append(
            self.update_dataset_file_counts, interval=self.CountsUpdateInterval, after=0
        )  # self.CountsUpdateInterval//3)
        self.Queue.append(
            self.update_namespace_file_counts, interval=self.CountsUpdateInterval, after=0
        )  # 2*self.CountsUpdateInterval//3)
        self.debug("tasks enqueued")

    def db(self):
        db = psycopg2.connect(self.DBConnect)
        if self.Schema:
            db.cursor().execute(f"set search_path to {self.Schema}")
        return ConnectionWithTransactions(db)

    @log_exceptions
    def update_dataset_file_counts(self):
        db = self.db()
        counts = DBDataset.file_count_by_dataset(db)
        for ds in DBDataset.list(db):
            ds.FileCount = counts.get((ds.Namespace, ds.Name), 0)
            ds.save()
        db.close()
        self.log("Dataset file counts updated")

    @log_exceptions
    def update_namespace_file_counts(self):
        db = self.db()
        counts = DBFile.file_count_by_namespace(db)
        for ns in DBNamespace.list(db):
            ns.FileCount = counts.get(ns.Name, 0)
            ns.save()
        db.close()
        self.log("Namespace file counts updated")


    def do_auth(self):
        if self.CertFile:
            cert = (self.CertFile, self.KeyFile)
        else:
            cert = None

        if self.TokenFile:
            with open(self.TokenFile, "r") as tf:
                token = tf.read().strip()
            headers = {"Authorization": "Bearer " + token}
        else:
            headers = None
        return cert, headers


    def fetch_url(self, url):

        self.debug("ferry URL:", url)
        cert, headers = self.do_auth()
        response = requests.get(url, verify=False, cert=cert, headers=headers)
        data = response.json()
        self.debug("data received")

        status = data["ferry_status"]
        if status != "success":
            print("\nFerry error:")
            for line in data["ferry_error"]:
                print(line)
            raise ValueError(f"Ferry error: {data['ferry_error']}")

        return data

    def ferry_user_roles(self, user):
        self.debug(f"ferry_user_roles: {user}")
        url = f"{self.FerryURL}/getUserFQANs?username={user}&unitname={self.VO}"

        data = self.fetch_url(url)

        for item in data["ferry_output"]: 
            m = self.role_pattern.search(item["fqan"])
            if m:
                # substitute subexpressions from pattern match
                rolename = self.role_template
                for i,val in enumerate(m.groups(), start=1):
                    rolename = rolename.replace(f"${i}", val)
                if rolename.find('$') >= 0:
                    self.debug(f"role template from {item['fqan']} still has '$' : {rolename}, skipping")
                    continue

                do_save = False
                db = self.db()
                role = DBRole.get(db, rolename)

                if not role:
                    self.debug(f"creating new role {rolename}")
                    role = DBRole(db, rolename, "auto-imported from FERRY")
                    role.save()
                    
                if not user in role.members:
                    self.debug(f"adding {user} to {rolename}")
                    role.add_member(user)


    @log_exceptions
    def ferry_update(self):
        self.debug("ferry_update...")
        url = f"{self.FerryURL}/getAffiliationMembersRoles?unitname={self.VO}"

        data = self.fetch_url(url)

        ferry_users = {item["username"]: item for item in data["ferry_output"][self.VO]}
        self.log("Loaded", len(ferry_users), "users from Ferry")

        db = self.db()
        db_users = {u.Username: u for u in DBUser.list(db)}

        ncreated = nupdated = 0
        updated = []
        created = []
        for username, ferry_user in ferry_users.items():
            db_user = db_users.get(username)
            if db_user is None:
                new_user = DBUser(
                    db,
                    username,
                    ferry_user.get("fullname", ""),
                    None,
                    "",
                    None,
                    ferry_user.get("tokensubject"),
                )
                new_user.save()
                ncreated += ncreated
                created.append(username)
            else:
                uuid = ferry_user.get("tokensubject")
                name = ferry_user.get("fullname")
                do_update = False
                if uuid and uuid != db_user.AUID:
                    db_user.AUID = uuid
                    do_update = True
                if name and name != db_user.Name:
                    db_user.Name = name
                    do_update = True
                if do_update:
                    db_user.save()
                    nupdated += 1
                    updated.append(username)

            if self.role_pattern:
                self.ferry_user_roles(username)

        self.log("created:", len(created), "" if not created else ",".join(created))
        self.log("updated:", len(updated), "" if not updated else ",".join(updated))
        db.close()


Usage = """
daemon.py -c <config.yaml> [-d] [-l <log path>]
"""


def main():
    import sys, getopt, yaml, time

    opts, args = getopt.getopt(sys.argv[1:], "l:c:dh?", ["help"])
    opts = dict(opts)

    if "-c" not in opts or "-?" in opts or "-h" in opts or "--help" in opts:
        print(Usage)
        sys.exit(2)


    config = yaml.load(open(opts["-c"], "r"), Loader=yaml.SafeLoader)
    log_file = opts.get("-l", "-")
    init_logs(log_file, error_out=log_file, debug_out=log_file, debug_enabled="-d" in opts)

    daemon = MetaCatDaemon(config)
    while True:
        time.sleep(10)

if __name__ == "__main__":
    main()
