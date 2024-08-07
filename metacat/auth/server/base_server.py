# common functionality for Auth, GUI and Data servers

from webpie import WPApp, Response, WPHandler
from wsdbtools import ConnectionPool
from metacat.util import to_str, to_bytes
from metacat.auth import BaseDBUser, AuthenticationCore
from metacat.common import (
    SignedToken, SignedTokenExpiredError, SignedTokenImmatureError, SignedTokenUnacceptedAlgorithmError, 
    SignedTokenSignatureVerificationError
)
import psycopg2, json, time, secrets, traceback, hashlib, pprint, os, yaml
from urllib.parse import quote_plus, unquote_plus

class BaseApp(WPApp):

    def __init__(self, cfg, root_handler, **args):
        WPApp.__init__(self, root_handler, **args)
        self.Cfg = cfg
        
        db_config = cfg["database"]
        self.DB = ConnectionPool(postgres=db_config, max_idle_connections=1, idle_timeout=5)

        if "user_database" in cfg:
            self.UserDB = ConnectionPool(postgres=cfg["user_database"], max_idle_connections=1, idle_timeout=5)
        else:
            self.UserDB = self.DB

        auth_config = cfg.get("authentication", {})
        self.Realm = auth_config.get("realm", "metacat")
        self.AuthCore = AuthenticationCore(cfg)
        
    def connstr(self, cfg):
        cs = "host=%(host)s port=%(port)s dbname=%(dbname)s user=%(user)s" % cfg
        if cfg.get("password"):
            cs += " password=%(password)s" % cfg
        return cs
        
            
    def init(self):
        #print("ScriptHome:", self.ScriptHome)
        self.initJinjaEnvironment(tempdirs=[self.ScriptHome, self.ScriptHome + "/templates"])
        
    def init_auth_core(self, config):
        self.AuthCore = AuthenticationCore(config)
        return self.AuthCore

    def connect(self):
        return self.DB.connect()
        
    db = connect        # for compatibility
    
    def user_db(self):
        return self.UserDB.connect()
        
    def auth_config(self, method, group=None):
        return self.auth_core(group).auth_config(method)

    # overridable
    def auth_core(self, realm = None):
        return self.AuthCore

    def get_digest_password(self, realm, username):
        db = self.connect()
        u = BaseDBUser.get(db, username)
        if u is None:
            return None
        return u.get_password(self.Realm)

    TokenExpiration = 24*3600*7

    def user_from_request(self, request):
        return self.AuthCore.user_from_request(request)
            
class BaseHandler(WPHandler):
    
    def __init__(self, request, app, group=None):
        WPHandler.__init__(self, request, app)
        self.Group = group
        self.AuthCore = app.auth_core(group)
    
    def connect(self):
        return self.App.connect()

    def text_chunks(self, gen, chunk=10000):
        buf = []
        size = 0
        for x in gen:
            n = len(x)
            buf.append(x)
            size += n
            if size >= chunk:
                #print("yielding:", "".join(buf))
                yield "".join(buf)
                size = 0
                buf = []
        if buf:
            #print("final yielding:", "".join(buf))
            yield "".join(buf)
            
    def authenticated_username(self):
        username, error = self.AuthCore.user_from_request(self.Request)
        return username, error

    def authenticated_user(self):
        username, error = self.authenticated_username()
        if username:
            user = self.AuthCore.get_user(username)
            if user is not None:
                return user, None
            error = f"user {username} not found"
        return None, error

    def jinja_globals(self):
        return {"G_User":self.authenticated_user()[0]}

    def messages(self, args):
        return {k: unquote_plus(args.get(k,"")) for k in ("error", "message")}
        

