import re
from wsdbtools import ConnectionPool
from condb import ConDB
from metacat.filters import MetaCatFilter

class RunsDBinConDB(MetaCatFilter):
    """
    Inputs: Single file set

    Positional parameters: none

    Keyword parameters: none

    Description: Uses core.runs[0], if present, to get the run number and then retrieves the runs history data from ConDB for the run and
        attaches the data to the file metadata under configured category. Database timestamps are converted to floating point timestamps.
        If the file does not have a run number associated with it (core.runs[] is missing from the file metadata) or there is no data
        for the run in the runs history database, then the file metadata is unchanged and will not contain the runs history fields.

    Configuration:
        connectio: Runs history Posrgres connection string "host=... port=... user=... dbname=..."
        folder: ConDB folder name as "name" or "namespace.name"
        meta_prefix: Metadata category prefix to use when appending the Runs history data to the MetaCat file metadata. Default "runs_history"
    """

    def __init__ (self, config):
        self.Config = config
        show_config = config.copy()
        show_config["connection"] = self.hide(show_config["connection"], "user", "password")
        MetaCatFilter.__init__(self, show_config)
        self.Connection = self.Config["connection"]
        self.ConnPool = ConnectionPool(postgres=self.Connection, max_idle_connections=1)
        self.FolderName = self.Config["folder"]
        self.MetaPrefix = self.Config.get("meta_prefix", "runs_history")
        
        #
        # get column names
        #
        
        db = ConDB(self.ConnPool)
        folder = db.openFolder(self.FolderName)
        self.ColumnTypes = folder.data_column_types() if folder is not None else None

    def hide(self, conn, *fields):
        for f in fields:
             conn = re.sub(f"\s+{f}\s*=\s*\S+", f" {f}=(hidden)", conn, re.I)
        return conn
        
    def file_run_number(self, metadata):
        file_runs = metadata.get("core.runs")
        if file_runs:
            return file_runs[0]
        else:
            return None

    def filter(self, inputs, **ignore):

        # Conect to db via condb python API
        db = ConDB(self.ConnPool)
        folder = db.openFolder(self.FolderName)
        
        data_by_run = {}        # cache data by run number across chunks

        # Get files from metacat input
        file_set = inputs[0]
        for chunk in file_set.chunked():
            need_run_nums = set()

            for f in chunk:
                runnum = self.file_run_number(f.Metadata)
                if runnum is not None and runnum not in data_by_run:
                    need_run_nums.add(runnum)

            if need_run_nums:
                # Get run_hist data
                data_runhist = folder.getData(min(need_run_nums), t1=max(need_run_nums))
                for row in data_runhist:
                    runnum, data = row[1], row[4:]
                    if runnum not in data_by_run:
                        data_by_run[runnum] = data
        
            # Insert run hist data to Metacat
            for f in chunk:
                runnum = self.file_run_number(f.Metadata)
                if runnum is not None and runnum in data_by_run:
                    for (col, typ), value in zip(self.ColumnTypes, data_by_run[runnum]):
                        if typ.startswith("timestamp") and value is not None:
                            value = value.timestamp()
                        f.Metadata[f"{self.MetaPrefix}.{col}"] = value

            yield from chunk
 

def create_filters(config):
    return {
        "dune_runshistdb": RunsDBinConDB(config)
    }
