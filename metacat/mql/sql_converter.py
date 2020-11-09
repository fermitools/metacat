from .trees import Ascender, Node
from metacat.db import DBFileSet, alias, limited, MetaExpressionDNF
from .meta_evaluator import MetaEvaluator

class SQLConverter(Ascender):
    
    def __init__(self, db, filters, debug=False):
        self.DB = db
        self.Filters = filters
        self.Debug = debug
        
    def columns(self, t, with_meta=True, with_provenance=True):
        meta = f"{t}.metadata" if with_meta else "null as metadata"
        if with_provenance:
            parents = f"{t}.parents"
            children = f"{t}.children"
        else:
            parents = "null as parents"
            children = "null as children"
        return f"{t}.id, {t}.namespace, {t}.name, {meta}, {parents}, {children}"
        
    def debug(self, *params, **args):
        parts = ["SQLConverter:"]+list(params)
        if self.Debug:
            print(*parts, **args)
            
    def convert(self, tree):
        result = self.walk(tree)
        if result.T == "sql":
            self.debug("sql:---------\n", result["sql"], "\n-------------")
        return self.node_to_file_set(result)
        
    def node_to_file_set(self, node):
        if node.T == "sql":
            file_set = DBFileSet.from_sql(self.DB, node["sql"])
        else:
            file_set = node["file_set"]
        return file_set
        
    def meta_filter(self, node, query=None, meta_exp=None, with_meta=False, with_provenance=False):
        #print("meta_filter: args:", args)
        assert query.T in ("sql","file_set")        
        if meta_exp is not None:
            if query.T == "sql":
                t = alias("t")
                dnf = MetaExpressionDNF(meta_exp)
                where_sql = dnf.sql(t)
                if not where_sql:
                    return node
                columns = self.columns(t, with_meta, with_provenance)
                query_sql = query["sql"]
                sql = f"""
                    -- meta_filter {t}
                        select {columns} 
                        from (
                            {query_sql}
                        ) {t} where {where_sql} 
                    -- end of meta_filter {t}
                """
                return Node("sql", sql=sql)
            else:
                evaluator = MetaEvaluator()
                out = (f for f in self.node_to_file_set(query)
                        if evaluator(f.metadata(), meta_exp)
                )
                return Node("file_set", file_set = DBFileSet(self.DB, out))
        else:
            return query

    def basic_file_query(self, node, *args, query=None):
        sql = DBFileSet.sql_for_basic_query(query)
        self.debug("basic_file_query: sql: --------\n", sql, "\n--------")
        return Node("sql", sql=sql)
        
    def file_list(self, node, specs=None, with_meta=False, with_provenance=False):
        return Node("sql", sql=DBFileSet.sql_for_file_list(specs, with_meta, with_provenance))
    
    def union(self, node, *args):
        #print("Evaluator.union: args:", args)
        assert all(n.T in ("sql","file_set") for n in args)
        sqls = [n for n in args if n.T == "sql"]
        self.debug("sqls:")
        for sql in sqls:
            self.debug(sql.pretty())
        file_sets = [n for n in args if n.T == "file_set"]
        from_file_sets = DBFileSet.union(self.DB, [n["file_set"] for n in file_sets]) if file_sets else None
        u_parts = ["\n(\n%s\n)" % (n["sql"],) for n in sqls]
        u_sql = None if not sqls else "\nunion\n".join(u_parts)
        
        if not file_sets:
            return Node("sql", sql=u_sql)        
        elif not u_sql:
            return Node("file_set", file_set=from_file_sets)
        else:
            from_sql = DBFileSet.from_sql(self.DB, u_sql)
            return DBFileSet.union(self.DB, [from_sql, from_file_sets])


    def join(self, node, *args, **kv):
        #print("Evaluator.union: args:", args)
        assert all(n.T in ("sql","file_set") for n in args)
        sqls = [n for n in args if n.T == "sql"]
        file_sets = [n for n in args if n.T == "file_set"]
        from_file_sets = DBFileSet.union(self.DB, [n["file_set"] for n in file_sets]) if file_sets else None
        u_parts = ["\n(\n%s\n)" % (n["sql"],) for n in sqls]
        u_sql = None if not sqls else "\nintersect\n".join(u_parts)
        
        if not file_sets:
            return Node("sql", sql=u_sql)        
        elif not u_sql:
            return Node("file_set", file_set=from_file_sets)
        else:
            from_sql = DBFileSet.from_sql(self.DB, u_sql)
            Node("file_set", file_set = DBFileSet.join(self.DB, [from_sql, from_file_sets]))

    def minus(self, node, *args, **kv):
        #print("Evaluator.union: args:", args)
        assert len(args) == 2
        assert all(n.T in ("sql","file_set") for n in args)
        left, right = args
        if left.T == "sql" and right.T == "sql":
            s1 = left["sql"]
            s2 = right["sql"]
            sql = f"""({s1})\nexcept\n({s2})"""
            return Node("sql", sql=sql)
        else:
            left_set = left["file_set"] if left.T == "file_set" else DBFileSet.from_sql(self.DB, left["sql"])
            right_set = right["file_set"] if right.T == "file_set" else DBFileSet.from_sql(self.DB, right["sql"])
            Node("file_set", file_set = left_set - right_set)

    def parents_of(self, node, *args, with_meta=False, with_provenance=False):
        assert len(args) == 1
        arg = args[0]
        assert arg.T in ("sql","file_set")
        with_meta = node["with_meta"]
        with_provenance = node["with_provenance"]
        if arg.T == "sql":
            arg_sql = arg["sql"]
            p = alias("p")
            c = alias("c")
            pc = alias("pc")
            columns = self.columns(p, with_meta, with_provenance)
            if with_provenance:
                new_sql = f"""
                    -- parents of {p}
                        select {columns}
                        from files_with_provenance {p}
                            inner join parent_child {pc} on {p}.id = {pc}.parent_id
                            inner join ({arg_sql}) as {c} on {c}.id = {pc}.child_id
                    -- end of parents of {p}
                """
            else:
                new_sql = f"""
                    -- parents of {p}
                        select {columns}
                        from files {p}
                            inner join parent_child {pc} on {p}.id = {pc}.parent_id
                            inner join ({arg_sql}) as {c} on {c}.id = {pc}.child_id
                    -- end of parents of {p}
                """
            return Node("sql", sql=new_sql)
        else:
            return Node("file_set", file_set = arg["file_set"].parents(with_metadata=with_meta, with_provenance=with_provenance))

    def children_of(self, node, *args, with_meta=False, with_provenance=False):
        assert len(args) == 1
        arg = args[0]
        assert arg.T in ("sql","file_set")
        if arg.T == "sql":
            arg_sql = arg["sql"]
            p = alias("p")
            c = alias("c")
            pc = alias("pc")
            columns = self.columns(c, with_meta, with_provenance)
            if with_provenance:
                new_sql = f"""
                    -- children of {c}
                        select {columns}
                        from files_with_provenance {c}
                            inner join parent_child {pc} on {c}.id = {pc}.child_id
                            inner join ({arg_sql}) as {p} on {p}.id = {pc}.parent_id
                    -- end of children of {c}
                """
            else:
                new_sql = f"""
                    -- children of {c}
                        select {columns}
                        from files {c}
                            inner join parent_child {pc} on {c}.id = {pc}.child_id
                            inner join ({arg_sql}) as {p} on {p}.id = {pc}.parent_id
                    -- end of children of {c}
                """

            return Node("sql", sql=new_sql)
        else:
            return Node("file_set", file_set = arg["file_set"].children(with_metadata=with_meta, with_provenance=with_provenance))

    def limit(self, node, arg, limit=None):
        if limit is None:
            return arg
        if arg.T == "sql":
            sql = arg["sql"]
            tmp = alias()
            columns = self.columns(tmp)
            new_sql = f"""
                -- limit {limit} {tmp}
                    select {columns} 
                    from (
                        {sql}
                    ) {tmp} limit {limit} 
                -- end of limit {limit} {tmp}
            """
            return Node("sql", sql=new_sql)
        else:
            return Node("file_set", file_set = limited(arg["file_set"], limit))

    def filter(self, node, *queries, name=None, params=[]):
        #print("Evaluator.filter: inputs:", inputs)
        assert name is not None
        filter_function = self.Filters[name]
        queries = [self.node_to_file_set(q) for q in queries]
        return Node("file_set", file_set = DBFileSet(self.DB, filter_function(queries, params)))