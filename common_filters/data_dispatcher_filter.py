import os
import sys
from metacat.filters import MetaCatFilter
import logging

logger = logging.getLogger(__name__)


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
        self.url = config.get("url")

    def filter(self, inputs, *params, **kwparams):

        from data_dispatcher.api import DataDispatcherClient

        client = DataDispatcherClient(self.url)

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
            proj = client.get_project(kwparams["project_id"], with_files=True)

        proj_fdata = {}
        for h in proj.get("file_handles", []):
            didstr = f"{h['namespace']}:{h['name']}"
            proj_fdata[didstr] = h

        for f in inputs[0]:

            did = f.did()

            if did in proj_fdata:

                # then we have data for it, update the file entry
                ph = proj_fdata[did]
                f.Metadata["project.worker_id"] = ph["worker_id"]
                f.Metadata["project.state"] = ph["state"]
                f.Metadata["project.project_id"] = proj["project_id"]
                # not sure if there is one?
                f.Metadata["project.description"] = proj.get("description", "")

            yield f


def create_filters(config):
    return {"data_dispatcher_project": DataDispatcherFilter(config)}
