"""
Asynchronous MetaCat client built on httpx.AsyncClient.

This mirrors metacat.webapi.webapi.MetaCatClient, but every method that talks to the
server is a coroutine (``await client.get_dataset(...)``) and every method that yielded
results in the synchronous client is an async generator (``async for f in client.query(...)``).

Usage:

    async with AsyncMetaCatClient(server_url) as client:
        print(await client.get_version())
        async for f in client.query("files from ns:ds"):
            ...

Exception classes are shared with the synchronous client.
"""

import asyncio, json, fnmatch, os, random, time

try:
    import httpx
except ModuleNotFoundError:
    raise ModuleNotFoundError(
        "httpx module required for the async client. Use: pip install 'metacat-client[async]'"
    )
from urllib.parse import quote_plus
from metacat.util import ObjectSpec, chunked
from metacat.common import SignedToken, TokenAuthClientMixin
from .webapi import (
    INVALID_METADATA_ERROR_CODE,
    parse_name,
    undid,
    MCError,
    WebAPIError,
    ServerReportedError,
    InvalidArgument,
    NotFoundError,
    BadRequestError,
    AlreadyExistsError,
    PermissionDeniedError,
    InvalidMetadataError,
    AuthenticationError,
)


class AsyncHTTPClient(object):

    InitialRetry = 1.0
    RetryExponent = 1.5
    DefaultTimeout = 1800.0
    RetryStatusCodes = (502, 503, 504, 521, 524)

    def __init__(self, server_url, token, timeout=None, client_args=None):
        self.ServerURL = server_url
        self.Token = token
        if timeout is not None and timeout <= 0:
            self.Timeout = None  # no timeout
        else:
            self.Timeout = timeout or self.DefaultTimeout
        self.ClientArgs = client_args or {}
        self.Client = None
        self.LastResponse = self.LastURL = self.LastStatusCode = None

    @property
    def client(self):
        if self.Client is None:
            self.Client = httpx.AsyncClient(timeout=httpx.Timeout(self.Timeout), **self.ClientArgs)
        return self.Client

    async def aclose(self):
        if self.Client is not None:
            await self.Client.aclose()
            self.Client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        await self.aclose()

    async def retry_request(self, method, url, timeout=None, stream=False, **args):
        """
        Implements the functionality to retry on 503 response with random exponentially growing delay
        Use timemout = 0 to try the request exactly once
        Returns the response with status=503 on timeout
        """
        if timeout is None:
            timeout = self.DefaultTimeout
        tend = time.time() + timeout
        retry_interval = self.InitialRetry
        while True:
            request = self.client.build_request(method.upper(), url, **args)
            response = await self.client.send(request, stream=stream)
            if response.status_code not in self.RetryStatusCodes:
                break
            sleep_time = min(random.random() * retry_interval, tend - time.time())
            retry_interval *= self.RetryExponent
            if sleep_time < 0:
                break  # time out
            if stream:
                await response.aclose()
            await asyncio.sleep(sleep_time)
        return response

    def request_headers(self, headers=None):
        default_headers = {
            "Accept": "text/plain, application/json, text/json, application/json-seq"
        }
        if self.Token is not None:
            default_headers["X-Authentication-Token"] = self.Token.encode()
        if headers:
            default_headers.update(headers)
        return default_headers

    async def check_response(self, url, response, stream=False):
        status = response.status_code
        if status // 100 == 2:
            return
        if stream:
            # exceptions need the body
            await response.aread()
            await response.aclose()
        if status == INVALID_METADATA_ERROR_CODE:
            raise InvalidMetadataError(url, response)
        elif status == 404:
            raise NotFoundError(url, response)
        elif status == 403:
            raise PermissionDeniedError(url, response)
        elif status == 409:
            raise AlreadyExistsError(url, response)
        elif status == 400:
            raise BadRequestError(url, response)
        raise WebAPIError(url, response)

    async def send_request(
        self, method, uri_suffix, headers=None, timeout=None, stream=False, **args
    ):
        self.LastURL = url = "%s/%s" % (self.ServerURL, uri_suffix)
        headers = self.request_headers(headers)
        self.LastResponse = response = await self.retry_request(
            method, url, timeout=timeout, stream=stream, headers=headers, **args
        )
        self.LastStatusCode = response.status_code
        await self.check_response(url, response, stream=stream)
        return response

    async def get_text(self, uri_suffix):
        return (await self.send_request("get", uri_suffix)).text

    async def post_text(self, uri_suffix, data):
        return (await self.send_request("post", uri_suffix, content=data)).text

    def unpack_json(self, json_text):
        results = json.loads(json_text)
        if isinstance(results, dict):
            if "results" in results:
                results = results["results"]
            elif "error" in results:
                raise ServerReportedError(
                    self.LastURL,
                    self.LastStatusCode,
                    results["error"]["type"],
                    results["error"].get("value", ""),
                )
        return results

    RS = "\x1e"

    def unpack_json_seq_text(self, text):
        # str.splitlines() treats the RS character as a line boundary too
        return [
            self.unpack_json(line) for line in text.splitlines() if line.strip(self.RS + " \t")
        ]

    def unpack_json_data(self, response):
        if "application/json-seq" in response.headers.get("content-type", ""):
            return self.unpack_json_seq_text(response.text)
        else:
            return self.unpack_json(response.text)

    @staticmethod
    def encode_json(data):
        if not isinstance(data, (str, bytes)):
            data = json.dumps(data)
        return data

    JSONAccept = "application/json-seq, application/json, text/json"

    async def get_json(self, uri_suffix):
        """Returns the whole response. A json-seq response is collected into a list."""
        headers = {"Accept": self.JSONAccept}
        return self.unpack_json_data(await self.send_request("get", uri_suffix, headers=headers))

    async def post_json(self, uri_suffix, data):
        """Returns the whole response. A json-seq response is collected into a list."""
        headers = {"Accept": self.JSONAccept, "Content-Type": "text/json"}
        response = await self.send_request(
            "post", uri_suffix, content=self.encode_json(data), headers=headers
        )
        return self.unpack_json_data(response)

    async def iter_json(self, method, uri_suffix, data=None):
        """
        Async generator yielding result items as they arrive.
        A json-seq response yields one item per record; a plain JSON list response yields
        one item per list element; any other JSON response is yielded as a single item.
        """
        headers = {"Accept": self.JSONAccept}
        args = {}
        if data is not None:
            headers["Content-Type"] = "text/json"
            args["content"] = self.encode_json(data)
        response = await self.send_request(
            method, uri_suffix, headers=headers, stream=True, **args
        )
        try:
            if "application/json-seq" in response.headers.get("content-type", ""):
                async for line in response.aiter_lines():
                    line = line.strip().lstrip(self.RS)
                    if line:
                        yield self.unpack_json(line)
            else:
                await response.aread()
                results = self.unpack_json(response.text)
                if isinstance(results, list):
                    for item in results:
                        yield item
                else:
                    yield results
        finally:
            await response.aclose()

    async def get_json_stream(self, uri_suffix):
        """Async generator over a response which must be application/json-seq"""
        url = "%s/%s" % (self.ServerURL, uri_suffix)
        headers = {"Accept": "application/json-seq"}
        if self.Token is not None:
            headers["X-Authentication-Token"] = self.Token.encode()

        response = await self.retry_request("get", url, headers=headers, stream=True)
        try:
            await self.check_response(url, response, stream=True)
            if (
                response.status_code != 200
                or response.headers.get("Content-Type") != "application/json-seq"
            ):
                await response.aread()
                raise WebAPIError(url, response)

            async for line in response.aiter_lines():
                line = line.strip().lstrip(self.RS)
                if line:
                    yield json.loads(line)
        finally:
            await response.aclose()


