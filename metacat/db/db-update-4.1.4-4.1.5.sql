
-- get in the metacat schema.

set search_path = metacat;

-- in 4.1.5 we will need the "required" column in 
-- the paramater_categories table.

alter table parameter_categories add column required boolean default 'false';
