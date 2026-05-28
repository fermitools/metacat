
This is my attempt at a different migration approach, to wit:

* Make views on a SAM database that directly generate the desired MetaCat and
  DataDispatcher table data
* Have 2 psql clients running, one dumping the SAM database with those views and
  piped into one loading the generated data into the Metacat /  DataDispatcher
  databases.

The view queries are a bit hair-raising looking (especially the file table w/metadata)
but seem to actually work okay (in samdev, at least).

Now have a migrator script ("migrator") which gets psql commands for each end, etc. from
a config file ("migrator.ini") and does the whole thing. 

NOTE:  

`                 DANGER                 DANGER                `

Currently drops and and re-creates the destination metacat tables !!!  
Do not point this at a real Metacat destination with its own data!!!

`                 DANGER                 DANGER                `
    
