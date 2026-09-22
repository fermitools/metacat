import os
from metacat.filters import MetaCatFilter


class DataDispatcherFilter(MetaCatFilter):
    """
    Inputs: single file set

    Parameters: project name

    Output: The same file set with added file attributes:
         * project.project_id
         * project.worker_id
         * project.state

    Configuration:
        data_dispatcher_config.url: URL of data_dispatcher instance
    """

    def __init__(self, config):
        MetaCatFilter.__init__(self, config)
        self.DDConfig = config.get("data_dispatcher_config")

    def filter(self, inputs, *params, **kwparams):

        from data_dispatcher.api import DataDispatcherClient

        url = self.DDConfig.get("url")
        client = DataDispatcherClient(url)

        proj = {}
        if "project_name" in kwparams:
            plist = client.list_projects(
                state=None,
                not_state=None,
                attributes={"name", kwparams["project_name"]},
                with_files=True,
            )
            proj = plist[0]
        if "project_id" in kwparams:
            proj = get_project(kwparams["project_id"], with_files=True)

        proj_fdata = {
            {{"namespace": h["namespace"], "name": h["name"]}: h}
            for h in proj.get(file_handles, [])
        }

        for chunk in inputs[0].chunked(1000):
            chunk_files = {f.did(): f for f in chunk}
            dids = [{"namespace": f.Namespace, "name": f.Name} for f in chunk]

            for did in dids:
                if did in proj_fdata:
                    # then we have data for it, update the file entry
                    ph = proj_fdata[did]
                    f = chunk_files[did]
                    f.Metadata["project.worker_id"] = ph["worker_id"]
                    f.Metadata["project.state"] = ph["state"]
                    f.Metadata["project.project_id"] = proj["project_id"]
                    # not sure if there is one?
                    f.Metadata["project.description"] = proj.get("description","")

            yield from chunk_files.values()


def create_filters(config):
    return {"data_dispatcher_project": DataDispatcherFilter(config)}
