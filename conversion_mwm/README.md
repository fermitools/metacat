
This is my attempt at a different migration approach, to wit:

* Make views on a SAM database that directly generate the desired MetaCat and
  DataDispatcher table data
* Have 2 psql clients running, one dumping the SAM database with those views and
  piped into one loading the generated data into the Metacat /  DataDispatcher
  databases.
* Will of course have to generate the tables in dependency order

The view queries are a bit hair-raising looking (especially the metadata generation)
but seem to actually work okay (in samdev, at least).

Have so far done users, roles, files and parent_child as far as views.

migration then goes like:

```
psql_sam < views.sql
for table in users roles namespaces files parent_child
do
    psql_sam  -c "copy (select * from meta_${table}) TO stdout;" | 
       psql_meta -c "copy ${table} from stdin"  
done
```
