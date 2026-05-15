
-- views to build in SAM database to generate the Metacat table data
create view meta_users as 
  select 
    max(username) as username, 
    max(first_name||' '||last_name) as name, 
    max(email_address) as email, 
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

create view meta_roles as 
  select 
    work_grp_name as name, 
    '' as parent_role, 
    '' as description
  from working_groups;

create view meta_users_roles as
  select 
    persons.username as username,
    working_groups.work_grp_name as role_name
  from persons, persons_working_groups, working_groups 
  where
       persons.person_id = persons_working_groups.person_id 
   and working_groups.work_grp_id = persons_working_groups.work_grp_id;
      

-- still needs application info, checksums, runs(?), file_types, file_formats...
create view meta_files as
  select 
    data_files.file_id as id,
    'default' as namespace,
    data_files.file_name as name,
    (select jsonb_object_agg(meta_inner.key, meta_inner.value) from
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
    union (select  'core.process_id'::text , to_jsonb(data_files.process_id ))
    union (select  'core.first_event_number'::text , to_jsonb(data_files.first_event_number ))
    union (select  'core.last_event_number'::text , to_jsonb(data_files.last_event_number ))
    union (select  'core.event_count'::text , to_jsonb(data_files.event_count ))
    union (select  'core.end_time'::text , to_jsonb(data_files.end_time ))
    union (select  'core.start_time'::text , to_jsonb(data_files.start_time ))
    union (select  'core.file_partition'::text , to_jsonb(data_files.file_partition ))
    union (select  'core.file_content_status_id'::text , to_jsonb(data_files.file_content_status_id ))
    union (select  'core.responsible_working_group_id'::text , to_jsonb(data_files.responsible_working_group_id ))
    union (select  'core.file_type_id'::text , to_jsonb(data_files.file_type_id ))
    union (select  'core.file_format_id'::text , to_jsonb(data_files.file_format_id ))
    union (select  'core.data_tier_id'::text , to_jsonb(data_files.data_tier_id ))
    union (select  'core.retired_date'::text , to_jsonb(data_files.retired_date ))
    union (select  'core.scope'::text , to_jsonb(scope))
    ) as meta_inner) as metadata,
    cpersons.username as creator,
    data_files.file_size_in_bytes as size,
    '{}'::jsonb as checksums,
    data_files.create_date as created_timestamp,
    upersons.username as updated_by,
    data_files.update_date as updated_timestamp,
    retired_date is not null as retired,
    retired_date as retired_timestamp,
    '' as retired_by
  from 
    data_files, 
    persons as cpersons, 
    persons as upersons
 where 
      cpersons.person_id = data_files.create_user_id
  and upersons.person_id = data_files.update_user_id;

