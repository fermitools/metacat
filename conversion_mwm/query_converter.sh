#!/bin/sh

 perl -pe '
    # space pad so blank matches below work at ends
    s/.*/ $& /;

    # map assorted field names
    s/ (data_tier|end_time|event_count|file_content_status|file_format|file_partition|file_type|first_event_number|last_event_number|process_id|retired_date|runs|scope|start_time) / core.$1 /g;
    s/ (family|version) / core.application.$1 /g;
    s/ (appl_name|application) / application.name /g;

    s/with +limit/limit/g;


    # fix name value to name = value  
    s/( [a-zA-Z_.]{4,}) +([a-zA-Z][a-zA-Z0-9_-]{3,}) / $1 = $2 /g; 
    s/( [a-zA-Z_.]{4,}) +([b-zA-Z][a-zA-Z0-9][a-zA-Z0-9]) / $1 = $2 /g; 
    s/( [a-zA-Z_.]{4,}) +([a-np-zA-Z][a-zA-Z0-9]) / $1 = $2 /g; 
    s/( [a-zA-Z_.]{4,}) +('"'"'[^'"'"']*'"'"') / $1 = $2 /g; 
    s/( [a-zA-Z_.]{4,}) +([0-9.]+ )/ $1 = $2 /g;

    s/isparentof: *\(/parents ( files where/g;
    s/ischildof: *\(/children ( files where/g;

    s/ minus / - files where /g;

    # finally put files where or files selected by  prefix on
    if ( m/^ *defname:/) {
        s/ *defname: *([^ ]*) (.*)/ files selected by $1 where $2/;
    } elsif ( m/ and +defname:/) {
        s/(.*)and +defname: *([^ ]*) (.*)/ files selected by $2 where $1 $3/;
    } else {
        s/^/ files where /;
    }
    s/ where +limit / limit /;

  '
