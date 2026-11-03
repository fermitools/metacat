import yaml, os, getopt, sys

from fastapi import FastAPI, APIRouter, Request
from fastapi.responses import Response, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi_utils.cbv import cbv
from a2wsgi import ASGIMiddleware
from pythreader import schedule_task, Primitive, synchronized
from metacat.db import DBUser, DBRole, DBDataset
from metacat.filters import standard_filters

from datetime import datetime, timezone

import json, time, secrets, traceback, hashlib, pprint
from urllib.parse import quote_plus, unquote_plus

from metacat.util import to_str, to_bytes
from metacat.common import SignedToken, SignedTokenExpiredError, SignedTokenImmatureError, SignedTokenUnacceptedAlgorithmError, SignedTokenSignatureVerificationError
from metacat.logs import Logged, Logger

from metacat import Version
from wsdbtools import ConnectionPool

from common_handler import SanitizeException, sanitize_exception_handler
from gui_handler import gui_router, gui_categories_router
from data_handler import data_router
from metacat.auth.server import BaseApp, BaseHandler, auth_router, gui_auth_router, install_exception_handlers

root_router = APIRouter()

@cbv(root_router)
class RootHandler(BaseHandler):

    @root_router.api_route("/", methods=["GET", "POST"])
    def index(self):
        return RedirectResponse("./gui/index", status_code=302)
        
    @root_router.api_route("/version", methods=["GET", "POST"])
    def version(self):
        return HTMLResponse(Version)

    @root_router.api_route("/probe", methods=["GET", "POST"])
    def probe(self):
        try:    db = self.App.connect()
        except Exception as e:
            return HTMLResponse(str(e), status_code=500)
        c = db.cursor()
        c.execute("select 1")
        if c.fetchone()[0] == 1:
            return HTMLResponse("OK")
        return Response(status_code=500)

class CachedStaticFiles(StaticFiles):
    # static files with Cache-Control, same as webpie WPStaticHandler(cache_ttl=...)

    def __init__(self, *params, cache_ttl=None, **args):
        StaticFiles.__init__(self, *params, **args)
        self.CacheTTL = cache_ttl

    def file_response(self, *params, **args):
        response = StaticFiles.file_response(self, *params, **args)
        if self.CacheTTL is not None:
            response.headers["Cache-Control"] = f"max-age={self.CacheTTL}"
        return response


def as_dt_utc(t):
    # datetim in UTC
    if t is None:
        return ""
    if isinstance(t, (int, float)):
        t = datetime.utcfromtimestamp(t)
    return t.strftime("%Y-%m-%d %H:%M:%S")

def as_dt_local(t):
    # datetim in UTC
    if t is None:
        return ""
    if isinstance(t, (int, float)):
        t = datetime.utcfromtimestamp(t)
    return t.strftime("%Y-%m-%d %H:%M:%S")
    
def as_json(x):
    return json.dumps(x)

def none_as_blank(x):
    if x is None:
        return ""
    else:
        return str(x)

def angle_brackets(text):
    if text is None:    return None
    return text.replace("<", "&lt;").replace(">", "&gt;")


