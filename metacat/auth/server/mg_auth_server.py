#
# Multi-group authentication server
#

from metacat.auth.server import auth_router, install_exception_handlers
from metacat.auth.auth_core import AuthenticationCore
from metacat.util import to_str
from fastapi import FastAPI
from fastapi.responses import Response, PlainTextResponse
from a2wsgi import ASGIMiddleware

import time, os, yaml, os.path, sys
from urllib.parse import quote_plus, unquote_plus

class MultiGroupAuthServerApp(object):

    TokenExpiration = 24*3600*7

    def __init__(self, config):
        self.Cfg = config

        self.Cores = {
            group: AuthenticationCore(group_cfg, group)
            for group, group_cfg in config["groups"].items()
        }

    def groups(self):
        return self.Cores.keys()

    def auth_core(self, group):
        return self.Cores.get(group)
        
    def response_with_auth_cookie(self, user, redirect, token=None, expiration=None, group=None):
        # expiration here is absolute time
        #print("response_with_auth_cookie: user:", user, "  redirect:", redirect)
        if expiration is None:
            expiration = self.TokenExpiration + time.time()
        if token is not None:
            encoded = token.encode()
        else:
            token, encoded = self.auth_core(group).generate_token(user, {"user": user}, expiration=expiration)
        #print("Server.App.response_with_auth_cookie: new token created:", token.Payload)
        #print("   ", encoded)
        #print("   time:", time.time())
        if redirect:
            resp = Response(status_code=302, headers={"Location": redirect})
        else:
            resp = PlainTextResponse("")
        #print ("response:", resp, "  reditrect=", redirect)
        resp.headers["X-Authentication-Token"] = to_str(encoded)
        resp.set_cookie("auth_token", to_str(encoded), max_age = max(0, int(expiration - time.time())))
        #print("BaseApp.response_with_auth_cookie: returning", resp)
        return resp

def create_application(config=None):
    if config is None:  config = os.environ.get("AUTH_SERVER_CFG")
    if config is None:  
        print("Config file is not defined. Use AUTH_SERVER_CFG environment variable", file=sys.stderr)
        sys.exit(1)
    if isinstance(config, str):
        config = yaml.load(open(config, "r"), Loader=yaml.SafeLoader)
    app = MultiGroupAuthServerApp(config)

    fastapi_app = FastAPI(title="MetaCat Multi-Group Authentication Server", docs_url=None, redoc_url=None, openapi_url=None)
    fastapi_app.state.metacat_app = app
    install_exception_handlers(fastapi_app)
    # each group has its own set of the authentication methods under /<group>/...
    # allow /auth/ on front for direct uwsgi setup
    fastapi_app.include_router(auth_router, prefix="/auth/{group}")
    fastapi_app.include_router(auth_router, prefix="/{group}")
    return fastapi_app

if __name__ == "__main__":
    import uvicorn, getopt, ssl
    
    Usage = """
    python mg_auth_server.py [-p <port>] [-c <config.yaml>]
    """

    opts, args = getopt.getopt(sys.argv[1:], "c:p:")
    opts = dict(opts)
    config_file = opts.get("-c", os.environ.get("AUTH_SERVER_CFG"))
    if not config_file:
        print("Configuration file must be provided either using -c command line option or via AUTH_SERVER_CFG environment variable")
        sys.exit(1)
    config = yaml.load(open(config_file, "r"), Loader=yaml.SafeLoader)
    app = create_application(config)
    port = int(opts.get("-p", config.get("port", -1)))
    if port == -1:
        print("Port number is not configured")
        sys.exit(1)

    ssl_args = {}
    if "ssl" in config:
        ssl_args = dict(
            ssl_keyfile = config["ssl"]["key"],
            ssl_certfile = config["ssl"]["cert"],
            ssl_ca_certs = config["ssl"]["ca_bundle"],
            ssl_cert_reqs = ssl.CERT_OPTIONAL
        )
        
    print("staring on port", port)
    uvicorn.run(app, host="0.0.0.0", port=port, **ssl_args)
else:
    # FastAPI (ASGI) application, e.g. for uvicorn
    app = create_application()
    # WSGI application, e.g. for mod_wsgi or uWSGI
    application = ASGIMiddleware(app)
