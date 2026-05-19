#!/usr/bin/perl

while(<>) {
    # space pad so blank matches below work at ends
    s/.*/ $& /;
    s/[()]/ $& /g;

    # map assorted field names
    s/ (data_stream|run_number|run_type|data_tier|end_time|event_count|file_content_status|file_format|file_partition|file_type|first_event_number|last_event_number|process_id|retired_date|runs|scope|start_time) / core.$1 /g;
    s/ (family|version) / core.application.$1 /g;
    s/ (appl_name|application) / application.name /g;
    s/ file_(name|size) / $1 /g;

    s/with +limit/limit/g;

    s/ like / ~ /g;

    # in foo, bar, baz -> in (foo, bar, baz)
    s/ in +([a-zA-Z0-9_.-]+ *(, *[a-zA-Z0-9_.-]+)*)/ in ($1) /g;

    # fix name value to name = value  
    # 6 and greater chars
    s/( [a-zA-Z_.]{4,}) +([a-zA-Z0-9_.-]{6,}) / $1 = $2 /g; 
    # 5 chars but not "w(here)" nor "l(imit)"
    s/( [a-zA-Z_.]{4,}) +([a-km-vx-zA-Z][a-zA-Z0-9_.-]{4}) / $1 = $2 /g; 
    # 4 chars
    s/( [a-zA-Z_.]{4,}) +([a-zA-Z0-9_.-]{4}) / $1 = $2 /g; 
    # 3 chars but not 'and'
    s/( [a-zA-Z_.]{4,}) +([b-zA-Z][a-zA-Z0-9_.-]{2}) / $1 = $2 /g; 
    # 2 chars but neither 'in' nor 'or'
    s/( [a-zA-Z_.]{4,}) +([a-hj-np-zA-Z][a-zA-Z0-9_.-]) / $1 = $2 /g; 
    s/( [a-zA-Z_.]{4,}) +('[^']*') / $1 = $2 /g; 
    s/( [a-zA-Z_.]{4,}) +([0-9.]+ )/ $1 = $2 /g;

    s/and not *isparentof: *\(/ - parents ( files where/g;
    s/and not *\( *isparentof: *\(/ - (parents ( files where/g;
    s/and not *ischildof: *\(/ - children ( files where/g;
    s/and not *\( *ischildof: *\(/ - (childs ( files where/g;
    s/and not ischildof: *\(/ - children ( files where/g;
    s/ischildof: *\(/children ( files where/g;
    s/isparentof: *\(/parents ( files where/g;

    s/ minus / - files where /g;

    # finally put files where or files selected by  prefix on
    if ( m/ and parents /) {
       s/(.*) and parents /union $1 parents/;
    }
    if ( m/ and childern /) {
       s/(.*) and children /union $1 children/;
    }

    if ( m/^[^-]* snapshot_id / ) {
        s/(.*) snapshot_id[ =]*([0-9]+) (.*)/ files from default:snapshot_$2 where $1 $3 /;
    }
    if ( m/ - .* snapshot_id / ) {
        print(" - snapshot_id case");
        s/(.*) - (.*) snapshot_id[=]*([0-9]+) (.*)/ $1 - files from default:snapshot_$3 where $2 $4 /;
    }


   
    if ( m/^ *defname:/) {
        s/ *defname: *([^ ]*) (.*)/ files selected by $1 where $2/;
    } elsif ( m/ and +defname:/) {
        s/(.*)and +defname: *([^ ]*) (.*)/ files selected by $2 where $1 $3/;
    } else {
        s/^/ files where /;
    }
    s/ where +limit / limit /;

    # now special cases done with filters(?)
    if (m/(.*) tape_label *= *([^ ]*) (.*)/) {
        $_ = "filter tape_label() ( $1 $3 ) where tape_label = $2\n";
        s/and +and/and/g;
        s/where *\)/)/;
    }
    if (m/(.*) full_path +(=|like) +([^ ]*) (.*) full_path +(=|like) +([^ ]*) (.*)/) {
        $comp = $2;
        $comp =~ s/like/~/;
        $comp2 = $5;
        $comp =~ s/like/~/;
        $_ = "filter rucio_replicas() ( $1 $4 $7 ) where rucio.lfn $comp $3 and rucio.lfn $comp $6";
        s/and +and/and/g;
        s/where *\)/)/;
    }
    if (m/(.*) full_path +(=|like) +([^ ]*) (.*)/) {
        $comp = $2;
        $comp =~ s/like/~/;
        $_ = "filter rucio_replicas() ( $1 $4 ) where rucio.lfn $comp $3";
        s/and +and/and/g;
        s/where *\)/)/;
    }
    if ( m/(.*) files where (.*) snapshot_for_project_name *= *([^ ]+) (.*)/ ) {
        $_ = "$1 files from sam_projects:$3 where $2 $4";
    }
    s/ full_path / rucio.lfn /g;
    # clean up goofiness that ensues...
    s/ limit +=/ limit /g;
    s/ offset +=/ offset /g;
    s/ files +where +parents / parents /g;
    s/ files +where +children / children /g;
    s/ where +and / where /g;
    s/ where +or / where /g;
    s/ files +where +files +from / files from /g;
    s/ where +$//;
    s/ where +=/ where /;
    # combine multiple dataset refs
    s/ files from ([^ ]*) files from ([^ ]*) / files from $1,$2 /g;
    s/ files from ([^ ]*) files from ([^ ]*) / files from $1,$2 /g;
    print;
}
