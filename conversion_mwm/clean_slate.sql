drop schema metacat cascade;
create schema metacat;
set search_path=metacat;
\i ../metacat/db/schema.sql
drop schema data_dispatcher cascade;
create schema data_dispatcher;
set search_path=data_dispatcher;
\i ../../data_dispatcher/schema.sql

