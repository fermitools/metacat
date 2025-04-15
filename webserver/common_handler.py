from metacat.auth.server import BaseHandler
from metacat.db import DBUser, DBNamespace, DBRole
import re, json
from metacat.logs import Logged, Logger, init
import traceback

_StatusReasons = {
    # Status Codes
    # Informational
    100: 'Continue',
    101: 'Switching Protocols',
    102: 'Processing',

    # Successful
    200: 'OK',
    201: 'Created',
    202: 'Accepted',
    203: 'Non-Authoritative Information',
    204: 'No Content',
    205: 'Reset Content',
    206: 'Partial Content',
    207: 'Multi Status',
    226: 'IM Used',

    # Redirection
    300: 'Multiple Choices',
    301: 'Moved Permanently',
    302: 'Found',
    303: 'See Other',
    304: 'Not Modified',
    305: 'Use Proxy',
    307: 'Temporary Redirect',
    308: 'Permanent Redirect',

    # Client Error
    400: 'Bad Request',
    401: 'Unauthorized',
    402: 'Payment Required',
    403: 'Forbidden',
    404: 'Not Found',
    405: 'Method Not Allowed',
    406: 'Not Acceptable',
    407: 'Proxy Authentication Required',
    408: 'Request Timeout',
    409: 'Conflict',
    410: 'Gone',
    411: 'Length Required',
    412: 'Precondition Failed',
    413: 'Request Entity Too Large',
    414: 'Request URI Too Long',
    415: 'Unsupported Media Type',
    416: 'Requested Range Not Satisfiable',
    417: 'Expectation Failed',
    418: "I'm a teapot",
    422: 'Unprocessable Entity',
    423: 'Locked',
    424: 'Failed Dependency',
    426: 'Upgrade Required',
    428: 'Precondition Required',
    429: 'Too Many Requests',
    451: 'Unavailable for Legal Reasons',
    431: 'Request Header Fields Too Large',

    # Server Error
    500: 'Internal Server Error',
    501: 'Not Implemented',
    502: 'Bad Gateway',
    503: 'Service Unavailable',
    504: 'Gateway Timeout',
    505: 'HTTP Version Not Supported',
    507: 'Insufficient Storage',
    510: 'Not Extended',
    511: 'Network Authentication Required',
}



class SanitizeException(Exception):
    pass
    
def _error_response(code, message, reason=None, type="json"):
    reason = reason or _StatusReasons.get(code, "unknown")
    if type == "json":
        text = json.dumps({ "message": message, "code":"%s %s" % (code, reason), "title":reason })
        content_type = "application/json"
    else:
        text = message
        content_type = "text/plain"
    return code, text, content_type

    
def sanitized(method):
    def decorated(self, *params, **agrs):
        try:    out = method(self, *params, **agrs)
        except SanitizeException as e:
            out = _error_response(400, str(e))
        except Exception as e:
            self.log("Uncaught exception:" + str(e))
            self.log(traceback.format_exc())
            raise
        return out
    return decorated

class MetaCatHandler(BaseHandler, Logged):
    
    def __init__(self, *params, **args):
        BaseHandler.__init__(self, *params, **args)
        self.NamespaceAuthorizations = {}
        Logged.__init__(self, debug=True, logger=self.App.Logger)
        
    SafePatterns = {
        "any": None,        # no-op
        "safe": re.compile(r"[^']+", re.I),
        "aname": re.compile(r"[a-z][a-z0-9_.-]*", re.I),
        "fname": re.compile(r"[a-z0-9_./-]+", re.I)
    }

    def sanitize(self, *words, allow="fname", **kw):
        pattern = self.SafePatterns[allow]
        if pattern is not None:
            for w in words:
                if w and not pattern.fullmatch(w):
                    raise SanitizeException("Invalid value: %s" % (w,))
            for name, value in kw.items():
                if value and not pattern.fullmatch(value):
                    raise SanitizeException("Invalid value for %s: %s" % (name, value))

    def authenticated_user(self):
        username, error = self.authenticated_username()
        if username is None:
            return None, error
        user = DBUser.get(self.App.connect(), username)
        if user is not None:
            return user, None
        else:
            return None, "user not found"

    def _namespace_authorized(self, db, namespace, user):
        authorized = self.NamespaceAuthorizations.get(namespace)
        if authorized is None:
            ns = DBNamespace.get(db, namespace)
            if ns is None:
                raise KeyError("Namespace %s does not exist" % (namespace) )
            authorized = ns.owned_by_user(user) or user.is_admin() 
            self.NamespaceAuthorizations[namespace] = authorized
        return authorized

    def _handle_request(self, request, path, path_down, args):
        response = super()._handle_request(request, path, path_down, args)
        if isinstance(response, tuple):
            status = response[0]
        try:
            if isinstance(status, int) and status != 200:
                clientaddr = request.client_addr
                if clientaddr is not None:
                    self.log("%s %s %s %s %s" % (clientaddr, response[0], response[1], path, args), who="metacat", channel="error")
                else:
                    self.log("%s %s %s %s" % (response[0], response[1], path, args), who="metacat", channel="error")
        except UnboundLocalError:
            pass
        return response

    def error_response(self, code, message, reason=None, type="json"):
        reason = reason or _StatusReasons.get(code, "unknown")
        if type == "json":
            text = json.dumps({ "message": message, "code":"%s %s" % (code, reason), "title":reason })
            content_type = "application/json"
        else:
            text = message
            content_type = "text/plain"
        self.log('%s' % message)
        return code, text, content_type

    def namespace_create_common(self, current_user, name, owner_role, description):
        """ Namespace creation code that was going to be repeated in 
            both the gui an data handlers"""

        nsrules = self.App.Cfg.get("namespace_rules", [])
        default_owner_user = current_user.Username
        db = self.App.connect()

        if nsrules:
            allowed = False
            for rule in nsrules:
                cr_regex = re.compile(rule["regex"])
                if cr_regex.match(name):
                    #self.log( f"namespace create: matched rule: {repr(rule)}")
                    rule_user = cr_regex.sub(rule["owner"], name,1)
                    rule_roles = list(cr_regex.sub(rule["allowed_creator"],name,1).split(","))

                    #self.log( f"namespace create: rule_roles: {repr(rule_roles)}")
                    #self.log( f"namespace create: rule_user: {repr(rule_user)}")
                    if current_user.is_admin() or current_user.Username in rule_roles or '*' in rule_roles:
                        #self.log( f"username match")
                        allowed = True

                    for role in rule_roles:
                        r = DBRole.get(db, role)
                        if r and current_user.Username in r.members:
                             #self.log( f"role member match")
                             allowed = True
                    break
            if allowed:
                if rule_user != '*':
                    default_owner_user = rule_user
            else:
                return 403, None
        else:
            if owner_role:
                r = DBRole.get(db, owner_role)
                if not current_user.is_admin() and not current_user.Username in r.members:
                    return 403, None 

        if owner_role is None:
            owner_user = default_owner_user
       
        if DBNamespace.exists(db, name):
            return  409, None

        if description:
            description = unquote_plus(description)
            
        ns = DBNamespace(db, name, owner_user=owner_user, owner_role = owner_role, description=description)
        ns.Creator = current_user.Username
        ns.create()

        return 200, ns



