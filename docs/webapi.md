# MetaCat API

## Creating MetaCat Client Interface

`MetaCatClient` object is the client side interface to MetaCat server.
To create a `MetaCatClient`, you need the URL for the server:

``` python
from metacat.webapi import MetaCatClient

client = MetaCatClient("http://host.domain:8080/metacat/instance")
```

For client authentication, the `MetaCatClient` object uses the token
created by calling one of its login methods or using CLI. To get a token
using CLI, use `metacat auth login` command:

``` shell
$ metacat auth login alice
Password:...
```

Not all client methods require any client authentication. Most of
read-only methods can be used without any authentication. Authentication
is required to get information about users and roles (these methods are
not yet implemented).

## MetaCatClient Class Methods Reference

<div class="autoclass" members="" noindex="" inherited-members="">

[Here](../../..//wiki/Webapi-pydoc-page#MetaCatClient)

</div>

## Exceptions

MetaCatClient methods can raise the following exceptions:

-   `metacat.webapi.InvalidArgument` - method was called with an invalid
    argument
-   `metacat.webapi.NotFoundError` - an object is not found in the
    database
-   `metacat.webapi.BadRequestError` - invalid request
-   `metacat.webapi.AlreadyExistsError` - an object already exists in
    the database
-   `metacat.webapi.PermissionDeniedError` - permission denied
-   `metacat.webapi.InvalidMetadataError` - metadata validation failed

All these exceptions inherit from `metacat.webapi.WebAPIError` class.
`WebAPIError.__str__` can be used to get humanly readable description of
the exceptions, e.g.:

``` python
try:
  results = metacat_client.method(...)
except metacat.webapi.WebAPIError as e:
  print(e)
```

## Asynchronous Queries

When you need to run multiple queries, you can use the `async_query` to
run them concurrently by starting them asynchronously and then waiting
for their results:

``` python
client = MetaCatClient(url)

datasets = [ "production:A", "production:B" ]

promises = []
for dataset_name in datasets:
    query = f"files from {dataset_name} where created_timestamp > '2020-10-10'"
    promise = client.async_query(query, dataset_name)
    promises.append(promise)

for promise in promises:
    results = promise.wait()
    n = len(results)
    dataset_name = promise.Data
    print(f"Dataset {dataset_name}: {n} files")
```

In this example, we start 2 queries concurrently. Each will get files
from its own dataset. When we start the asynchronous queries, instead of
query results, the client object returns `promises`. Promise is an
object, on which you can wait for actual results. Also, we pass the
dataset name to the `async_query` method as the `data` argument to be
able to associate the returned results with the dataset.

In the second for-loop, we wait for the results from each query and use
the promise `Data` attribute to refer to the actual datset name to print
the results. Note that the second for-loop loops through the promises in
the same order as they were created. But that does not mean that we
expect the queries to complete in the same order. If the query completes
before we call the corresponding promise `wait` method, it will simply
return the results immediately.

Another way to wait for all asynchronous queries to complete is to call
`wait_queries` method of the `MetaCatClient`:

``` python
client = MetaCatClient(url)

datasets = [ "production:A", "production:B" ]
promises = {}

for dataset_name in datasets:
    query = f"files from {dataset_name} where created_timestamp > '2020-10-10'"
    promise = client.async_query(query, None)
    promises[dataset_name] = promise

client.wait_queries()

for dataset_name, promise in promises.items():
    results = promise.wait()
    n = len(results)
    print(f"Dataset {dataset_name}: {n} files")
```

The `wait_queries` method will block until all asynchronous queries
started by the client complete. In this case, calling `wait` method of
the promise is still necessary to get the results of each individual
query, but because we called `wait_queries` first, the `wait` method
will return results immediately without blocking.
