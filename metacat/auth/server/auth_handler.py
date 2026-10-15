from metacat.common import SignedToken
from .base_server import BaseHandler
from metacat.auth import BaseDBUser as DBUser
from metacat.util import to_str, to_bytes

import time, os, yaml, json, traceback
from datetime import datetime
from typing import Optional
from urllib.parse import quote_plus, unquote_plus
from fastapi import APIRouter
from fastapi.responses import Response, PlainTextResponse
from fastapi_utils.cbv import cbv

# Routes are relative to where the routers are included into the application:
#   metacat server:             /auth
#   auth server:                /
#   multi-group auth server:    /{group} and /auth/{group}

auth_router = APIRouter()

@cbv(auth_router)
class AuthHandler(BaseHandler):

    @auth_router.api_route("/whoami", methods=["GET", "POST"])
    def whoami(self):
        user, error = self.AuthCore.user_from_request(self.Request)
        return PlainTextResponse(user or "")

    @auth_router.api_route("/mydn", methods=["GET", "POST"])
    @auth_router.api_route("/mydn/{relpath:path}", methods=["GET", "POST"])
    def mydn(self, relpath: str = ""):
        environ = self.Request.environ
        ssl = environ.get("HTTPS") == "on" or environ.get("REQUEST_SCHEME") == "https"
        if not ssl:
            return Response("Use HTTPS connection\n", status_code=400, media_type="text/html")
        if relpath == "issuer":
            return PlainTextResponse(environ.get("SSL_CLIENT_I_DN","") + "\n")
        elif relpath == "subject":
            return PlainTextResponse(environ.get("SSL_CLIENT_S_DN","") + "\n")
        else:
            return Response(json.dumps({
                "subject":  environ.get("SSL_CLIENT_S_DN",""),
                "issuer":  environ.get("SSL_CLIENT_I_DN","")
            }) + "\n", media_type="text/json")

    @auth_router.api_route("/verify", methods=["GET", "POST"])
    def verify(self):
        username, error = self.AuthCore.user_from_request(self.Request)
        if username:
            return PlainTextResponse("OK")
        else:
            return Response(error, status_code=401, media_type="text/html")

    @auth_router.api_route("/auth", methods=["GET", "POST"])
    def auth(self, redirect: Optional[str] = None, method: str = "password", username: Optional[str] = None):
        status, extra = self.AuthCore.authenticate(method, username, self.Request, redirect)
        #print("AuthHandler.auth:", method, status, extra)
        if status == "continue":
            http_status, text, headers = extra
            return Response(text, status_code=http_status, headers=headers, media_type="text/html")
        elif status == "reject":
            return Response((extra or "Authentication failed") + "\n", status_code=401, media_type="text/html")
        elif status != "ok":
            return Response("Unknown authentication status\n", status_code=401, media_type="text/html")

        # status == "ok":
        username = extra.get("username", username)
        if not username:
            return Response("Authentication failed: unknown username\n", status_code=401, media_type="text/html")

        token, encoded = self.AuthCore.generate_token(username, expiration=extra.get("expiration"))
        headers = {"X-Authentication-Token": to_str(encoded)}
        http_status = 200
        if redirect:
            headers["Location"] = redirect
            http_status = 302
        return Response("", status_code=http_status, headers=headers, media_type="text/html")

    # draft of adding probe check for monitoring
    @auth_router.api_route("/probe", methods=["GET", "POST"])
    def probe(self):
        try:    db = self.AuthCore.user_db()
        except Exception as e:
            return Response(str(e), status_code=500, media_type="text/html")
        c = db.cursor()
        c.execute("select 1")
        if c.fetchone()[0] == 1:
            return Response("OK", media_type="text/html")
        return Response(status_code=500)


gui_auth_router = APIRouter()

@cbv(gui_auth_router)
class GUIAuthHandler(AuthHandler):

    @gui_auth_router.api_route("/logout", methods=["GET", "POST"])
    def logout(self, redirect: Optional[str] = None):
        if redirect:
            resp = Response(status_code=302, headers={"Location": redirect})
        else:
            resp = PlainTextResponse("")
        resp.set_cookie("auth_token", "-", max_age=1)
        return resp

    @gui_auth_router.api_route("/login", methods=["GET", "POST"])
    def login(self, redirect: Optional[str] = None):
        if redirect: redirect = unquote_plus(redirect)
        return self.render_to_response("login.html", redirect=redirect, **self.messages())

    @gui_auth_router.api_route("/logged_in", methods=["GET", "POST"])
    def logged_in(self):
        token, error = self.AuthCore.token_from_request(self.Request)
        if token is None:
            self.redirect("./login?error=%s" % (quote_plus(error or "Not logged in"),))
        encoded = to_str(token.encode())
        exp = datetime.utcfromtimestamp(token.expiration)
        return self.render_to_response("show_token.html", token=token, expiration=exp, encoded=encoded)

    @gui_auth_router.api_route("/do_login", methods=["GET", "POST"])
    def do_login(self):
        # handle SciTokens here !!!
        form = self.form
        username = form.get("username")
        hashed_password = form.get("hashed_password")
        password = form.get("password")
        token_text = form.get("token")
        redirect = form.get("redirect")
        relogin_url = "./login"
        if redirect:
            relogin_url += "?redirect=%s&" % (quote_plus(redirect),)
        else:
            relogin_url += "?"
        token = None

        db = self.App.user_db()
        if token_text:
            token, error = self.AuthCore.verify_token(token_text)
            subject = token and token.subject
            if not subject:
                self.redirect("%serror=%s" % (relogin_url, quote_plus(error)))
            username = subject

        if not username:
            self.redirect("%serror=%s" % (relogin_url, quote_plus("Need username or token")))

        u = DBUser.get(db, username)
        if not u:
            #print("authentication error")
            self.redirect("%serror=User+%s+not+found" % (relogin_url, username))

        if not token:
            if (password or hashed_password) and username:
                #print("GUIAuthHandler: password:", password,"  hashed_password:", hashed_password)
                ok, reason, expiration = u.authenticate("password", self.App.Realm, hashed_password or password)
                #print("GUIAuthHandler: ok, reason:", ok, reason)
                if not ok and password:
                    ok, _, expiration = u.authenticate("ldap", self.AuthCore.auth_config("ldap"), password)
                if not ok:
                    self.redirect("%serror=%s" % (relogin_url, quote_plus("Authentication error")))
            else:
                self.redirect(relogin_url)

        if token is not None:
            expiration = token.expiration

        if expiration is None:
            expiration = self.AuthCore.TokenExpiration + time.time()

        if token is not None:
            encoded = token.encode()
        else:
            token, encoded = self.AuthCore.generate_token(username, expiration=expiration)

        if redirect:
            resp = Response(status_code=302, headers={"Location": redirect})
        else:
            resp = PlainTextResponse("")
        #print ("response:", resp, "  reditrect=", redirect)
        resp.headers["X-Authentication-Token"] = to_str(encoded)
        resp.set_cookie("auth_token", to_str(encoded), max_age = max(0, int(expiration - time.time())))
        #print("GUIAuthHandler.do_login: returning", resp)
        return resp