class App(BaseApp, Primitive):

    Version = Version

    def __init__(self, cfg, static_location="./static", prefix=None):
        BaseApp.__init__(self, cfg, prefix=prefix)
        Primitive.__init__(self, name="MetaCat app")
        self.Title = cfg.get("site_title", "DEMO Metadata Catalog")
        
        self.StaticLocation = static_location

        config_name = os.environ.get("METACAT_SERVER_CFG")
        instance_name = config_name.split("/")[-1].split(".")[0]
        log_file = "logs/%s.log" % instance_name
        self.Logger = Logger(log_file)

        self.StandardFilters = {}
        self.CustomFilters = {}

        filters_config = self.Cfg.get("filters", {})
        if filters_config.get("standard_filters", True):
            self.StandardFilters.update(standard_filters)

        for mod_spec in filters_config.get("modules", []):
            name = mod_spec["name"]
            env = mod_spec.get("env")
            filter_cfg = mod_spec.get("config")
            filters_from_module = self.load_filters_module(name, env, filter_cfg)
            self.CustomFilters.update(filters_from_module)

        self.Filters = {}
        self.Filters.update(self.StandardFilters)
        self.Filters.update(self.CustomFilters)
        self.init_auth_core(cfg)
        self.Realm = self.AuthCore.Realm

    def load_filters_module(self, module_name, env, config):
        from importlib import import_module
        saved_environ = None
        if env:
            saved_environ = os.environ.copy()
            os.environ.update(env)
        filters = {}
        try:
            mod = import_module(module_name)
            filters = mod.create_filters(config)
        except ImportError as e:
            self.Logger.error("Failed to import filters module %s with error: %s" % (module_name, e), who="metacat")
        except AttributeError as e:
            self.Logger.error("Failed to create filter %s (missing create_filters method) with error: %s" % (module_name, e), who="metacat")
        except Exception as e:
            self.Logger.error("Failed filter %s with error: %s" % (module_name, e), who="metacat")
        finally:
            if saved_environ:
                for k in env.keys():
                    if k in saved_environ:
                        os.environ[k] = saved_environ[k]
                    else:
                        del os.environ[k]
        return filters

    def update_file_counts(self):
        db = self.connect()
        for dataset in DBDataset.list(db):
            self.DatasetCounts[(dataset.Namespace, dataset.Name)] = self.get_dataset_counts(dataset)

    @synchronized
    def ______dataset_file_counts(self, namespace, name):
        counts = self.DatasetCounts.get((namespace, name))
        if counts is None:
            db = self.connect()
            dataset = DBDataset.get(db, namespace, name)
            counts = self.DatasetCounts[(namespace, name)] = self.get_dataset_counts(dataset)
        return counts

    def filters(self):
        return self.Filters
        
    def init(self):
        tempdir = os.environ.get("METACAT_SERVER_TEMPLATES_DIR", "server")
        self.initJinjaEnvironment(
            filters={"as_dt_utc":as_dt_utc,
                "as_dt_local":as_dt_local,
                "none_as_blank":none_as_blank,
                "angle_brackets":angle_brackets,
                "json": as_json
            },
            tempdirs=[tempdir, tempdir + "/templates"],
            globals={
                "G_Version": Version, 
                "G_SiteTitle": self.Title
            }
        )

    def render_to_string(self, template, **args):
        # G_StaticRoot depends on the external application root path, which is known per request
        args.setdefault("G_StaticRoot", args.get("GLOBAL_AppRootPath", "") + "/static")
        return BaseApp.render_to_string(self, template, **args)


def create_application(config=None, prefix=None):
    config = config or os.environ.get("METACAT_SERVER_CFG")
    if config is None:
        print("Configuration file must be provided using METACAT_SERVER_CFG environment variable")
        return None
    elif isinstance(config, str):
        os.environ["METACAT_SERVER_CFG"] = config
    if isinstance(config, str):
        config = yaml.load(open(config, "r"), Loader=yaml.SafeLoader)  
    prefix = (prefix or config.get("prefix") or "").rstrip("/")
    static_location = config.get("static_location", os.environ.get("METACAT_SERVER_STATIC_DIR", "static"))
    #print("static_location:", static_location)
    app = App(config, static_location=static_location, prefix=prefix)
    app.init()

    fastapi_app = FastAPI(title="MetaCat", version=Version, docs_url=None, redoc_url=None, openapi_url=None)
    fastapi_app.state.metacat_app = app
    install_exception_handlers(fastapi_app)
    fastapi_app.add_exception_handler(SanitizeException, sanitize_exception_handler)

    @fastapi_app.middleware("http")
    async def log_errors(request: Request, call_next):
        response = await call_next(request)
        if response.status_code >= 400:
            clientaddr = request.client.host if request.client is not None else None
            msg = "%s %s %s" % (response.status_code, request.url.path, request.url.query)
            if clientaddr is not None:
                msg = clientaddr + " " + msg
            app.Logger.log(msg, who="metacat", channel="error")
        return response

    fastapi_app.include_router(root_router, prefix=prefix)
    fastapi_app.include_router(data_router, prefix=prefix)
    fastapi_app.include_router(gui_categories_router, prefix=prefix)
    fastapi_app.include_router(gui_router, prefix=prefix)
    fastapi_app.include_router(gui_auth_router, prefix=prefix + "/auth")
    fastapi_app.include_router(auth_router, prefix=prefix + "/auth")
    fastapi_app.mount(prefix + "/static", CachedStaticFiles(directory=static_location, cache_ttl=3600), name="static")
    return fastapi_app


# FastAPI (ASGI) application, e.g. for uvicorn
app = create_application()

# WSGI application, e.g. for mod_wsgi or uWSGI
application = ASGIMiddleware(app) if app is not None else None


if __name__ == "__main__":
    # running from shell
    import uvicorn

    opts, args = getopt.getopt(sys.argv[1:], "c:p:")
    opts = dict(opts)

    config_file = opts.get("-c") or os.environ.get("METACAT_SERVER_CFG")
    if config_file is None:
        print("Config file must be specified with -c or METACAT_SERVER_CFG")
        sys.exit(1)
    os.environ["METACAT_SERVER_CFG"] = config_file
    config = yaml.load(open(config_file, "r"), Loader=yaml.SafeLoader)
    #print("main: config:", config_file)
    port = int(opts.get("-p", config.get("port", 8080)))
    prefix = config.get("prefix")
    print(f"Starting the server on port {port} ...")   
    uvicorn.run(create_application(config, prefix), host="0.0.0.0", port=port, log_level="info")