class AsyncMetaCatClient(AsyncHTTPClient, TokenAuthClientMixin):
    """
    Asynchronous counterpart of MetaCatClient.

    The login_* methods inherited from TokenAuthClientMixin remain synchronous.
    """

    Version = "1.0"

    def __init__(
        self,
        server_url=None,
        auth_server_url=None,
        max_concurrent_queries=5,
        token=None,
        token_file=None,
        token_library=None,
        timeout=None,
        client_args=None,
    ):
        """Initializes the AsyncMetaCatClient object

        Arguments
        ---------
        server_url : str
            The server endpoint URL, defult = from METACAT_SERVER_URL environment variable
        auth_server_url : str
            The endpoint URL for the Authentication server, default = server_url + "/auth"
        max_concurrent_queries : int, optional
            Limits the number of concurrent requests made by async_query() and list_datasets(with_counts=True)
        token_file : str
            File path to read the authentication token from
        token : bytes or str or SignedToken
            Use this token for authentication, optional
        timeout : int or float
            Request timeout in seconds. Default: None - use default timeout, which is 600 seconds
        client_args : dict
            Extra keyword arguments for httpx.AsyncClient, e.g. {"verify": "/path/to/ca.pem"}
        """

        server_url = server_url or os.environ.get("METACAT_SERVER_URL")
        if not server_url:
            raise RuntimeError("MetaCat server URL unspecified")

        auth_server_url = auth_server_url or os.environ.get("METACAT_AUTH_SERVER_URL")

        # using a longer timeout on the server side in case of large
        # queries, client should too
        if timeout is None:
            timeout = 600

        TokenAuthClientMixin.__init__(
            self,
            server_url,
            auth_server_url,
            token=token,
            token_file=token_file,
            token_library=token_library,
        )
        AsyncHTTPClient.__init__(
            self, server_url, token=self.token(), timeout=timeout, client_args=client_args
        )
        self.MaxConcurrent = max_concurrent_queries
        self.Semaphore = None
        self.PendingQueries = set()

    def sanitize(self, *words, **kw):
        for w in words:
            if "'" in words:
                raise InvalidArgument("", "Invalid value: %s" % (w,))
        for name, value in kw.items():
            if "'" in value:
                raise InvalidArgument("", "Invalid value for %s: %s" % (name, value))
            if "'" in name:
                raise InvalidArgument("", "Invalid name for: %s" % (name,))

    @property
    def semaphore(self):
        # created lazily so that it binds to the running event loop
        if self.Semaphore is None:
            self.Semaphore = asyncio.Semaphore(self.MaxConcurrent)
        return self.Semaphore

    def resfresh_token(self):
        if self.TokenFile:
            token = open(self.TokenFile, "rb").read()
            self.Token = SignedToken.decode(token)
        return self.Token

    async def simulate_503(self):
        return await self.get_text("data/simulate_503")

    async def get_version(self):
        """Returns server version as text"""
        return await self.get_text("data/version")

    async def list_datasets(self, namespace_pattern=None, name_pattern=None, with_counts=False):
        """Gets the list of datasets with namespace/name matching the templates. The templates are
        Python ``fnmatch`` module style templates where ``'*'`` matches any substring and ``'?'`` matches a single character.

        Arguments
        ---------
        namespace_pattern : str
        name_pattern : str
        with_counts : boolean
            controls whether the results should include file counts or dataset names only

        Yields
        ------
        async generator
            yields dictionaries like {"namespace":..., "name":..., "file_count":...}
        """
        lst = await self.get_json("data/datasets?with_counts=no")
        selected = []
        for item in lst:
            namespace, name = item["namespace"], item["name"]
            if namespace_pattern is not None and not fnmatch.fnmatch(namespace, namespace_pattern):
                continue
            if name_pattern is not None and not fnmatch.fnmatch(name, name_pattern):
                continue
            if not with_counts:
                yield item
            else:
                selected.append(item)

        if selected:

            async def counts(item):
                async with self.semaphore:
                    return await self.get_dataset_counts(item["namespace"] + ":" + item["name"])

            # fetch counts concurrently
            all_counts = await asyncio.gather(*(counts(item) for item in selected))
            for item, c in zip(selected, all_counts):
                item.update(c or {})
                yield item

    async def get_dataset_counts(self, did=None, namespace=None, name=None):
        """Gets single dataset files, subsets, supersets, etc. counts

        Arguments
        ---------
        did : str - "namespace:name"
        namespace : str
        name : str

        Returns
        -------
        dict
            dataset counts or None if the dataset was not found
        """
        did = ObjectSpec(did, namespace=namespace, name=name).did()
        try:
            return await self.get_json(f"data/dataset_counts?dataset={did}")
        except NotFoundError:
            return None

    async def get_dataset(
        self, did=None, namespace=None, name=None, exact_file_count=False, with_subsets=False
    ):
        """Gets single dataset

        Arguments
        ---------
        did : str - "namespace:name"
        namespace : str
        name : str

        Returns
        -------
        dict
            dataset attributes or None if the dataset was not found
        """
        spec = ObjectSpec(did, namespace=namespace, name=name).did()
        try:
            url = f"data/dataset?dataset={spec}"
            if exact_file_count:
                url += "&exact_file_count=yes"
            if with_subsets:
                url += "&with_subsets=yes"
            return await self.get_json(url)
        except NotFoundError:
            return None

    async def get_dataset_files(
        self,
        did,
        namespace=None,
        name=None,
        with_metadata=False,
        include_retired_files=False,
        with_subsets=False,
    ):
        """Gets files in a dataset

        Arguments
        ---------
        did : str - "namespace:name"
        namespace : str
        name : str

        Returns
        -------
        async generator
            generates sequence of dictionaries, one dictionary per file.
            Raises NotFoundError if the dataset does not exist.
        """
        if namespace is not None:
            did = namespace + ":" + name
        with_metadata = "yes" if with_metadata else "no"
        include_retired_files = "yes" if include_retired_files else "no"
        url = f"data/dataset_files?dataset={did}&with_metadata={with_metadata}&include_retired_files={include_retired_files}"
        async for item in self.get_json_stream(url):
            yield item

    async def create_dataset(
        self,
        did,
        frozen=False,
        monotonic=False,
        metadata=None,
        metadata_requirements=None,
        files_query=None,
        subsets_query=None,
        description="",
        batchsize=0,
    ):
        """Create a new dataset

        Arguments
        ---------
            did : str  - "namespace:name"
            frozen : boolean - once created and populated, freeze dataset
            monotonic : boolean - files cannot be removed, it can only grow
            metadata : dict  - key value pairs for dataset
            metadata_requirements : dict - field requirements for files added to this dataset
            files_query : str  - file query for files to put in dataset
            subsets_query : str  - datset query for datasets to add as subsets
            description : str - description of datsaet
            batchsize : int - add query file results in batches of this many files at a time
        """
        if batchsize and files_query:
            # if batching, limit initial query and don't freeze yet...
            base_query = files_query
            files_query = f"({base_query}) ordered limit {batchsize}"
            frozen2 = frozen
            frozen = False

        out = await self._create_dataset(
            did,
            frozen=frozen,
            monotonic=monotonic,
            metadata=metadata,
            metadata_requirements=metadata_requirements,
            files_query=files_query,
            subsets_query=subsets_query,
            description=description,
        )

        if batchsize and files_query:
            keep_going = True
            batch = 0
            # now add remaining files in batches
            while keep_going:
                batch = batch + 1
                files_query = f"({base_query}) ordered skip {batch*batchsize} limit {batchsize}"
                af = await self.add_files(did, query=files_query)
                out["file_count"] += af
                keep_going = af > 0

            # finally set frozen flag if requested
            if frozen2:
                await self.update_dataset(did, frozen=frozen2)

        return out

    async def _create_dataset(
        self,
        did,
        frozen=False,
        monotonic=False,
        metadata=None,
        metadata_requirements=None,
        files_query=None,
        subsets_query=None,
        description="",
    ):
        """Creates new dataset. Requires client authentication.

        Returns
        -------
        dict
            created dataset attributes
        """
        namespace, name = did.split(":", 1)
        params = {
            "namespace": namespace,
            "name": name,
            "frozen": frozen,
            "monotonic": monotonic,
            "metadata": metadata or {},
            "metadata_requirements": metadata_requirements or None,
            "description": description or "",
            "files_query": files_query or None,
            "subsets_query": subsets_query or None,
        }
        return await self.post_json("data/create_dataset", params)

    async def add_child_dataset(self, parent_spec, child_spec):
        """Adds a child dataset to a dataset.

        Arguments
        ---------
        parent_spec : str
            Parent namespace, name ("namespace:name")
        child_spec : str
            Child namespace, name ("namespace:name")
        """
        return await self.get_text(
            f"data/add_child_dataset?parent={parent_spec}&child={child_spec}"
        )

    async def remove_child_dataset(self, parent_spec, child_spec):
        """Removes a child dataset from a dataset.

        Arguments
        ---------
        parent_spec : str
            Parent namespace, name ("namespace:name")
        child_spec : str
            Child namespace, name ("namespace:name")
        """
        return await self.get_text(
            f"data/remove_child_dataset?parent={parent_spec}&child={child_spec}"
        )

    @staticmethod
    def file_specs(file_list, default_namespace=None):
        lst = []
        for f in file_list:
            spec = ObjectSpec.from_dict(f, default_namespace)
            spec.validate()
            lst.append(spec.as_dict())
        return lst

    async def add_files(self, dataset, file_list=None, namespace=None, query=None):
        """Add existing files to an existing dataset. Requires client authentication.

        Arguments
        ---------
        dataset : str
            "namespace:name" or "name", if namespace argument is given
        query : str
            MQL query to run and add files matching the query
        file_list : list
            List of dictionaries, one dictionary per file, each with "fid", or "namespace" and "name", or "did"
        namespace : str, optional
            Default namespace.

        Returns
        -------
        int
            number of files added to the dataset

        Notes
        -----
        Either ``file_list`` or ``query`` must be specified, but not both
        """
        default_namespace = namespace
        if ":" not in dataset:
            if default_namespace is None:
                raise ValueError("Namespace not specified for the target dataset")
            dataset = f"{default_namespace}:{dataset}"

        if (file_list is None) == (query is None):
            raise ValueError("Either file_list or query must be specified, but not both")

        params = {"namespace": namespace}
        if file_list is not None:
            params["file_list"] = self.file_specs(file_list, default_namespace)
        elif query:
            params["query"] = query
        else:
            raise ValueError("Either file_list or query must be specified, but not both")

        out = await self.post_json(f"data/add_files?dataset={dataset}", params)
        return out["files_added"]

    async def remove_files(self, dataset, file_list=None, namespace=None, query=None):
        """Remove files from a dataset. Requires client authentication.

        Arguments
        ---------
        dataset : str
            "namespace:name" or "name", if namespace argument is given
        query : str
            MQL query to run and remove files matching the query
        file_list : list
            List of dictionaries, one dictionary per file, each with "fid", or "namespace" and "name", or "did"
        namespace : str, optional
            Default namespace.

        Returns
        -------
        int
            actual number of files removed from the dataset

        Notes
        -----
        Either ``file_list`` or ``query`` must be specified, but not both
        """
        default_namespace = namespace
        dataset_spec = ObjectSpec(dataset, namespace=default_namespace)

        if (file_list is None) == (query is None):
            raise ValueError("Either file_list or query must be specified, but not both")

        params = {
            "dataset_namespace": dataset_spec.Namespace,
            "dataset_name": dataset_spec.Name,
            "namespace": namespace,
        }
        if file_list is not None:
            params["file_list"] = self.file_specs(file_list, default_namespace)
        elif query:
            params["query"] = query
        else:
            raise ValueError("Either file_list or query must be specified, but not both")

        out = await self.post_json("data/remove_files", params)
        return out["files_removed"]

    async def remove_dataset(self, dataset):
        """Remove a dataset. Requires client authentication.

        Arguments
        ---------
        dataset : str
            "namespace:name"
        """
        return await self.get_text(f"data/remove_dataset/{dataset}")

    async def declare_file(
        self,
        did=None,
        namespace=None,
        name=None,
        auto_name=None,
        dataset_did=None,
        dataset_namespace=None,
        dataset_name=None,
        size=0,
        metadata={},
        fid=None,
        parents=[],
        checksums={},
        dry_run=False,
    ):
        """Declare new file and add it to the dataset. Requires client authentication.
        See MetaCatClient.declare_file for full argument descriptions.

        Returns
        -------
        dict
            dictionary with file name, namespace and file id. Names and file ids will be auto-generated as necessary.
        """
        if not did:
            if not namespace:
                raise ValueError("Unspecified file namespace")
            if not name and not auto_name:
                raise ValueError("Unspecified file name")
        else:
            namespace, name = undid(did)
        if not (dataset_namespace and dataset_name) and not dataset_did:
            raise ValueError(
                "Either dataset_did or dataset_namespace and dataset_name must be provided"
            )
        if dataset_did is None:
            dataset_did = f"{dataset_namespace}:{dataset_name}"
        info = dict(
            namespace=namespace,
            name=name,
            size=size,
            checksums=checksums,
            fid=fid,
            parents=parents,
            metadata=metadata,
        )
        if not name and auto_name:
            info["auto_name"] = auto_name
        return (await self.declare_files(dataset_did, [info], dry_run=dry_run))[0]

    async def declare_files(self, dataset, files, namespace=None, dry_run=False, as_required=None):
        """Declare new files and add them to an existing dataset. Requires client authentication.
        See MetaCatClient.declare_files for the file dictionary format.

        Arguments
        ---------
        dataset : str
            "namespace:name"
        files : list or dict
            List of dictionaries, one dictionary per a file to be declared, or a single dictionary.
        namespace: str, optional
            Default namespace for files to be declared
        dry_run : boolean
            If true, run all the necessary checks but stop short of actual file declaraion or adding to a dataset.
        as_required : str or None
            If set, files which already exist are updated instead ("unretire" also un-retires them)

        Returns
        -------
        list
            list of dictionaries, one dictionary per file with file ids: { "fid": "..." }
        """
        default_namespace = namespace
        if isinstance(files, dict):
            files = [files]  # convenience

        lst = []
        for i, item in enumerate(files):
            f = item.copy()
            namespace = f.get("namespace", default_namespace)
            if "did" in f:
                if "name" in f or "namespace" in f:
                    raise ValueError(f"Both DID and namespace/name specified for {f['did']}")
                did = f.pop("did")
                namespace, name = parse_name(did, default_namespace)
                f["name"] = name
            f["namespace"] = namespace
            size = f.get("size")
            if not isinstance(size, int) or size < 0:
                raise ValueError(f"File size is unspecified or invalid for file #{i} in the list")

            meta = item.get("metadata", {})
            for k in meta.keys():
                if "." not in k:
                    raise ValueError(
                        f'Invalid metadata key "{k}" for file #{i} in the list: metadata key must contain dot (.)'
                    )

            f["metadata"] = meta
            lst.append(f)

        url = f"data/declare_files?dataset={dataset}"
        if dry_run:
            url += "&dry_run=yes"
        try:
            out = await self.post_json(url, lst)
        except AlreadyExistsError as e:
            if not as_required:
                raise
            existing_files = [s.strip() for s in e.Message.split("\n")][1:]
            to_declare = []
            out = []
            for l in lst:
                if f"{l['namespace']}:{l['name']}" in existing_files:
                    if as_required == "unretire":
                        await self.retire_file(
                            name=l["name"], namespace=l["namespace"], retire=False
                        )
                    out.append(await self.update_file(**l))
                else:
                    to_declare.append(l)

            # any that weren't in the error, still need to be declared
            if to_declare:
                out = out + await self.post_json(url, to_declare)
        return out

    async def move_files(self, namespace, file_list=None, query=None):
        """
        Arguments
        ---------
        namespace : str
            namespace to move files to
        query : str
            MQL query to run and move files matching the query
        file_list : list
            List of dictionaries, one dictionary per file, each with "fid", or "namespace" and "name", or "did"

        Returns
        -------
        tuple
            number of files moved, list of errors, number of errors
        """
        params = {"namespace": namespace}
        if file_list is not None:
            params["files"] = self.file_specs(file_list)
        elif query:
            params["query"] = query
        else:
            raise ValueError("Either file_list or query must be specified, but not both")

        out = await self.post_json("data/move_files", params)
        errors = out.get("errors", [])
        return out["files_moved"], errors, out.get("nerrors", len(errors))

    async def update_file(
        self,
        did=None,
        namespace=None,
        name=None,
        fid=None,
        replace=False,
        size=None,
        checksums=None,
        parents=None,
        children=None,
        metadata=None,
    ):
        """
        Arguments
        ---------
        did : str
            file "namespace:name"
        fid : str
            file id
        namespace : str
            file namespace
        name : str
            file name
        replace : bool
            If True, the specified attribute values will be replaced with new values.
            Otherwise added (for parents and children) and updated (for checksums and metadata)
        size : int >= 0
            file size, optional
        checksums : dict
            checksum values, optional
        parents : list
            list of parent file ids, optional
        children : list
            list of child file ids, optional
        metadata : dict
            dictionary with metadata to update or replace, optional

        Returns
        -------
        dict
            Dictionary with updated file information
        """
        data = {"mode": "replace" if replace else "add-update"}

        if fid:
            data["fid"] = fid
        else:
            if did:
                namespace, name = did.split(":", 1)
            assert namespace and name
            data["namespace"] = namespace
            data["name"] = name

        if size is not None:
            assert isinstance(size, int) and size >= 0
            data["size"] = size

        if checksums is not None:
            assert isinstance(checksums, dict)
            data["checksums"] = checksums

        if parents is not None:
            assert isinstance(parents, list)
            data["parents"] = [ObjectSpec(p).as_dict() for p in parents]

        if children is not None:
            assert isinstance(children, list)
            data["children"] = [ObjectSpec(c).as_dict() for c in children]

        if metadata is not None:
            assert isinstance(metadata, dict)
            data["metadata"] = metadata

        return await self.post_json("data/update_file", data)

    async def update_file_meta(
        self, metadata, files=None, names=None, fids=None, namespace=None, dids=None, mode="update"
    ):
        """Updates metadata for existing files. Requires client authentication.

        **DEPRECATED** *update_file() should be used instead*

        See MetaCatClient.update_file_meta for argument descriptions.

        Returns
        -------
        list
            list of dictionaries, one dictionary per file with file ids: { "fid": "..." }
        """
        if names and not namespace:
            raise ValueError("Namespace must be specified with names argument")

        def combined():
            for name in names or []:
                yield ObjectSpec(namespace, name).as_dict()
            for did in dids or []:
                spec = ObjectSpec(did)
                spec.validate()  # will raise ValueError
                yield spec.as_dict()
            for fid in fids or []:
                yield ObjectSpec(fid=fid).as_dict()
            for item in files or []:
                spec = ObjectSpec.from_dict(item)
                spec.validate()  # will raise ValueError
                yield spec.as_dict()

        out = []
        for chunk in chunked(combined(), 1000):
            data = {"metadata": metadata, "files": chunk, "mode": mode}
            out.extend(await self.post_json("data/update_file_meta", data))
        return out

    async def delete_file(self, did=None, namespace=None, name=None, fid=None):
        """Delete an existing file. The file will be removed from all datasets and the database and its name and file id can be reused.

        Arguments
        ---------
        did : str
            file "namespace:name"
        fid : str
            file id
        namespace : str
            file namespace
        name : str
            file name
        """
        data = {}
        if fid:
            data["fid"] = fid
        else:
            if did:
                namespace, name = did.split(":", 1)
            assert namespace and name
            data["namespace"] = namespace
            data["name"] = name
        return await self.post_json("data/delete_file", data)

    async def retire_file(self, did=None, namespace=None, name=None, fid=None, retire=True):
        """Modify retired status of the file. See MetaCatClient.retire_file.

        Arguments
        ---------
        did : str
            file "namespace:name"
        fid : str
            file id
        namespace : str
            file namespace
        name : str
            file name
        retire : bool
            whether the file should be retired

        Returns
        -------
        dict
            Dictionary with updated file information
        """
        data = {"retire": retire}
        if fid:
            data["fid"] = fid
        else:
            if did:
                namespace, name = did.split(":", 1)
            assert namespace and name
            data["namespace"] = namespace
            data["name"] = name
        return await self.post_json("data/retire_file", data)

    async def update_dataset(
        self, dataset, metadata=None, mode="update", frozen=None, monotonic=None, description=None
    ):
        """Update dataset. Requires client authentication.

        Arguments
        ---------
        dataset : str
           "namespace:name"
        metadata : dict or None
            New metadata values, or, if None, leave the metadata unchanged
        mode: str
            Either ``"update"`` or ``"replace"``. Ignored if ``metadata`` is None
        frozen: boolean or None
            if boolean, new value for the flag. If None, leave it unchanged
        monotonic: boolean or None
            if boolean, new value for the flag. If None, leave it unchanged
        description: str or None
            if str, new dataset description. If None, leave the description unchanged

        Returns
        -------
        dict
            dictionary with new dataset information
        """
        request_data = {}
        if metadata is not None:
            request_data["mode"] = mode
            request_data["metadata"] = metadata
        if frozen is not None:
            request_data["frozen"] = frozen
        if monotonic is not None:
            request_data["monotonic"] = monotonic
        if description is not None:
            request_data["description"] = description
        return await self.post_json(f"data/update_dataset?dataset={dataset}", request_data)

    async def get_files(self, lookup_list, with_metadata=True, with_provenance=True):
        """Get many file records

        Arguments
        ---------
        lookup_list : list
            List of dictionaries, one dictionary per file. Each dictionary must have either
                "did":"namespace:name", or
                "namespace":"..." and "name":"..." or
                "fid":"file id"
        with_metadata : boolean
            whether to include file metadata
        with_provenance:
            whether to include parents and children list

        Returns
        -------
        List of file records, each record is the same as returned by get_file()
        """
        with_metadata = "yes" if with_metadata else "no"
        with_provenance = "yes" if with_provenance else "no"

        new_list = []
        for item in lookup_list:
            if "fid" in item or "namespace" in item and "name" in item:
                pass
            elif "did" in item:
                did = item["did"]
                try:
                    namespace, name = did.split(":", 1)
                except ValueError:
                    raise ValueError("Invalid DID format: " + did)
                item = {"namespace": namespace, "name": name}
            else:
                raise ValueError("Invalid file specifification: " + str(item))
            new_list.append(item)

        url = "data/files?with_metadata=%s&with_provenance=%s" % (with_metadata, with_provenance)
        return await self.post_json(url, new_list)

    async def get_file(
        self,
        name=None,
        namespace=None,
        fid=None,
        did=None,
        with_metadata=True,
        with_provenance=True,
        with_datasets=False,
    ):
        """Get one file record

        Arguments
        ---------
        fid : str, optional
            File id
        name : str, optional
        namespace : str, optional
            name and namespace must be specified together
        did : str, optional
            "nemaspace:name"
        with_metadata : boolean
            whether to include file metadata
        with_provenance : boolean
            whether to include parents and children list
        with_datasets : boolean
            whether to include the list of datasets the file is in

        Returns
        -------
        dict
            dictionary with file information or None if the file was not found
        """
        assert (
            (fid is not None) or (did is not None) or (name is not None and namespace is not None)
        ), "Either DID or file id or namespace and name must be specified"
        with_meta = "yes" if with_metadata else "no"
        with_rels = "yes" if with_provenance else "no"
        with_datasets = "yes" if with_datasets else "no"
        url = f"data/file?with_metadata={with_meta}&with_provenance={with_rels}&with_datasets={with_datasets}"
        if did:
            namespace, name = parse_name(did, None)
            assert namespace is not None, f"Invalid DID format: {did}"
        if name:
            url += f"&name={name}&namespace={namespace}"
        else:
            url += f"&fid={fid}"

        try:
            return await self.get_json(url)
        except NotFoundError:
            return None

    async def query(
        self,
        query,
        namespace=None,
        with_metadata=False,
        with_provenance=False,
        save_as=None,
        add_to=None,
        include_retired_files=False,
        summary=None,
        batch_size=0,
    ):
        """Run file query. Requires client authentication if save_as or add_to are used.

        Arguments
        ---------
        query : str
            Query in MQL
        namespace : str
            default namespace for the query
        include_retired_files:
            boolean, whether to include retired files into the query results, default=False
        with_metadata : boolean
            whether to return file metadata
        with_provenance : boolean
            whether to return parents and children list
        save_as : str
            namespace:name for a new dataset to create and add found files to
        add_to : str
            namespace:name for an existing dataset to add found files to
        summary : str or None
            "count" - return [{"count": n, "total_size": nbytes }]
            "keys" - return list of list of all top level metadata keys for the selected files
            ``summary`` can not be used together with ``save_as`` or ``add_to``
        batch_size : int
            if > 0, run the query in batches of this many files

        Yields
        ------
        async generator
            one dictionary per file, or a single summary object if ``summary`` is used
        """
        assert not (
            summary is not None and (add_to or save_as)
        ), "Summary can not be used together with add_to or save_as"
        assert summary in ("count", "keys", None)

        trimquery = query[:255]
        if summary:
            url = f"data/query?summary={summary}"
            if namespace:
                url += f"&namespace={namespace}"
            if include_retired_files:
                url += "&include_retired_files=yes"
            url += f"&trimquery={trimquery}"
            yield await self.post_json(url, query)
            return

        url = "data/query?with_meta=%s&with_provenance=%s" % (
            "yes" if with_metadata else "no",
            "yes" if with_provenance else "no",
        )
        if namespace:
            url += f"&namespace={namespace}"
        if save_as:
            url += f"&save_as={save_as}"
        if add_to:
            url += f"&add_to={add_to}"
        if include_retired_files:
            url += "&include_retired_files=yes"
        url += f"&trimquery={trimquery}"
        if batch_size > 0:
            offset = 0
            count = batch_size
            while count == batch_size:
                batch_query = f"({query}) ordered skip {offset} limit {batch_size}"
                count = 0
                async for item in self.iter_json("post", url, batch_query):
                    count += 1
                    yield item
                offset += batch_size
        else:
            async for item in self.iter_json("post", url, query):
                yield item

    def async_query(self, query, data=None, **args):
        """Run the query concurrently as an asyncio Task. Must be called with an event loop running.

        Arguments
        ---------
        query : str
            Query in MQL
        data : anything
            Arbitrary data associated with this query
        args :
            Same keyword arguments as for the query() method

        Returns
        -------
        asyncio.Task
            Awaiting the task returns the list of query results. The task has a ``Data`` attribute containing
            the object passed as the ``data`` argument. At most ``max_concurrent_queries`` run at once.
        """

        async def run():
            async with self.semaphore:
                return [item async for item in self.query(query, **args)]

        task = asyncio.ensure_future(run())
        task.Data = data
        self.PendingQueries.add(task)
        task.add_done_callback(self.PendingQueries.discard)
        return task

    async def wait_queries(self):
        """
        Wait for all issued asynchronous queries to complete
        """
        if self.PendingQueries:
            await asyncio.gather(*list(self.PendingQueries), return_exceptions=True)

    async def search_named_queries(self, query):
        """
        Run MQL query for named queries

        Arguments
        ---------
        query : str
            Query in MQL

        Returns
        -------
        list of dicts
            The list contains one dictionary per matching named query with the query information.
        """
        return await self.post_json("data/search_queries", query)

    async def create_namespace(self, name, owner_role=None, description=None):
        """Creates new namespace. Requires client authentication.

        Arguments
        ---------
        name : str
            Namespace name
        owner_role : str
            Owner role for the new namespace. The user must be a member of the role.
            Optional. If unspecified, the new namespace will be owned by the user.
        description : str
            New namespace description

        Returns
        -------
        dict
            New namespace information
        """
        url = f"data/create_namespace?name={name}"
        if owner_role:
            url += f"&owner_role={owner_role}"
        if description:
            url += f"&description={quote_plus(description)}"
        return await self.get_json(url)

    async def get_namespace(self, name):
        """Get information about a namespace

        Arguments
        ---------
        name : str
            Namespace name

        Returns
        -------
        dict
            Namespace information or None if the namespace was not found
        """
        try:
            return await self.get_json(f"data/namespace?name={name}")
        except NotFoundError:
            return None

    async def get_namespaces(self, names):
        """Get information for multiple namespaces

        Arguments
        ---------
        names : list of str
            Namespace names

        Returns
        -------
        list
            Namespace information
        """
        return await self.post_json("data/namespaces", names)

    async def list_namespaces(
        self, pattern=None, owner_user=None, owner_role=None, directly=False
    ):
        """List namespaces

        Arguments
        ---------
        pattern : str
            Optional fnmatch style pattern to filter namespaces by name
        owner_user : str
            Optional, return only namespaces owned by the specified user
        directly : boolean
            If False and owner_user is specified, return also namespaces owned by all roles the user is in
            Ignored if owner_user is not specified
        owner_role : str
            Optional, return only namespaces owned by the specified role.
            Ignored if owner_user is also specified

        Yields
        ------
        async generator
            dictionaries with namespace information sorted by the namespace name
        """
        args = ""
        if owner_user:
            args += f"owner_user={owner_user}"
            if directly:
                args += "&directly=yes"
        if owner_role:
            if args:
                args += "&"
            args += f"owner_role={owner_role}"
        if args:
            args = "?" + args
        for item in await self.get_json("data/namespaces" + args):
            if pattern is None or fnmatch.fnmatch(item["name"], pattern):
                yield item

    #
    # Categories
    #
    async def list_categories(self, root=None):
        """List categories

        Arguments
        ---------
        root : str
            Optional, if present, list only categories under the root

        Returns
        -------
        list
            List of dictionaries with category information sorted by category path
        """
        lst = await self.get_json("data/categories")
        if root:
            if not root.endswith("."):
                root += "."
            lst = [cat for cat in lst if cat["path"].startswith(root)]
        return sorted(lst, key=lambda c: c["path"])

    async def get_category(self, path):
        """Get category information

        Arguments
        ---------
        path : str - category path

        Returns
        -------
        dict
            A dictionary with category information or None if not found
        """
        return await self.get_json(f"data/category/{path}")

    async def create_category(
        self,
        path=None,
        owner_role=None,
        restricted=False,
        required=False,
        description=None,
        definitions=None,
    ):
        """Create a category

        Arguments
        ---------
        path: str - category path
        owner_role: str, optional - owner or owner role of category
            if unspecified, the new category will be owned by the user
        restricted: bool - whether a category is restricted (cannot add extra fields to metadata), default False
        required: bool - whether a category is required (required field(s) must be present in metadata), default False
        description: str - description of category
        definitions: dict - parameter definitions

        Returns
        -------
        dict
            A dictionary with created category attributes
        """
        params = {
            "path": path,
            "owner_role": owner_role,
            "restricted": restricted,
            "required": required,
            "description": description,
            "definitions": definitions,
        }
        return await self.post_json("data/create_category", params)

    async def update_category(
        self,
        path=None,
        owner_role=None,
        restricted=None,
        required=None,
        description=None,
        definitions=None,
        mode="update",
    ):
        """Update a category

        Arguments
        ---------
        path: str - category path
        owner_role: str, optional - owner or owner role of category
        restricted: bool, optional - whether a category is restricted (cannot add extra fields to metadata)
        required: bool, optional - whether a category is required (required field(s) must be present in metadata)
        description: string, optional - description of category
        definitions: dict, optional - parameter definitions
        mode: str, optional - "update" to merge, "replace" to overwrite (default: "update")

        Returns
        -------
        dict
            A dictionary with updated category attributes
        """
        params = {
            "path": path,
            "owner_role": owner_role,
            "restricted": restricted,
            "required": required,
            "description": description,
            "definitions": definitions,
            "mode": mode,
        }
        return await self.post_json("data/update_category", params)

    async def remove_category(self, path):
        """Remove a category

        Arguments
        ---------
        path : str - category path
        """
        return await self.get_text(f"data/remove_category/{path}")

    #
    # Named queries
    #
    async def get_named_query(self, namespace, name):
        """Get named query

        Arguments
        ---------
        namespace : str
        name : str

        Returns
        -------
        dict or None
            A dictionary with information about the named query or None if the named query does not exist.
        """
        try:
            return await self.get_json(f"data/named_query?namespace={namespace}&name={name}")
        except NotFoundError:
            return None

    async def list_named_queries(self, namespace=None):
        """Get multiple named queries

        Arguments
        ---------
        namespace : str
            optional, if specified the list will include all named queries in the namespace. Otherwise all named queries will be returned

        Returns
        -------
        list
            List of dictionaries with information about the named queries.
        """
        url = "data/named_queries"
        if namespace is not None:
            url += f"?namespace={namespace}"
        return await self.get_json(url)

    async def create_named_query(self, namespace, name, source, parameters=[], update=False):
        """
        Arguments
        ---------
        namespace: str
        name: str
             Namespace and name to store query under
        source:
             Query text
        parameters:
             Values to substitute into query
        update: bool
             Update the exising named query

        Returns
        -------
             JSON dump of data used to create query
        """
        data = dict(namespace=namespace, name=name, source=source, parameters=parameters)
        url = "data/create_named_query"
        if update:
            url += "?update=yes"
        return await self.post_json(url, data)

    async def report_metadata_keys(self):
        """
        Returns
        -------
            JSON List of all metadata dictionary keys in database
        """
        return await self.get_json("data/report_metadata_keys")

    async def report_metadata_counts_ranges(self, keylist):
        """
        Arguments
        ---------
            keylist: list of str
                metadata keys

        Returns
        -------
            JSON dictionary 3 values for each category and key in the input.

        .. code-block:: python

            { "cat.key.min": value, "cat.key.max": value, "cat.key.count": n, ...  }
        """
        return await self.get_json(
            "data/report_metadata_counts_ranges?keylist=%s" % ",".join(keylist)
        )

    async def report_metadata_values(self, key, *args, **kwargs):
        """
        Arguments
        ---------
            key: str
                Metadata key
        Returns
        -------
            JSON list of all distinct values for key in the various metadata dictionaries.
        """
        return await self.get_json(f"data/report_metadata_values?key={key}")
