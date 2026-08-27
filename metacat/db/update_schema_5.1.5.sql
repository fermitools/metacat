
create index concurrently files_retired_size on files ( id, retired, size );

alter table datasets add column total_file_size bigint;
