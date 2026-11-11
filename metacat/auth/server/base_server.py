# common functionality for Auth, GUI and Data servers

from fastapi import FastAPI, Request, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response, HTMLResponse, PlainTextResponse, RedirectResponse
from starlette.datastructures import FormData
from starlette.exceptions import HTTPException as StarletteHTTPException
from wsdbtools import ConnectionPool
from metacat.util import to_str, to_bytes
from metacat.auth import BaseDBUser, AuthenticationCore
from metacat.common import (
    SignedToken, SignedTokenExpiredError, SignedTokenImmatureError, SignedTokenUnacceptedAlgorithmError,
    SignedTokenSignatureVerificationError
)
import psycopg2, json, time, secrets, traceback, hashlib, pprint, os, yaml, functools, logging
from html import escape
from typing import Optional
from urllib.parse import quote, quote_plus, unquote_plus

logger = logging.getLogger("metacat.server")

#
# Request helpers
#

def request_environ(request: Request) -> dict:
    """WSGI-style environment for the request.

    When running under a WSGI server through a2wsgi, this is the original WSGI environ, which carries
    the SSL_CLIENT_* variables set by mod_ssl. Under a native ASGI server, an equivalent is built from the scope.
    """
    environ = request.scope.get("wsgi_environ")
    if environ is not None:
        return environ
    scheme = request.url.scheme
    environ = {
        "REQUEST_METHOD":   request.method,
        "REQUEST_SCHEME":   scheme,
        "wsgi.url_scheme":  scheme,
        "SCRIPT_NAME":      request.scope.get("root_path", ""),
        "PATH_INFO":        request.url.path,
        "QUERY_STRING":     request.url.query,
    }
    if scheme == "https":
        environ["HTTPS"] = "on"
    if request.client is not None:
        environ["REMOTE_ADDR"] = request.client.host
        environ["REMOTE_PORT"] = str(request.client.port)
    for name, value in request.headers.items():
        key = name.upper().replace("-", "_")
        if key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            environ[key] = value
        else:
            environ["HTTP_" + key] = value
    return environ

async def request_body(request: Request) -> bytes:
    return await request.body()

async def request_form(request: Request) -> FormData:
    # parsed POST form. Empty for requests without form-encoded payload, like webob request.POST
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/x-www-form-urlencoded") or content_type.startswith("multipart/form-data"):
        await request.body()            # cache the body so that it remains available after the form is parsed
        return await request.form()
    return FormData()

def path_group(request: Request) -> Optional[str]:
    # authentication group, for servers which mount the auth handler under /{group}
    return request.path_params.get("group")

class LegacyRequest(object):
    """Synchronous WSGI-style view of a request, used by AuthenticationCore"""

    def __init__(self, request: Request, body: bytes = b""):
        self.headers = request.headers
        self.cookies = request.cookies
        self.body = body
        self.environ = request_environ(request)
        self.method = request.method

#
# Exceptions and exception handlers
#

class HTTPRedirect(Exception):
    """Raised by BaseHandler.redirect() to abort the request processing and redirect the client"""

    def __init__(self, location, status_code=302):
        Exception.__init__(self, location)
        self.Location = location
        self.StatusCode = status_code

async def redirect_exception_handler(request: Request, exc: HTTPRedirect):
    return RedirectResponse(exc.Location, status_code=exc.StatusCode)

async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return PlainTextResponse(str(exc.detail), status_code=exc.status_code, headers=getattr(exc, "headers", None))

async def validation_exception_handler(request: Request, exc: RequestValidationError):
    messages = []
    for error in exc.errors():
        loc = ".".join(str(x) for x in error.get("loc", ()))
        messages.append(f"{loc}: {error.get('msg')}")
    return PlainTextResponse("Invalid request arguments:\n" + "\n".join(messages) + "\n", status_code=400)

