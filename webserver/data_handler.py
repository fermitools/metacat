from webpie import WPApp, WPHandler, Response, WPStaticHandler
import psycopg2, json, time, secrets, traceback, hashlib, pprint, uuid, random
import re
from metacat.db import DBFile, DBDataset, DBFileSet, DBNamedQuery, DBUser, DBNamespace, DBRole, \
    DBParamCategory, parse_name, AlreadyExistsError, IntegrityError, MetaValidationError
from wsdbtools import ConnectionPool
from urllib.parse import quote_plus, unquote_plus
from metacat.util import to_str, to_bytes, ObjectSpec
from metacat.mql import MQLQuery, MQLSyntaxError, MQLExecutionError, MQLCompilationError, MQLError
from metacat import Version
from datetime import datetime, timezone

from common_handler import MetaCatHandler, sanitized

METADATA_ERROR_CODE = 488

def parse_name(name, default_namespace=None):
    words = (name or "").split(":", 1)
    if len(words) < 2:
        assert not not default_namespace, "Null default namespace"
        ns = default_namespace
        name = words[0]
    else:
        assert len(words) == 2, "Invalid namespace:name specification:" + name
        ns, name = words
    return ns, name


class DataHandler(MetaCatHandler):
    
    MAX_ERRORS = 100                # Truncate error list if it is too long
    
    def __init__(self, request, app):
        MetaCatHandler.__init__(self, request, app)
        self.Categories = None
        self.Datasets = {}            # {(ns,n)->DBDataset}
        # for validate_checksums, below...
        self.expected_len = {
           "adler32": 8,
           "sha256": 64,
           "md5": 32,
        }
        
    def load_categories(self):
        if self.Categories is None:
            db = self.App.connect()
            self.Categories = {c.Path:c for c in DBParamCategory.list(db)}
        return self.Categories
        
    def load_dataset(self, ns, n):
        ds = self.Datasets.get((ns, n))
        if ds is None:
            db = self.App.connect()
            ds = self.Datasets[(ns, n)] = DBDataset.get(db, ns, n)
        return ds

    def json_generator(self, lst, page_size=10000):
        from collections.abc import Iterable
        assert isinstance(lst, Iterable)
        yield "["
        first = True
        n = 0
        page = []
        for x in lst:
            j = json.dumps(x)
            if not first: j = ","+j
            page.append(j)
            if len(page) >= page_size:
                yield "".join(page)
                page = []
            first = False
        if page:
            yield "".join(page)
        yield "]"
        
    def json_chunks(self, lst, chunk=10000):
        #yield json.dumps(lst)
        return self.text_chunks(self.json_generator(lst), chunk)

    RS = '\x1E'
    LF = '\n'    

    def json_stream(self, iterable, chunk=100000):
        # iterable is an iterable, returning jsonable items, one item at a time
        return self.text_chunks(("%s%s%s" % (self.RS, json.dumps(item), self.LF) for item in iterable), chunk)

    def realm(self, request, relpath, **args):
        return self.App.Realm           # realm used for the digest password authentication

    def version(self, request, relpath, **args):
        return Version, "text/plain"
        
    def simulate_503(self, request, relpath, prob=0.5, **args):
        prob = float(prob)
        if prob > random.random():
            return 503, "try later", "text/plain"
        else:
            return "OK", "text/plain"

    @sanitized
    def namespaces(self, request, relpath, owner_user=None, owner_role=None, directly="no", **args):
        directly = directly == "yes"
        db = self.App.connect()
        if request.body:
            names = json.loads(request.body)
            for name in names:
                self.sanitize(namespace=name)
                
            lst = DBNamespace.get_many(db, names)
        else:
            lst = sorted(DBNamespace.list(db, owned_by_user=owner_user, owned_by_role=owner_role, directly=directly), 
                    key=lambda ns: ns.Name)
        return json.dumps([ns.to_jsonable() for ns in lst]), "application/json"

    @sanitized
    def namespace(self, request, relpath, name=None, **args):
        name = name or relpath
        self.sanitize(name=name)
        db = self.App.connect()
        ns = DBNamespace.get(db, name)
        if ns is None:
            return 404, "Not found", "text/plain"
        return json.dumps(ns.to_jsonable()), "application/json"
        
    @sanitized
    def create_namespace(self, request, relpath, name=None, owner_role=None, description=None, **args):
        
        self.sanitize(owner_role, name)
        
        user, error = self.authenticated_user()
        if user is None:
            return 401, error

        code, ns = self.namespace_create_common(user, name, owner_role, description)
        if not ns:
               return code, "Permission Denied" if code=="403" else "Namespace already exists"
        return json.dumps(ns.to_jsonable()), "application/json"
            
    @sanitized
    def namespace_counts(self, request, relpath, name=None, **args):
        self.sanitize(name, relpath)
        name = name or relpath
        db = self.App.connect()
        ns = DBNamespace.get(db, name)
        out = json.dumps(
            dict(
                nfiles = ns.file_count(),
                ndatasets = ns.dataset_count(),
                nqueries = ns.query_count()
            )
        )
        return out, {"Content-Type":"application/json",
            "Access-Control-Allow-Origin":"*"
        } 
        
    def datasets(self, request, relpath, with_file_counts="no", **args):
        with_file_counts = with_file_counts == "yes"        # whether exact number of files is to be returned
        #print("data_server.datasets: with_file_counts:", with_file_counts)
        db = self.App.connect()
        datasets = DBDataset.list(db)
        out = []
        for ds in datasets:
            dct = ds.to_jsonable()
            if with_file_counts:        # otherwise the file_count column will be used
                dct["file_count"] = ds.nfiles(exact=True)
            out.append(dct)
        return json.dumps(out), "application/json"
        
    @sanitized
    def dataset_files(self, request, relpath, dataset=None, with_metadata="no", include_retired_files="no", **args):
        with_metadata=with_metadata == "yes"
        namespace, name = (dataset or relpath).split(":", 1)
        self.sanitize(namespace, name)
        db = self.App.connect()
        dataset = DBDataset.get(db, namespace, name)
        if dataset is None:
            return 404, "Dataset not found"
        files = dataset.list_files(with_metadata=with_metadata, 
                        include_retired_files = include_retired_files == "yes")
        return self.json_stream((f.to_jsonable(with_metadata=with_metadata) for f in files)), "application/json-seq"
        
    @sanitized
    def dataset(self, request, relpath, dataset=None, exact_file_count="no", **args):
        db = self.App.connect()
        namespace, name = (dataset or relpath).split(":", 1)
        self.sanitize(namespace, name)
        dataset = DBDataset.get(db, namespace, name)
        if dataset is None:
            return 404, "Dataset not found"
        dct = dataset.to_jsonable()
        dct["file_count"] = dataset.nfiles(exact_file_count == "yes")
        return json.dumps(dct), "application/json"
            
    @sanitized
    def dataset_count(self, request, relpath, dataset=None, exact_file_count="yes", **args):
        namespace, name = dataset.split(":", 1)
        self.sanitize(namespace, name)
        db = self.App.connect()
        nfiles = DBDataset(db, namespace, name).nfiles(exact_file_count == "yes")
        return '{"file_count":%d}\n' % (nfiles,), {"Content-Type":"application/json",
            "Access-Control-Allow-Origin":"*"
        } 

    @sanitized
    def dataset_counts(self, request, relpath, dataset=None, exact_file_count="yes", **args):
        namespace, name = dataset.split(":", 1)
        self.sanitize(namespace, name)
        db = self.App.connect()
        ds = DBDataset(db, namespace, name)
        
        #nfiles = self.App.dataset_file_count(namespace, name)
        data = {
            "dataset":      namespace + ":" + name,
            "file_count":   ds.nfiles(exact_file_count == "yes"),
            "parent_count": ds.parent_count(),
            "child_count":  ds.child_count(),
            "superset_count":  ds.ancestor_count(),
            "subset_count":  ds.subset_count()
        }
        return json.dumps(data), {"Content-Type":"application/json",
            "Access-Control-Allow-Origin":"*"
        }

    @sanitized
    def ____dataset_counts(self, request, relpath, dataset=None, **args):
        namespace, name = dataset.split(":", 1)
        self.sanitize(namespace, name)
        return self.App.dataset_file_counts(namespace, name)

    @sanitized
    def create_dataset(self, request, relpath):
        db = self.App.connect()
        user, error = self.authenticated_user()
        if user is None:
            return 401, error
        if not request.body:
            return 400, "Dataset parameters are not specified"
        params = json.loads(request.body)
        namespace = params["namespace"]
        name = params["name"]
        self.sanitize(namespace=namespace, name=name)
        ns = DBNamespace.get(db, namespace)
        if ns is None:
            return 404, "Namespace not found"
        if not user.is_admin() and not ns.owned_by_user(user):
            return 403, "Permission denied"
        if DBDataset.get(db, namespace, name) is not None:
            return 409, "Already exists"

        creator = user.Username 

        files = None
        files_query = params.get("files_query")
        if files_query:
            query = MQLQuery.parse(files_query, default_namespace=namespace or None)
            if query.Type != "file":
                return 400, f"Invalid file query: {files_query}"
            files = query.run(db, filters=self.App.filters(), with_meta=True, with_provenance=False)

        metadata = params.get("metadata") or {}
        for k in metadata.keys():
            if '.' not in k:
                return 400, f"Metadata parameter without a category: {k}"

        dataset = DBDataset(db, namespace, name,
            frozen = params.get("frozen", False), monotonic = params.get("monotonic", False),
            creator = creator, description = params.get("description", ""),
            file_meta_requirements = params.get("file_meta_requirements"),
            metadata = metadata
        )
        
        with db.transaction() as transaction:
            dataset.create(transaction=transaction)

            if files is not None:
                dataset.FileCount = dataset.add_files(files, transaction=transaction)
                dataset.save(transaction=transaction)
        
        return dataset.to_json(), "application/json"
    
    @sanitized
    def update_dataset(self, request, relapth, dataset=None):
        if not dataset:
            return 400, "Dataset is not specified"
        try:    spec = ObjectSpec(dataset)
        except ValueError as e:
            return 400, str(e)
        namespace, name = spec.Namespace, spec.Name
        self.sanitize(namespace=namespace, name=name)

        user, error = self.authenticated_user()
        if user is None:
            return 403, "Authentication required"
        db = self.App.connect()

        ds = DBDataset.get(db, namespace, name)
        if ds is None:
            return 404, "Dataset not found"

        request_data = json.loads(request.body)

        if not user.is_admin() and not self._namespace_authorized(db, namespace, user):
            return 403, f"Permission to update dataset in namespace {namespace} denied", "text/plain"
        
        if "metadata" in request_data:
            meta = request_data["metadata"]
            mode = request_data.get("mode", "update")
            if mode == "update":
                ds.Metadata.update(meta)
            else:
                ds.Metadata = meta

            for name in ds.Metadata:
                if '.' not in name:
                    return 400, f"Metadata parameter without a category: {name}"

        if "monotonic" in request_data: 
            ds.Monotonic = request_data["monotonic"]
        if "frozen" in request_data: 
            ds.Frozen = request_data["frozen"]
        if "description" in request_data: 
            ds.Description = request_data["description"]
        
        ds.save(updated_by=user.Username)
        return json.dumps(ds.to_jsonable()), "application/json"

    @sanitized
    def add_child_dataset(self, request, relpath, parent=None, child=None, **args):
        if not parent or not child:
            return 400, "Parent or child dataset unspecified"
        user, error = self.authenticated_user()
        if user is None:
            return 401, error
        parent_namespace, parent_name = parent.split(":",1)
        self.sanitize(parent_namespace=parent_namespace, parent_name=parent_name)
        child_namespace, child_name = child.split(":",1)
        self.sanitize(child_namespace=child_namespace, child_name=child_name)
        db = self.App.connect()
        parent_ns = DBNamespace.get(db, parent_namespace)
        child_ns = DBNamespace.get(db, child_namespace)
        if not user.is_admin() and not parent_ns.owned_by_user(user):      # allow adding unowned datasets as subsets 
                                                                            # was: or not child_ns.owned_by_user(user)):
            return 403, "Permission denied"
        parent_ds = DBDataset.get(db, parent_namespace, parent_name)
        if parent_ds is None:
            return 404, "Parent dataset not found", "text/plain"
        child_ds = DBDataset.get(db, child_namespace, child_name)
        if child_ds is None:
            return 404, "Child dataset not found", "text/plain"
        
        if any(a.Namespace == child_namespace and a.Name == child_name for a in parent_ds.ancestors()):
            return 400, "Circular connection detected - child dataset is already an ancestor of the parent"
        
        if not any(c.Namespace == child_namespace and c.Name == child_name for c in parent_ds.children()):
            #print("Adding ", child_ds, " to ", parent_ds)
            parent_ds.add_child(child_ds)
        return "OK"

    @sanitized
    def remove_files(self, request, relpath, dataset=None, **args):
        #
        # add existing files to a dataset
        #

        user, error = self.authenticated_user()
        if user is None:
            return 403, "Client authentication failed"

        params = json.loads(request.body)
        file_list = params.get("file_list")
        query_text = params.get("query")

        if not file_list and not query_text:
            return 400, "No files to remove", "text/plain"
        if file_list and query_text:
            return 400, "Either file list or query must be specified, but not both", "text/plain"
            
        db = self.App.connect()
        ds_namespace, ds_name = parse_name(dataset)
        self.sanitize(namespace=default_namespace, dataset_namespace=ds_namespace, dataset_name=ds_name)
        if ds_namespace is None:
            return 400, "Dataset namespace unspecified", "text/plain"
        if not self._namespace_authorized(db, ds_namespace, user):
            return 403, f"Permission to add files dataset {dataset} denied", "text/plain"
        ds = DBDataset.get(db, ds_namespace, ds_name)
        if ds is None:
            return 404, "Dataset not found", "text/plain"
        if ds.Frozen:
            return 403, "Dataset is frozen", "text/plain"
        if ds.Monotonic:
            return 403, "Dataset is monotonic", "text/plain"
        
        if query_text:
            query = MQLQuery.parse(query_text)
            if query.Type != "file":
                return 400, f"Invalid file query: {query_text}"
            files = query.run(db, filters=self.App.filters(), with_meta=False, with_provenance=False)
        elif file_list:
            files = []
            for file_item in file_list:
                spec = ObjectSpec(file_item, namespace=default_namespace)
                self.sanitize(namespace=spec.Namespace, name=spec.Name, fid=spec.FID)
                
                if spec.FID:
                    f = DBFile(db, fid=spec.FID)
                else:
                    f = DBFile(db, spec.Namespace, spec.Name)
                files.append(f)
                #print("add_files: files:", files)
        
        if files:
            try:    ds.remove_files(files)
            except MetaValidationError as e:
                return 400, e.as_json(), "application/json"
        return json.dumps([f.to_jsonable() for f in files]), "application/json"


    @sanitized
    def add_files(self, request, relpath, dataset=None, **args):
        #
        # add existing files to a dataset
        #

        user, error = self.authenticated_user()
        if user is None:
            return 403, "Client authentication failed"

        params = json.loads(request.body)
        file_list = params.get("file_list")
        query_text = params.get("query")
        default_namespace = params.get("namespace")

        if not file_list and not query_text:
            return 400, "No files to add", "text/plain"
        if file_list and query_text:
            return 400, "Either file list or query must be specified, but not both", "text/plain"
            
        db = self.App.connect()
        ds_namespace, ds_name = parse_name(dataset, default_namespace)
        self.sanitize(namespace=default_namespace, dataset_namespace=ds_namespace, dataset_name=ds_name)
        if ds_namespace is None:
            return 400, "Dataset namespace unspecified", "text/plain"
        if not self._namespace_authorized(db, ds_namespace, user):
            return 403, f"Permission to add files dataset {dataset} denied", "text/plain"
        ds = DBDataset.get(db, ds_namespace, ds_name)
        if ds is None:
            return 404, "Dataset not found", "text/plain"
        if ds.Frozen:
            return 403, "Dataset is frozen", "text/plain"
        
        if query_text:
            query = MQLQuery.parse(query_text)
            if query.Type != "file":
                return 400, f"Invalid file query: {query_text}"
            files = query.run(db, filters=self.App.filters(), with_meta=False, with_provenance=False)
            #print("add_files: files from query:", files)

        elif file_list:
            files = []
            for file_item in file_list:
                spec = ObjectSpec(file_item, namespace=default_namespace)
                self.sanitize(namespace=spec.Namespace, name=spec.Name, fid=spec.FID)
                
                if spec.FID:
                    f = DBFile(db, fid=spec.FID)
                else:
                    f = DBFile(db, spec.Namespace, spec.Name)
                files.append(f)
                #print("add_files: files:", files)

        nadded = 0
        if files is not None:
            try:
                with db.transaction() as transaction:
                    nadded = ds.add_files(files, transaction=transaction)
                    ds.FileCount += nadded
                    ds.save(transaction=transaction)
            except MetaValidationError as e:
                return 400, e.as_json(), "application/json"
        return json.dumps({"files_added": nadded}), "application/json"

    @sanitized
    def remove_files(self, request, relpath, **args):
        user, error = self.authenticated_user()
        if user is None:
            return 403, "Client authentication failed"

        params = json.loads(request.body)
        file_list = params.get("file_list")
        query_text = params.get("query")
        default_namespace = params.get("namespace")
        ds_namespace = params.get("dataset_namespace", default_namespace)
        ds_name = params.get("dataset_name")
        if not ds_namespace or not ds_name:
            return 400, "Dataset unspecified", "text/plain"

        self.sanitize(namespace=default_namespace, dataset_namespace=ds_namespace, dataset_name=ds_name)

        if not file_list and not query_text:
            return 400, "No files to remove", "text/plain"
        if file_list and query_text:
            return 400, "Either file list or query must be specified, but not both", "text/plain"
            
        db = self.App.connect()
        if ds_namespace is None:
            return 400, "Dataset namespace unspecified", "text/plain"
        if not self._namespace_authorized(db, ds_namespace, user):
            return 403, f"Permission to add files dataset {dataset} denied", "text/plain"
        ds = DBDataset.get(db, ds_namespace, ds_name)
        if ds is None:
            return 404, "Dataset not found", "text/plain"
        if ds.Frozen:
            return 403, "Dataset is frozen", "text/plain"
        if ds.Monotonic:
            return 403, "Dataset is monotonic", "text/plain"
        
        resolved = []
        to_resolve = []

        if query_text:
            query = MQLQuery.parse(query_text)
            if query.Type != "file":
                return 400, f"Invalid file query: {query_text}"
            resolved = query.run(db, filters=self.App.filters(), with_meta=False, with_provenance=False)
            #print("add_files: files from query:", files)

        elif file_list:
            for file_item in file_list:
                spec = ObjectSpec(file_item, namespace=default_namespace)
                self.sanitize(namespace=spec.Namespace, name=spec.Name, fid=spec.FID)
                
                if spec.FID:
                    resolved.append(DBFile(db, fid=spec.FID))
                else:
                    to_resolve.append(spec.as_dict())
                #print("add_files: files:", files)
        with db.transaction() as transaction:
            if to_resolve:
                resolved += list(DBFile.get_files(db, to_resolve, transaction=transaction))
            nremoved = ds.remove_files(resolved, transaction=transaction)
        return json.dumps({"files_removed": nremoved}), "application/json"
        
    @sanitized
    def remove_dataset(self, request, relpath, dataset=None):
        #
        # add existing files to a dataset
        #
        
        dataset = dataset or relpath

        user, error = self.authenticated_user()
        if user is None:
            return 403, "Client authentication failed"

        ds_namespace, ds_name = parse_name(dataset)
        self.sanitize(dataset_namespace=ds_namespace, dataset_name=ds_name)
        if ds_namespace is None or ds_name is None:
            return 400, "Dataset specification error", "text/plain"
        db = self.App.connect()
        ds = DBDataset.get(db, ds_namespace, ds_name)
        if ds is None:
            return 404, "Dataset not found", "text/plain"
        if not self._namespace_authorized(db, ds_namespace, user):
            return 403, f"Permission denied", "text/plain"
        ds.delete()
        return "OK"

    @sanitized
    def datasets_for_files(self, request, relpath, **args):
        #
        # JSON data: list of one of the following
        #       { "namespace": "...", "name":"..." },
        #       { "fid":"..." }
        #
        db = self.App.connect()

        # validate input data
        file_list = json.loads(request.body) if request.body else []
        for item in file_list:
            fid = item.get(fid)
            namespace, name = item.get("namespace"), item.get("name")
            self.sanitize(namespace=namespace, name=name, fid=fid)
            
        datasets_by_file = DBDataset.datasets_for_files(db, file_list)
        out = []
        for item in file_list:
            out_item = item.copy()
            out_item["datasets"] = []
            fid = item.get(fid)
            namespace, name = item.get("namespace"), item.get("name")
            if fid:
                out_item["datasets"] = [ds.as_jsonable() for ds in datasets_by_file.get(fid, [])]
            elif namespace and name:
                out_item["datasets"] = [ds.as_jsonable() for ds in datasets_by_file.get((namespace, name), [])]
            out.append(out_item)
        return json.dumps(out), "application/json"

    def split_cat(self, path):
        if '.' in path:
            return tuple(path.rsplit(".",1))
        else:
            return ".", path
        
    def validate_metadata(self, data):
        categories = self.load_categories()
        invalid = []
        for k, v in data.items():
            path, name = self.split_cat(k)
            cat = categories.get(path)
            if cat is None:
                while True:
                    path, _ = self.split_cat(path)
                    if path in categories:
                        if categories[path].Restricted:
                            invalid.append({"name":k, "value":v, "reason":f"Category {path} is restricted"})
                        break
                    if path == ".":
                        break
            else:
                valid, reason = cat.validate_parameter(name, v)
                if not valid:
                    invalid.append({"name":k, "value":v, "reason":reason})
        return invalid
        
    @sanitized
    def declare_files(self, request, relpath, namespace=None, dataset=None, dry_run="no", **args):
        # Declare new files, add to the dataset
        # request body: JSON with list:
        #
        # [
        #       {       
        #               name: "namespace:name",   or "name", but then default namespace must be specified
        #               size: ..,                       // required
        #               fid: "fid",                     // optional
        #               parents:        [...],          // optional
        #               metadata: { ... }               // optional
        #               checksums: {                    // optional
        #                 "method":"value", ...
        #               }
        #       },...
        # ]
        #
        #   Parents can be specified with one of the following:
        #       {"fid":"..."}
        #       {"namespace":"...", "name":"..."}
        #               
        #   Dry run - do all the steps and checks, including metadata validation up to actual declaration
        default_namespace = namespace
        dry_run = dry_run == "yes"
        user, error = self.authenticated_user()
        if user is None:
            #print("Unauthenticated user")
            return 401, error

        if dataset is None:
            return 400, "Dataset not specified"

        db = self.App.connect()

        ds_namespace, ds_name = parse_name(dataset, default_namespace)
        try:
            if not self._namespace_authorized(db, ds_namespace, user):
                return 403, f"Permission to add files to dataset {ds_namespace}:{ds_name} denied"
        except KeyError:
            return 404, f"Namespace {ds_namespace} does not exist"

        ds = DBDataset.get(db, namespace=ds_namespace, name=ds_name)
        if ds is None:
            return 400, f"Dataset {ds_namespace}:{ds_name} does not exist"

        file_list = json.loads(request.body) if request.body else []
        if not file_list:
            return 400, "Empty file list"
        files = []
        errors = []
        parents_to_resolve = set()

        metadata_validation_errors = DBParamCategory.validate_metadata_bulk(db, [f["metadata"] for f in file_list])
        if metadata_validation_errors:
            for index, item_errors in metadata_validation_errors:
                errors.append({
                    "index": index,
                    "message":"Metadata category validation errors",
                    "metadata_errors":item_errors
                })
            return METADATA_ERROR_CODE, json.dumps({"metadata_errors": errors}), "application/json"

        checksum_validation_errors = []
        index = 0
        checksum_errors = False
        for f in file_list:
            item_errors = []
            self.validate_checksums(f,item_errors)
            if item_errors:
                checksum_validation_errors = True
                errors.append({
                    "index": index,
                    "message":"Metadata checksum validation errors",
                    "metadata_errors": item_errors
                })
            index = index + 1

        if checksum_validation_errors:
            return METADATA_ERROR_CODE, json.dumps({"metadata_errors": errors}), "application/json"
        
        for inx, file_item in enumerate(file_list):
            #print("data_handler.declare_files: file_item:", inx, file_item)
            namespace = file_item.get("namespace", default_namespace)
            name = file_item.get("name")
            fid = file_item.get("fid")
            
            self.sanitize(namespace=namespace, name=name, fid=fid)
            
            size = file_item.get("size")
            if size is None:
                errors.append({
                    "message": "Missing file size",
                    "fid": fid,
                    "index": inx
                })
                continue

            if name is None:
                did = file_item.get("did")
                if did is not None:
                    namespace, name = parse_name(did, namespace)
                    
            if name is None and "auto_name" not in file_item:
                errors.append({
                    "message":"Missing name and auto_name",
                    "index": inx,
                    "fid":fid
                })
                continue

            if not namespace:
                errors.append({
                    "message":"Missing namespace",
                    "index": inx,
                    "fid":fid
                })
                continue

            try:
                if not self._namespace_authorized(db, namespace, user):
                    errors.append({
                        "index": inx,
                        "fid":fid,
                        "message":f"Permission to declare files in namespace {namespace} denied"
                    })
                    continue
            except KeyError:
                    errors.append({
                        "index": inx,
                        "fid":fid,
                        "message":f"Namespace {namespace} does not exist"
                    })
                    continue

            meta = file_item.get("metadata", {})
            
            for k in meta.keys():
                if '.' not in k:
                    errors.append({
                        "index": inx,
                        "message":f"Metadata parameter without a category: {k}"
                    })

            ds_validation_errors = ds.validate_file_metadata(meta)
            if ds_validation_errors:
                #print("validation errors:", ds_validation_errors)
                errors.append({
                    "index": inx,
                    "message":f"Dataset metadata validation errors",
                    "metadata_errors":ds_validation_errors
                })
                continue

            fid = file_item.get("fid") or DBFile.generate_id()
            if name is None:
                pattern = file_item.get("auto_name", "$fid")
                clock = int(time.time() * 1000)
                clock3 = "%03d" % (clock%1000,)
                clock6 = "%06d" % (clock%1000000,)
                clock9 = "%09d" % (clock%1000000000,)
                clock = str(clock)
                u = uuid.uuid4().hex
                u8 = u[-8:]
                u16 = u[-16:]
                name = pattern.replace("$clock3", clock3).replace("$clock6", clock6).replace("$clock9", clock9)\
                    .replace("$clock", clock)\
                    .replace("$uuid8", u8).replace("$uuid16", u16).replace("$uuid", u)\
                    .replace("$fid", fid)
                print("")

            # allow admins to use creation info if present for migration.
            # default it if not passed, or not admin.
            if user.is_admin():
                creator = file_item.get("creator", user.Username)
                created_timestamp = file_item.get("created_timestamp", None)
            else:
                creator = user.Username
                created_timestamp = None

            f = DBFile(db, namespace=namespace, name=name, fid=fid, metadata=meta, size=size, creator=creator, created_timestamp=created_timestamp)
            f.Checksums = file_item.get("checksums")

            parents = []
            for item in file_item.get("parents") or []:
                if isinstance(item, str):
                    parents.append(item)
                elif isinstance(item, dict):
                    if "fid" in item:
                        parents.append(item["fid"])
                    else:
                        if "did" in item:
                            pns, pn = parse_name(item["did"], default_namespace)
                        elif "name" in item:
                            pn = item["name"]
                            pns = item.get("namespace", default_namespace)
                            if not pns:
                                errors.append({
                                    "index": inx,
                                    "message": "Parent specification error: no namespace: %s" % (item,)
                                })
                                error = True
                                break
                        else:
                            errors.append({
                                "index": inx,
                                "message": "Parent specification error: %s" % (item,)
                            })
                            break
                        parents.append((pns, pn))
                        parents_to_resolve.add((pns, pn))
                else:
                    errors.append({
                        "index": inx,
                        "message": "Parent specification error: %s" % (item,)
                    })
                    
            f.Parents = parents
            files.append(f)
            #print("data_handler.declare_files: file appended:", f)
        
        if not errors and parents_to_resolve:
            resolved = DBFile.get_files(db, ({"namespace":ns, "name":n} for ns, n in parents_to_resolve))
            resolved = list(resolved)
            did_to_fid = {(f.Namespace, f.Name): f.FID for f in resolved}
            for inx, f in enumerate(files):
                parents = []
                for item in f.Parents:
                    if isinstance(item, tuple):
                        fid = did_to_fid.get(item)
                        if not fid:
                            errors.append({
                                "index": inx,
                                "message": "Can not get file id for parent: %s:%s" % item
                            })
                            break
                        parents.append(fid)
                    else:
                        # assume str with fid
                        parents.append(item)
                f.Parents = parents
                                
        if errors:
            #print("data_handler.declare_files: errors:", errors)
            return METADATA_ERROR_CODE, json.dumps({
                    "message": "Invalid file data",
                    "metadata_errors": errors
                    }), "application/json"

        if dry_run:
            return 202, json.dumps(
                [
                    {
                        "name": f.Name,
                        "namespace": f.Namespace,
                        "fid":  f.FID
                    }
                    for f in files
                ]
            ), "application/json"

        with db.transaction() as transaction:
            try:    
                results = DBFile.create_many(db, files, user.Username, transaction=transaction)
                #print("data_server.declare_files: DBFile.create_may->results: ", results)
            except IntegrityError as e:
                transaction.rollback()
                existing = list(DBFile.get_files(db, files))
                if existing:
                    message = "The following files already exist:"
                    for f in existing:
                        message += "\n  " + f.did()
                else:
                    message = f"Integrity error: {e}"
                return 409, message
            
            #print("server:declare_files(): calling ds.add_files...")
            ds.add_files(files, validate_meta=False, transaction=transaction)
        
        out = [f.to_jsonable(with_metadata=True) for f in results]
        return json.dumps(out), "application/json"

    def move_files(self, request, relpath, **args):
        """
        Moves files to new namespace
        the request body expected to be:
        {
            "namespace": "new_namespace",
            "files" : [
                { "namespace":..., "name":... } or
                { "fid": ... }
                ...
            ]
            or
            "query": "MQL query"
        }
        """
        
        user, error = self.authenticated_user()
        if user is None:
            return 403, "Authentication required"

        db = self.App.connect()
        data = json.loads(request.body)
        if not isinstance(data, dict):
            return 400, "Unsupported request data format"
        if "namespace" not in data:
            return 400, "Request must contain target namespace"
        
        authorized_namespaces = set(ns.Name for ns in user.namespaces())
        target_namespace = data["namespace"]
        if DBNamespace.get(db, target_namespace) is None:
            return 404, f"Namespace {target_namespace} not found"
        if target_namespace not in authorized_namespaces:
            return 401, f"Not authorized to rename into namespace {target_namespace}"
            
        if "files" in data:
            files = DBFile.get_files(db, data["files"])
        else:
            query = MQLQuery.parse(data["query"])
            if query.Type != "file":
                return 400, f"Invalid file query: {query_text}"
            files = query.run(db, filters=self.App.filters(), with_meta=False, with_provenance=False)

        nmoved, errors = DBFile.move_to_namespace(db, target_namespace, files, authorized_namespaces=authorized_namespaces)
        nerrors = len(errors)
        return json.dumps({"files_moved": nmoved, "errors":errors[:self.MAX_ERRORS], "nerrors":nerrors}), "application/json"

    def __update_meta_bulk(self, db, user, new_meta, mode, names=None, ids=None):
        metadata_errors = self.validate_metadata(new_meta)
        if metadata_errors:
            return METADATA_ERROR_CODE, json.dumps({
                "message":"Metadata validation errors",
                "metadata_errors":metadata_errors
            }), "application/json"
        
        file_sets = []
        out = []
        if ids:
            file_sets.append(DBFileSet.from_id_list(db, ids))
        if names:
            file_sets.append(DBFileSet.from_namespace_name_specs(db, names))

        if file_sets:
            file_set = list(DBFileSet.union(db, file_sets))
            files_datasets = DBDataset.datasets_for_files(db, file_set)
            #print("files_datasets:", files_datasets)
            
            #
            # validate new metadata for affected datasets
            #
            all_datasets = {}
            for dslist in files_datasets.values():
                for ds in dslist:
                    all_datasets[(ds.Namespace, ds.Name)] = ds
            for ds in all_datasets.values():
                errors = ds.validate_file_metadata(new_meta)
                if errors:
                    metadata_errors += errors

            if metadata_errors:
                #print("update_files_bulk:", metadata_errors)
                return METADATA_ERROR_CODE, json.dumps({
                    "message":"Metadata validation errors",
                    "metadata_errors":metadata_errors
                }), "application/json"

            #
            # check namespace permissions
            #
            for f in file_set:
                namespace = f.Namespace
                try:
                    if not self._namespace_authorized(db, namespace, user):
                        return 403, f"Permission to update files in namespace {namespace} denied"
                except KeyError:
                    return 404, f"Namespace {namespace} does not exist"
            
                #
                # update the metadata
                #
                meta = new_meta
                if mode == "update":
                    meta = {}
                    meta.update(f.Metadata)   # to make a copy
                    meta.update(new_meta)

                for k in meta.keys():
                    if '.' not in k:
                        return 400, f"Metadata parameter without a category: {k}"

                f.Metadata = meta

                out.append(                    
                    dict(
                            name=f.Name,
                            namespace=f.Namespace,
                            fid=f.FID,
                            metadata=meta
                        )
                )

            DBFile.update_many(db, file_set)

        return json.dumps(out), "application/json"

    @sanitized
    def delete_file(self, request, relpath, **args):
        # mode can be "update" - add/pdate metadata with new values
        #             "replace" - discard old metadata and update with new values
        # 
        # Update existing file information
        #
        user, error = self.authenticated_user()
        if user is None:
            return 403, "Authentication required"
        db = self.App.connect()
        #print("request.body:", request.body)
        data = json.loads(request.body)
        if not isinstance(data, dict):
            return 400, "Unsupported request data format"

        data = json.loads(request.body)
        
        spec = ObjectSpec.from_dict(data)

        if spec.FID:
            self.sanitize(fid = spec.FID)
            f = DBFile.get(db, fid=spec.FID, with_metadata=True)
        else:
            self.sanitize(namespace=spec.Namespace, name=spec.Name)
            f = DBFile.get(db, namespace=spec.Namespace, name=spec.Name, with_metadata=True)
        if f is None:
            return 404, "File not found"

        if not self._namespace_authorized(db, f.Namespace, user):
            return 403, "Not authorized"
            
        f.delete()
        return '{"fid":"%s"}' % (f.FID,)

    def validate_checksums(self, data, errors):
        """validate checksums are in hex and of appropriate length """

        for ct in data.get("checksums",[]):
            cs = data["checksums"][ct]
            ct = ct.lower()
            if ct in self.expected_len and len(cs) != self.expected_len[ct]:
                errors.append({
                    "name": f"checksum {ct}", 
                    "reason":f"value is wrong length {len(cs)} should be {self.expected_len[ct]}",
            })
            for c in cs:
                if not ((c >= "0" and c <= "9") or (c >= "a" and c <= "f")):
                    errors.append({"name":f"checksum {ct}", "reason": f"contains invalid digit {c}"})

    @sanitized
    def update_file(self, request, relpath, **args):
        # mode can be "update" - add/pdate metadata with new values
        #             "replace" - discard old metadata and update with new values
        # 
        # Update existing file information
        #
        user, error = self.authenticated_user()
        if user is None:
            return 403, "Authentication required"
        db = self.App.connect()
        data = json.loads(request.body)
        if not isinstance(data, dict):
            return 400, "Unsupported request data format"

        data = json.loads(request.body)
        spec = ObjectSpec.from_dict(data)

        if spec.FID:
            self.sanitize(fid = spec.FID)
            f = DBFile.get(db, fid=spec.FID, with_metadata=True)
        else:
            self.sanitize(namespace=spec.Namespace, name=spec.Name)
            f = DBFile.get(db, namespace=spec.Namespace, name=spec.Name, with_metadata=True)

        if not f:
            return 404, "File not found"
        
        if not self._namespace_authorized(db, f.Namespace, user):
            return 403, "Not authtorized"

        mode = data.get("mode", "add-update")

        errors = []
        if "metadata" in data:
            for k in data["metadata"].keys():
                if '.' not in k:
                    errors.append({
                        "name": k,
                        "message":f"Metadata parameter without a category: {k}"
                    })

            if mode == "add-update":
                new_metadata = f.Metadata.copy()
                new_metadata.update(data["metadata"])
            else:
                new_metadata = data["metadata"]

            # validate new metadata against file datasets
            for ds_ns, ds_n in f.datasets:
                ds = DBDataset(db, ds_ns, ds_n)
                if ds is None:
                    return 500, f"Can not load dataset {ds_ns}:{ds_n}"
                errors += ds.validate_file_metadata(new_metadata)
        
            if not errors:
                f.Metadata = new_metadata

        if "checksums" in data:
            self.validate_checksums(data, errors)
            if errors:
                return METADATA_ERROR_CODE, json.dumps({
                    "message":          "Metadata validation errors",
                    "metadata_errors":  errors
                }), "application/json"
               
            if mode == "add-update":
                new_checksums = (f.Checksums or {}).copy()
                new_checksums.update(data["checksums"])
            else:
                new_checksums = data["checksums"]

            if not errors:
                f.Checksums = new_checksums

        if errors:
            return METADATA_ERROR_CODE, json.dumps({
                "message":          "Metadata validation errors",
                "metadata_errors":  errors
            }), "application/json"

        if "parents" in data:
            # parents are specified as dictionaries either {"fid":...} or {"namespace":..., "name":...}
            parents = list(DBFile.get_files(db, data["parents"]))
            fids = set(p.FID for p in parents)
            dids = set((p.Namespace, p.Name) for p in parents)
            for p in data["parents"]:
                if "fid" in p and p["fid"] not in fids:
                    return ValueError("File fid=%s not found" % (p["fid"]))
                elif (p["namespace"], p["name"]) not in dids:
                    return ValueError("File %s:%s not found" % (p["namespace"], p["name"]))
            if mode == "add-update":
                f.add_parents(parents)
            else:
                f.set_parents(parents)

        if "children" in data:
            children = list(DBFile.get_files(db, data["children"]))
            fids = set(c.FID for c in children)
            dids = set((c.Namespace, c.Name) for c in children)
            for c in data["children"]:
                if "fid" in c and c["fid"] not in fids:
                    return ValueError("File fid=%s not found" % (c["fid"]))
                elif (c["namespace"], c["name"]) not in dids:
                    return ValueError("File %s:%s not found" % (c["namespace"], c["name"]))
            if mode == "add-update":
                f.add_children(children)
            else:
                f.set_children(children)

        f.Size = data.get("size", f.Size or 0)

        f.update(user)
        return f.to_json(with_metadata=True, with_provenance=True), "application/json"
                
    @sanitized
    def update_file_meta(self, request, relpath, **args):
        # mode can be "update" - add/pdate metadata with new values
        #             "replace" - discard old metadata and update with new values
        # 
        # Update metadata for existing files
        #
        # common changes for many files, cannot be used to update parents
        # {
        #   files: [ ... ]              # dicts, either namespace/name or fid
        #   metadata: { ... }
        #   mode: "update" or "replace"
        # }
        #
        user, error = self.authenticated_user()
        if user is None:
            return 403, "Authentication required"
        db = self.App.connect()
        data = json.loads(request.body)
        if not isinstance(data, dict):
            return 400, "Unsupported request data format"
        mode = data["mode"]
        if mode not in ("update", "replace"):
            return 400, "Invalid mode"

        by_fid = []
        by_namespace_name = []
        for f in data["files"]:
            spec = ObjectSpec.from_dict(f)
            if spec.FID:
                self.sanitize(fid = spec.FID)
                by_fid.append(spec.FID)
            else:
                self.sanitize(namespace=spec.Namespace, name=spec.Name)
                by_namespace_name.append({"namespace":spec.Namespace, "name":spec.Name})
        return self.__update_meta_bulk(db, user, data["metadata"], data["mode"], ids=by_fid, names=by_namespace_name)
        
    @sanitized
    def file(self, request, relpath, did=None, namespace=None, name=None, fid=None, with_metadata="yes", with_provenance="yes", 
            with_datasets="no", **args):
        with_metadata = with_metadata == "yes"
        with_provenance = with_provenance == "yes"
        with_datasets = with_datasets == "yes"

        if relpath:
            if ':' in relpath:
                did = did or relpath
            else:
                fid = fid or relpath

        if did:
            namespace, name = did.split(':', 1)

        self.sanitize(namespace=namespace, name=name, fid=fid)

        db = self.App.connect()
        if fid:
            f = DBFile.get(db, fid = fid)
        else:
            f = DBFile.get(db, namespace=namespace, name=name)
        if f is None:
            return 404, "File not found"
        return f.to_json(with_metadata=with_metadata, with_provenance=with_provenance, with_datasets=with_datasets), "application/json"
        
    @sanitized
    def retire_file(self, request, relpath):
        #print("retire file...")
        user, error = self.authenticated_user()
        if user is None:
            return 403, "Authentication required"

        data = json.loads(request.body)
        retire = data["retire"]
        db = self.App.connect()
        spec = ObjectSpec.from_dict(data)
        namespace = None
        if spec.FID:
            f = DBFile.get(db, fid = spec.FID)
        else:
            f = DBFile.get(db, namespace=spec.Namespace, name=spec.Name)
        if f is None:
            return 404, "File not found"
        namespace = f.Namespace
        try:
            if not self._namespace_authorized(db, namespace, user):
                return 403, f"Permission to manage files in namespace {namespace} denied"
        except KeyError:
            return 404, f"Namespace {namespace} does not exist"
        #print("retire_file: doing it: retire:", retire, "  f.Retired:", f.Retired)
        if retire != f.Retired:
            #print("retire_file: doing it: retire:", retire, "  f.Retired:", f.Retired)
            f.set_retire(retire, user.Username)
        return f.to_json(), "application/json"

    @sanitized
    def files(self, request, relpath, with_metadata="no", with_provenance="no", **args):
        with_metadata = with_metadata=="yes"
        with_provenance = with_provenance=="yes"
        #print("data_handler.files(): with_metadata:", with_metadata,"  with_provenance:", with_provenance)
        #print("environ:", request.environ)
        file_list = json.loads(request.body)
        lookup_lst = []
        for f in file_list:
            spec = ObjectSpec(f)
            self.sanitize(namespace=spec.Namespace, name=spec.Name, fid=spec.FID)
            
            lookup_lst.append(spec.as_dict())

        db = self.App.connect()
        files = list(DBFile.get_files(db, lookup_lst))
        out = [f.to_jsonable(with_metadata = with_metadata, with_provenance = with_provenance) 
                for f in files
        ]
        return json.dumps(out), "application/json"

    @sanitized
    def query(self, request, relpath, query=None, namespace=None, 
                    with_meta="no", with_provenance="no", debug="no", include_retired_files="no",
                    add_to=None, save_as=None, summary=None,
                    **args):

        if summary not in ("count", "keys", None):
            return 400, f"Unsupported summary type: {summary}"

        with_meta = with_meta == "yes"
        with_provenance = with_provenance == "yes"
        include_retired_files = include_retired_files == "yes"

        if summary == "count":
            with_meta = False
            with_provenance = False

        if summary in ("keys", "key-values"):
            with_meta = True
            with_provenance = False

        self.sanitize(namespace=namespace)

        if query is not None:
            query_text = unquote_plus(query)
            #print("query from URL:", query_text)
        elif "query" in request.POST:
            query_text = request.POST["query"]
            #print("query from POST:", query_text)
        else:
            query_text = request.body
            #print("query from body:", query_text)
        query_text = to_str(query_text or "")
        
        add_namespace = add_name = ds_namespace = ds_name = None

        db = self.App.connect()
        user, error = self.authenticated_user()
        if (save_as or add_to) and user is None:
            return 401, error

        add_to_dataset = None
        if save_as:
            ds_namespace, ds_name = parse_name(save_as, namespace)
            self.sanitize(dataset_namespace=ds_namespace, dataset_name=ds_name)
            ns = DBNamespace.get(db, ds_namespace)

            if ns is None:
                return 404, f"Namespace {ds_namespace} does not exist"

            if not ns.owned_by_user(user):
                return 403, f"Permission to create a dataset in the namespace {ds_namespace} denied"

            if DBDataset.exists(db, ds_namespace, ds_name):
                return 409, f"Dataset {ds_namespace}:{ds_name} already exists"
                
            add_to_dataset = DBDataset(db, ds_namespace, ds_name)
            add_to_dataset.create()
            
        elif add_to:
            add_namespace, add_name = parse_name(add_to, namespace)
            self.sanitize(dataset_namespace=add_namespace, dataset_name=add_name)
            ns = DBNamespace.get(db, add_namespace)

            if ns is None:
                return 404, f"Namespace {add_namespace} does not exist"

            if not ns.owned_by_user(user):
                return 403, f"Permission to add files to dataset in the namespace {add_namespace} denied"

            add_to_dataset = DBDataset.get(db, add_namespace, add_name)
            if add_to_dataset is None:
                return 404, f"Dataset {add_namespace}:{add_name} does not exist"

        t0 = time.time()
        if not query_text:
            return "[]", "application/json"
            
        try:
            query = MQLQuery.parse(query_text, 
                        db=db, 
                        default_namespace=namespace or None, 
                        include_retired_files=include_retired_files
            )
            query_type = query.Type
            results = query.run(db, filters=self.App.filters(), with_meta=with_meta, with_provenance=with_provenance,
                debug = debug == "yes"
            )
        except (AssertionError, ValueError, MQLError) as e:
            #traceback.print_exc()
            return 400, e.__class__.__name__ + ": " + e.Message

        if results is None: 
            return "[]", "application/json"

        if query_type == "file":

            if summary == "count":
                count, size = results.counts()
                return json.dumps({"count":count, "total_size":size}), "application/json"
            elif summary == "keys":
                return json.dumps(list(results.metadata_keys())), "application/json"

            if add_to_dataset is not None:
                results = list(results)
                nfiles = add_to_dataset.add_files(results)
            data = (f.to_jsonable(with_metadata=with_meta, with_provenance=with_provenance) for f in results)
            return self.json_stream(data), "application/json-seq"

        else:
            # dataset query
            data = ( d.to_jsonable(with_relatives=with_provenance) for d in results )

        return self.json_stream(data), "application/json-seq"
        
    @sanitized
    def search_queries(self, request, relpath, query=None,**args):
        if query is not None:
            query_text = unquote_plus(query)
            #print("query from URL:", query_text)
        elif "query" in request.POST:
            query_text = request.POST["query"]
            #print("query from POST:", query_text)
        else:
            query_text = request.body
            #print("query from body:", query_text)
        query_text = to_str(query_text or "")
        
        db = self.App.connect()
        t0 = time.time()
        if not query_text:
            return "[]", "application/json"
            
        try:
            query = MQLQuery.parse(query_text, db=db)
            query_type = query.Type
            if query_type != "query":
                return 400, "Invalid query type"
            results = query.run(db, with_meta=True)
        except (AssertionError, ValueError, MQLError) as e:
            #traceback.print_exc()
            return 400, e.__class__.__name__ + ": " + e.Message

        if results is None:
            return "[]", "application/json"

        data = ( q.to_jsonable() for q in results )
        return self.json_stream(data), "application/json-seq"
        
    @sanitized
    def named_queries(self, request, relpath, namespace=None, **args):
        db = self.App.connect()
        queries = list(DBNamedQuery.list(db, namespace))
        data = [q.to_jsonable() for q in queries]
        return json.dumps(data), "application/json"

    @sanitized
    def named_query(self, request, relpath, namespace=None, name=None, **args):
        db = self.App.connect()
        q = DBNamedQuery.get(db, namespace, name)
        if q is None:
            return 404, "Query not found"
        return q.to_json(), "application/json"
    
    @sanitized
    def create_named_query(self, request, relpath, update="no", **agrs):
        update = update == "yes"
        user, error = self.authenticated_user()
        if user is None:
            return 401, "Authentication required"
        data = json.loads(request.body)
        db = self.App.connect()
        namespace = data["namespace"]
        name = data["name"]
        try:
            if not self._namespace_authorized(db, namespace, user):
                return 403, "Permission denied"
        except KeyError:
            return 404, f"Namespace {namespace} does not exist"
        existing = DBNamedQuery.get(db, namespace, name)
        if existing is not None:
            if not update:
                return 409, "Query already exists"
            existing.Source = data["source"]
            existing.Parameters = data.get("parameters", [])
            existing.Creator = user.Username
            existing.CreatedTimestamp = datetime.now()
            existing.save()
            q = existing
        else:
            q = DBNamedQuery(db, namespace, name, data["source"], data.get("parameters", []))
            q.Creator = user.Username
            q.create()
        return 200, q.to_json(), "application/json"

    #
    # Parameter categories
    #
    def categories(self, request, relpath, **args):
        db = self.App.connect()
        lst = DBParamCategory.list(db)
        out = [cat.to_jsonable() for cat in lst]
        return json.dumps(out), "application/json"

    @sanitized
    def category(self, request, relpath, path=None, **args):
        path = path or relpath
        if not path:
            return 400, "Category path not specified", "text/plain"
        db = self.App.connect()
        cat = DBParamCategory.get(db, path)
        if cat is None:
            out = None
        else:
            out = cat.to_jsonable()
        return json.dumps(out), "application/json"
        
        
        
