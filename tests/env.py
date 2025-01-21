import os
import sys
import time
import pytest

production=os.environ.get("METACAT_TEST_PRODUCTION", False)

if not production:
    base = os.path.dirname(os.path.dirname(__file__))
    os.environ["PATH"] = f"{base}/metacat/ui:{os.environ['PATH']}"
    os.environ["PYTHONPATH"] = f"{base}:{base}/tests/mocks:{os.environ.get('PYTHONPATH','')}"
    sys.path.insert(0,base)
    sys.path.insert(0,f"{base}/tests/mocks")

@pytest.fixture
def env():
    #if production:
    if True:
       hostaport = 'https://metacat.fnal.gov:8143'
       hostport = 'https://metacat.fnal.gov:9443'
    else:
       hostaport = 'https://metacat.fnal.gov:8143'
       hostport = 'http://fermicloud761.fnal.gov:9094'


    os.environ['METACAT_AUTH_SERVER_URL'] = f'{hostaport}/auth/hypot_dev'
    os.environ['DATA_DISPATCHER_URL'] = f'{hostport}/hypot_dd/data'
    os.environ['METACAT_SERVER_URL'] = f'{hostport}/hypot_meta_dev/app'
    os.environ['DATA_DISPATCHER_AUTH_URL'] = f'{hostaport}/auth/hypot_dev'
    os.environ['BEARER_TOKEN_FILE'] = '/tmp/bt_mc_test%d' % os.getpid()
    print("METACAT_SERVER_URL=", os.environ["METACAT_SERVER_URL"])

@pytest.fixture
def token(env):
    os.system("htgettoken -i hypot -a htvaultprod.fnal.gov ")
    
@pytest.fixture
def auth(token):
    os.system("metacat auth login -m token $USER")
    
start_ds = None
if start_ds is None:
    start_ds = time.time()


@pytest.fixture
def tst_ds():
    ds = start_ds
    return f"{os.environ['USER']}:tst{ds}"


@pytest.fixture
def tst_file_md_list():
    ds = start_ds
    mdl = []
    for i in range(15):
        fname = f"tst_{ds}_{i}.txt"
        fcont = f"data file {fname}\n"
        with open(fname, "w") as fo:
            fo.write(fcont)
        with os.popen(f"xrdadler32 {fname}", "r") as fi:
            hash_n_name = fi.read()
        fhash, _ = hash_n_name.split(" ")
        mdl.append(
            {
                "name": fname,
                "namespace": os.environ["USER"],
                "size": len(fcont),
                "checksums": {"adler32": fhash},
                "metadata": {"f.ds": ds},
            }
        )
    yield (mdl)
    for md in mdl:
        try:
            os.unlink(md["name"])
        except:
            pass