async def application_error_handler(request: Request, exc: Exception):
    # same as webpie WPApp.applicationErrorResponse()
    exc_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    logger.error("Uncaught exception in %s %s:\n%s", request.method, request.url.path, exc_text)
    text = """<html><body><h2>Application error</h2>
            <h3>%s</h3>
            <pre>\n%s</pre>
            </body>
            </html>""" % (escape(str(exc)), escape(exc_text))
    return HTMLResponse(text, status_code=500)

def install_exception_handlers(app: FastAPI):
    app.add_exception_handler(HTTPRedirect, redirect_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, application_error_handler)

#
# Application and handler base classes
#

class BaseApp(object):

    TokenExpiration = 24*3600*7

    def __init__(self, cfg, prefix=None):
        self.Cfg = cfg
        self.Prefix = (prefix or "").rstrip("/")
        self.JEnv = None
        self.JGlobals = {}

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

    def initJinjaEnvironment(self, tempdirs=[], filters={}, globals={}):
        from jinja2 import Environment, FileSystemLoader
        if not isinstance(tempdirs, list):
            tempdirs = [tempdirs]
        self.JEnv = Environment(loader=FileSystemLoader(tempdirs))
        self.JEnv.filters.update(filters)
        self.JGlobals = dict(globals)

    def render_to_string(self, template, **args):
        params = dict(self.JGlobals)
        params.update(args)
        return self.JEnv.get_template(template).render(params)

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

    def user_from_request(self, request):
        return self.AuthCore.user_from_request(request)

    def app_root_path(self, request: Request):
        # external URL path of the application root, without trailing slash
        return (request.scope.get("root_path", "") + self.Prefix).rstrip("/")

class BaseHandler(object):
    """Base for the class based views. The attributes annotated here are injected by fastapi_utils.cbv"""

    request: Request
    body: bytes = Depends(request_body)
    form: FormData = Depends(request_form)
    group: Optional[str] = Depends(path_group)

    def __init__(self):
        self.App = self.request.app.state.metacat_app
        self.AuthCore = self.App.auth_core(self.group)
        if self.AuthCore is None:
            raise StarletteHTTPException(404, "invalid path: " + self.request.url.path)
        self.Request = LegacyRequest(self.request, self.body)

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

    def scriptUri(self):
        # equivalent of the WSGI SCRIPT_NAME
        return self.request.scope.get("root_path", "")

    def appRootPath(self):
        return self.App.app_root_path(self.request)

    def jinja_globals(self):
        return {"G_User":self.authenticated_user()[0]}

    def render_to_string(self, template, **args):
        top = self.appRootPath()
        params = {
            "GLOBAL_AppRootPath":   top,
            "GLOBAL_AppTopPath":    top
        }
        params.update(self.jinja_globals())
        params.update(args)
        return self.App.render_to_string(template, **params)

    def render_to_response(self, template, **args):
        return HTMLResponse(self.render_to_string(template, **args))

    def redirect(self, location, status_code=302):
        raise HTTPRedirect(location, status_code)

    def messages(self):
        args = self.request.query_params
        return {k: unquote_plus(args.get(k,"")) for k in ("error", "message")}

def sanitize(exclude=[], only=None, unsafe=r"<'>\|"):
    """Endpoint decorator rejecting requests with unsafe characters in the query arguments or the relpath.
    Replacement for webpie.sanitize()
    """
    if isinstance(exclude, str):
        exclude = [exclude]
    if isinstance(only, str):
        only = [only]

    def check(name, value):
        if isinstance(value, str) and any(c in value for c in unsafe):
            raise StarletteHTTPException(400, "Invalid value for " + quote(name))

    def decorator(method):
        @functools.wraps(method)
        def decorated(self, *params, **args):
            if "(relpath)" not in exclude:
                check("(relpath)", args.get("relpath"))
            for name, value in self.request.query_params.multi_items():
                if name not in exclude and (only is None or name in only):
                    check(name, value)
            return method(self, *params, **args)
        return decorated
    return decorator
