
-- views to build in SAM database to generate the Metacat table data
drop view if exists meta_users;
create view meta_users as 
  select 
    max(username) as username, 
    max(first_name||' '||last_name) as name, 
    max(email_address) as email, 
    '' as flags,
    json_build_object('x509', json_agg(grid_subject)) as auth_info, 
    max(regexp_replace(
          case 
            when grid_subject like 'token:%' then grid_subject 
            else '' 
          end,
          'token:',
          '')
         ) as auid 
  from persons, grid_subjects 
  where persons.person_id = grid_subjects.person_id 
  group by grid_subjects.person_id;

drop view if exists meta_roles;
create view meta_roles as 
  select 
    work_grp_name as name, 
    null as parent_role, 
    null as description
  from working_groups;

drop view if exists meta_namespaces;
create view meta_namespaces as
   select 'default' as name,
          'default namespace for migraton' as description,
          null as owner_user,
          'admin_role' as owner_role,
          'mengel' as creator,
          now() as created_timestamp,
          0 as file_count;

drop view if exists meta_users_roles;
create view meta_users_roles as
  select 
    persons.username as username,
    working_groups.work_grp_name as role_name
  from 
    persons, 
    persons_working_groups, 
    working_groups 
  where
       persons.person_id = persons_working_groups.person_id 
   and working_groups.work_grp_id = persons_working_groups.work_grp_id;
      

-- metacat files table view
drop view if exists meta_files;
create view meta_files as
  select 
    data_files.file_id as id,
    'default' as namespace,
    data_files.file_name as name,
    -- Roll up "name value" rows into a metadata dictionary:
    (select jsonb_object_agg(meta_inner.key, meta_inner.value) from
    -- Union together a bunch of queries to to get name value rows
    --  that make our metadata
    --  first string parameters...
       (select
         param_categories.param_category||'.'|| param_types.param_type as key,
         to_jsonb(param_values.param_value) as value
       from
         data_files_param_values,
         param_types,
         param_categories,
         param_values
       where
            data_files_param_values.file_id = data_files.file_id
        and data_files_param_values.param_value_id = param_values.param_value_id
        and param_types.param_type_id = data_files_param_values.param_type_id
        and param_categories.param_category_id = param_types.param_category_id
    --  then numeric parameters...
    union
       select
           param_categories.param_category||'.'|| param_types.param_type as key,
           to_jsonb(num_data_files_param_values.param_value) as value
         from num_data_files_param_values,
           param_types, param_categories
         where
              num_data_files_param_values.file_id = data_files.file_id
          and param_types.param_type_id = num_data_files_param_values.param_type_id
          and param_categories.param_category_id = param_types.param_category_id
    --  then a bunch of single name value pairs
    union (select  'core.process_id'::text , to_jsonb(data_files.process_id ))
    union (select  'core.application.family'::text , to_jsonb(application_families.family ))
    union (select  'core.application.version'::text , to_jsonb(application_families.version))
    union (select  'core.application.name'::text , to_jsonb(application_families.appl_name))
    union (select  'core.first_event_number'::text , to_jsonb(data_files.first_event_number ))
    union (select  'core.last_event_number'::text , to_jsonb(data_files.last_event_number ))
    union (select  'core.event_count'::text , to_jsonb(data_files.event_count ))
    union (select  'core.end_time'::text , to_jsonb(data_files.end_time ))
    union (select  'core.start_time'::text , to_jsonb(data_files.start_time ))
    union (select  'core.file_partition'::text , to_jsonb(data_files.file_partition ))
    union (select  'core.file_content_status'::text , to_jsonb(file_content_statuses.file_content_status ))
    union (select  'core.responsible_working_group'::text , to_jsonb(working_groups.work_grp_name))
    union (select  'core.file_type'::text , to_jsonb(file_types.file_type_desc ))
    union (select  'core.file_format'::text , to_jsonb(file_formats.file_format ))
    union (select  'core.data_tier'::text , to_jsonb(data_tiers.data_tier))
    union (select  'core.data_stream'::text , to_jsonb(datastreams.datastream_name))
    union (select  'core.retired_date'::text , to_jsonb(data_files.retired_date ))
    union (select  'core.scope'::text , to_jsonb(scope))
    -- then run numbers smushed into a list
    union (select  'core.runs'::text , (
       select jsonb_agg(to_jsonb(run_number))
         from  data_files_runs, runs
        where  data_files_runs.file_id = data_files.file_id
          and  data_files_runs.run_id = runs.run_id
     ))
    -- then and runs-subruns numbers smushed into a list
    union (select  'core.runs_subruns'::text , (
       select jsonb_agg(to_jsonb(run_number * 100000 + subrun_number))
         from  data_files_runs, runs
        where  data_files_runs.file_id = data_files.file_id
          and  data_files_runs.run_id = runs.run_id
     ))
    ) as meta_inner) as metadata,
    cpersons.username as creator,
    data_files.file_size_in_bytes as size,
    -- smush checksums together into a jason dict
    (select jsonb_object_agg(checksum_inner.key, checksum_inner.value) from
       (select
          checksum_name as key, checksum_value as value
        from checksums, checksum_types
        where 
             checksums.file_id = data_files.file_id
         and checksum_types.checksum_type_id = checksums.checksum_type_id
       ) as checksum_inner) as checksums,
    data_files.create_date as created_timestamp,
    upersons.username as updated_by,
    data_files.update_date as updated_timestamp,
    retired_date is not null as retired,
    retired_date as retired_timestamp,
    null as retired_by
  from 
    data_files 
  left outer join
    data_tiers on
      data_tiers.data_tier_id = data_files.data_tier_id
  left outer join
    datastreams on
      datastreams.stream_id = data_files.stream_id
  left outer join
    file_formats on
      file_formats.file_format_id = data_files.file_format_id
  left outer join
    persons as cpersons on
      cpersons.person_id = data_files.create_user_id
  left outer join
    persons as upersons on
      upersons.person_id = data_files.update_user_id
  left outer join
    file_types on
      file_types.file_type_id = data_files.file_type_id
  left outer join
    working_groups on
      data_files.responsible_working_group_id = working_groups.work_grp_id
  left outer join
    file_content_statuses on
      file_content_statuses.file_content_status_id = data_files.file_content_status_id 
  left outer join
    application_families on
      application_families.appl_family_id = data_files.appl_family_id;

drop view if exists meta_parent_child;
create view meta_parent_child as
  select 
    file_lineages.file_id_source as parent_id, 
    file_lineages.file_id_dest as child_id
  from
    file_lineages;
