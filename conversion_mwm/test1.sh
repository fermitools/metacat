. /cvmfs/fermilab.opensciencegrid.org/packages/common/setup-env.sh
spack load --first sam-web-client@3.6 os=default_os
spack load --first r-m-dd-config@1.8 os=default_os experiment=hypot

get_recent() {
   for exp in nova mu2e dune
   do
       echo "Getting recent defs for $exp..."
       htgettoken -i ${exp} -a htvaultprod.fnal.gov

       samweb -e $exp list-definitions --after=2023-01-01T00:00:00 | 
          head -100 | 
          while read defname
          do
              samweb -e $exp describe-definition $defname | 
                 sed -e 1,/Group:/d -e s/Dimensions:// |   
                 perl -pe 's/\n/ /;'
              echo
          done > recent_${exp}_definitions.txt
   done
}

if [ "$1" = "--get_recent" ]
then
    get_recent
fi

htgettoken -i hypot -a htvaultprod.fnal.gov
metacat auth login -mtoken $USER

./query_converter.pl *_definitions.txt |  (
  fail=0
  total=0
  while read query 
  do
    if metacat query --explain "$query" > /dev/null  2>&1
    then
        printf "."
        total=$((total + 1))
    else
        echo
        echo FAIL "$query"
        fail=$((fail + 1))
        total=$((total + 1))
    fi
  done
  echo $fail / $total
  )
