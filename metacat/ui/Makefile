BINFILES = metacat 	mql.py
LIBFILES = \
    metacat_ui.py metacat_config.py  \
    metacat_auth.py metacat_dataset.py metacat_query.py metacat_file.py metacat_namespace.py \
	metacat_admin.py metacat_category.py metacat_named_query.py 

build:	
	mkdir -p $(BINDIR) $(LIBDIR)
	cp $(BINFILES) $(BINDIR); cd $(BINDIR); chmod +x $(BINFILES)
	cp $(LIBFILES) $(LIBDIR)
	cp -R cli $(LIBDIR)
	
