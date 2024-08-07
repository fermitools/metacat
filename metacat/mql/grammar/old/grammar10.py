MQL_Grammar = """
query:  ("with" param_def_list)? params_applied_query

?params_applied_query:  top_file_query             
    | top_dataset_query                            

top_file_query          :    file_query
top_dataset_query       :    dataset_query

?file_query: meta_filter                                  
    | file_query "-" meta_filter                          -> minus

//?limited_file_query_expression: meta_filter "limit" SIGNED_INT
//    | meta_filter                   

?meta_filter: file_query_exression "where" meta_exp     
    |   file_query_exression                             

?file_query_exression:  term_file_query                   
    |   "union" "(" file_query_list ")"                  -> union
    |   "[" file_query_list "]"                          -> union
    |   "join"  "(" file_query_list ")"                  -> join
    |   "{" file_query_list "}"                          -> join
    |   "parents" "(" file_query ")"                     -> parents_of
    |   "children" "(" file_query ")"                    -> children_of
    |   file_query "limit" SIGNED_INT                    -> limit              
    |   file_query "skip" SIGNED_INT                     -> skip              
    |   "(" file_query ")"           

term_file_query: "files" ("from" dataset_term_list)?                                -> basic_file_query
    |   "filter" FNAME "(" filter_params ? ")" "(" file_query_list ")"              -> filter
    |   "query" qualified_name                                                      -> named_query
    |   ("dids"|"fids") string_list                                                 -> file_list
    |   "names" STRING? string_list                                                 -> file_list

?string_list:    STRING ("," STRING)*

filter_params : constant_list
    |   (constant_list ",")? param_def_list

file_query_list: file_query ("," file_query)*     

?dataset_query_list: dataset_query_expression ("," dataset_query_expression)*

dataset_query_expression:   dataset_query_term_with_provenance
    |   "(" dataset_query_list ")"
    |   dataset_query_expression "having" meta_exp
    
!dataset_query_term_with_provenance:   dataset_query_term ("with" "children" "recursively"?)?

dataset_query_term: qualified_name
    | dataset_pattern

dataset_pattern:    (FNAME ":")? STRING

qualified_name:     (FNAME ":")? FNAME

param_def_list :  param_def ("," param_def)*

param_def: CNAME "=" constant

?meta_exp:   meta_or                                                           

meta_or:    meta_and ( "or" meta_and )*

meta_and:   term_meta ( "and" term_meta )*

?term_meta:  scalar CMPOP constant                  -> cmp_op
    | scalar "in" constant ":" constant             -> in_range
    | scalar "not" "in" constant ":" constant       -> not_in_range
    | scalar "in" "(" constant_list ")"             -> in_set
    | scalar "not" "in" "(" constant_list ")"       -> not_in_set
    | ANAME "present"?                              -> present                   
    | ANAME "not" "present"                         -> not_present                   
    | constant "in" ANAME                           -> constant_in_array
    | constant "not" "in" ANAME                     -> constant_not_in_array
    | "(" meta_exp ")"                              
    | "!" term_meta                                 -> meta_not

scalar:  ANAME
        | ANAME "[" "all" "]"                              -> array_all
        | ANAME "[" "any" "]"                              -> array_any
        | ANAME "[" SIGNED_INT "]"                  -> array_subscript
        | ANAME "[" STRING "]"                      -> array_subscript
        | "len" "(" ANAME ")"                       -> array_length

    
constant_list:    constant ("," constant)*                    

constant : SIGNED_FLOAT                             -> float_constant                      
    | STRING                                        -> string_constant
    | SIGNED_INT                                    -> int_constant
    | BOOL                                          -> bool_constant

index:  STRING
    | SIGNED_INT

ANAME: "." WORD
    | WORD ("." WORD)*

FNAME: LETTER ("_"|"-"|"."|LETTER|DIGIT)*

WORD: LETTER ("_"|LETTER|DIGIT)*

CMPOP:  "<" "="? | "!"? "=" "="? | "!"? "~" "*"? | ">" "="? | "like"            //# like is not implemented yet

BOOL: "true"i | "false"i

STRING : /("(?!"").*?(?<!\\\\)(\\\\\\\\)*?"|'(?!'').*?(?<!\\\\)(\\\\\\\\)*?')/i

%import common.CNAME
%import common.SIGNED_INT
%import common.SIGNED_FLOAT

%import common.WS
%import common.LETTER
%import common.DIGIT
%ignore WS
"""


