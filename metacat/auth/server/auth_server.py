from metacat.auth.server import BaseApp, auth_router, install_exception_handlers

import time, os, yaml, sys
from urllib.parse import quote_plus, unquote_plus
from fastapi import FastAPI
from a2wsgi import ASGIMiddleware

class AuthApp(BaseApp):
    pass

def create_application(config_path=None):
    config_path = config_path or os.environ.get("AUTH_SERVER_CFG")
    if not config_path:
        print("Config file is not defined. Use AUTH_SERVER_CFG environment variable")
        return None
    config = yaml.load(open(config_path, "r"), Loader=yaml.SafeLoader)
    app = AuthApp(config)
    templdir = config.get("templates", "")
    if templdir.startswith("$"):
        templdir = os.environ[templdir[1:]]
    app.initJinjaEnvironment(tempdirs=[templdir, "."])

    fastapi_app = FastAPI(title="MetaCat Authentication Server", docs_url=None, redoc_url=None, openapi_url=None)
    fastapi_app.state.metacat_app = app
    install_exception_handlers(fastapi_app)
    fastapi_app.include_router(auth_router)
    return fastapi_app

if __name__ == "__main__":
    import uvicorn, getopt, ssl

    Usage = """
    python auth_server.py [-p <port>] [-c <config.yaml>]
    """

    opts, args = getopt.getopt(sys.argv[1:], "c:p:")
    opts = dict(opts)
    config_file = opts.get("-c", os.environ.get("AUTH_SERVER_CFG"))
    if not config_file:
        print("Configuration file must be provided either using -c command line option or via AUTH_SERVER_CFG environment variable")
        sys.exit(1)

    config = yaml.load(open(config_file, "r"), Loader=yaml.SafeLoader)
    auth_config = config["authentication"]
    port = int(opts.get("-p", auth_config.get("port", -1)))
    if port == -1:
        print("AuthServer port is not configured")
        sys.exit(1)

    ssl_args = {}
    if "ssl" in auth_config:
        ssl_args = dict(
            ssl_keyfile = auth_config["ssl"]["key"],
            ssl_certfile = auth_config["ssl"]["cert"],
            ssl_ca_certs = auth_config["ssl"]["ca_bundle"],
            ssl_cert_reqs = ssl.CERT_OPTIONAL
        )

    app = create_application(config_file)

    print(f"Starting server on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, **ssl_args)
else:
    # FastAPI (ASGI) application, e.g. for uvicorn
    app = create_application()
    # WSGI application, e.g. for mod_wsgi or uWSGI
    application = ASGIMiddleware(app) if app is not None else None
