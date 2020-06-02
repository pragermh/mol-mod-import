#!/usr/bin/env python
# -*- coding:utf-8 -*-
'''
Code for reading ASV data and metadata in Excel sheets or *.tsv files,
and importing these into corresponding postgreSQL (db) tables,
via pandas dataframes (df), and some editing.

If present, Excel will overwrite tsv:s,
and an (asv-by-event) 'asv-table' sheet or tsv will be converted to,
and overwrite, an (eventid-asvid-count) 'occurrence' dito.

DB changes will only be committed if all inserts succeed.

Includes NO input validation yet.

Data are passed multiple times btw Excel, text and pandas df:s
(especially if asv-table is present), so room for improvement.

TSVs (manually saved) from Mac Excel have 'mac_roman' encoding, but are detected
as 'ISO-8859-1'(aka. 'latin_1'), messing up special characters (e.g. °C).
temp.fix: add argument 'mac-roman' to get_record_df().

Annotation import uses dummy data for some fields,
until we get real output from DL.

Tables and code for dealing with EML metadata (mostly) removed 200302
as MS says eml.xml will be generated separately.
Minor dataset tbl kept for internal use, but unclear how we will get data for that
'''
import datetime as dt
import hashlib
from io import StringIO
import os
from os import path
import random  # For testing only
import sys
from urllib.parse import urlparse

from chardet import detect
import inflection as inf
import pandas as pd
import psycopg2
from psycopg2 import sql
from tabulate import tabulate  # For testing only


def main():

    # Locate user input
    dir = 'input/'

    # EXCEL
    # xl_file = 'input.xlsx'
    xl_file = 'input-small.xlsx'

    # TSVs
    evt_mixs_file = 'event.tsv'
    occ_asv_file = 'occurrence.tsv'
    emof_file = 'emof.tsv'
    # annot_file = 'annotation.tsv'
    annot_file = 'annotation-small.tsv'

    # Get TSVs from EXCEL (or comment out to use TSVs directly)
    excel_to_tsv(dir, xl_file)

    # Convert ASV table to occurrence data, if provided
    if path.exists(dir + 'asv-table.tsv'):
        print("Replacing 'occurrence.tsv' with data from 'asv-table.txt'.")
        occ_fr_asv_tbl(dir + 'asv-table.tsv', dir + 'occurrence.tsv')

    # Load data into data frames
    # Add encoding arg if using TSV exported from Mac Excel (see intro notes)
    # e.g: evt_mixs_df = get_record_df(dir + evt_mixs_file, 'mac-roman')
    evt_mixs_df = get_record_df(dir + evt_mixs_file)
    occ_asv_df = get_record_df(dir + occ_asv_file)
    emof_df = get_record_df(dir + emof_file)
    annot_df = get_record_df(dir + annot_file)  # [0: 10]

    # Connect to db
    try:
        url = urlparse(get_env_variable('DATABASE_URL'))
        conn = psycopg2.connect(
            user=url.username,
            password=url.password,
            database=url.path[1:],
            host=url.hostname,
            port=url.port
        )
        print(conn)
    except (Exception, psycopg2.OperationalError) as error:
        print("Could not connect to DB.\npsycopg2 message:", error)
        sys.exit()
    else:
        conn.autocommit = False
        cur = conn.cursor()
    try:
        # Start data import
        # DATASET
        print('Inserting dataset.')
        ds_meta = get_ds_meta()
        # Insert dataset, and save autogenerated id
        ds_id = insert_dataset(ds_meta, cur)

        # SAMPLING EVENTS
        print('Inserting events.')
        # Extract event-cols from larger df
        evt_df = make_evt_df(evt_mixs_df, ds_id, cur)

        # Insert events. Save aliases (from user) and ids (generated) in dict
        evt_alias_dict = insert_events(evt_df, cur)

        # MIXS
        print('Inserting mixs.')
        # Extract mixs-cols, and add event ids from dict
        mixs_df = make_mixs_df(evt_mixs_df, evt_alias_dict, cur)
        # Insert into mixs tbl
        insert_mixs(mixs_df, cur)

        # eMoF (extended Measurements or Facts)
        print('Inserting eMoF.')
        # Prepare and insert data into db tbl emof
        emof_df_prep = prep_emof_df(emof_df, evt_alias_dict, cur)
        insert_emof(emof_df_prep, cur)

        # ASVs / OCCURRENCES
        # Split occ and asv data, and add event ids from dict
        occ_df, asv_df = split_occ_asv_df(occ_asv_df, evt_alias_dict, cur)
        print_tbl(occ_df)
        # Copy df data into asv and occ tbls
        print('Inserting ASVs.')
        # Make empty temp tbl
        make_temp_tbl_copy('asv', cur)
        # Copy data into temp tbl
        copy_tbl_from_df('temp_asv', asv_df, cur)
        # Insert new ASV records into tbl asv
        insert_new_from_temp('asv', cur)
        print('Inserting Occurrences.')
        copy_tbl_from_df('occurrence', occ_df, cur)

        # # Add SBDI taxon annotation
        # print('Inserting SBDI taxon annotation.')
        # # (Modify to only affect specified subset of data later!)
        # annot_prep_df = prep_annot_df(annot_df, cur)
        # copy_tbl_from_df('taxon_annotation', annot_prep_df, cur)

        # Commit db changes (only if all SQL above worked)
        print('Committing changes to DB.')
        conn.commit()
        print("Transaction completed successfully.")
        cur.close()

    except (Exception, psycopg2.DatabaseError) as error:
        print("Error in DB transaction. Rolling back. \npostgreSQL message:", error)
        conn.rollback()

    finally:
        # Close db connection
        if(conn):
            conn.close()
            print("PostgreSQL conn is closed.\n")


def get_env_variable(name):
    try:
        return os.environ[name]
    except KeyError:
        message = "Expected environment variable '{}' not set.".format(name)
        raise Exception(message)


def print_tbl(df):
    '''Prints dataframe as table'''
    print(tabulate(df, headers='keys', tablefmt='psql'))


def excel_to_tsv(dir, xl_file):
    """Writes Excel workbook sheets to tsv files, via pandas df"""
    xl_path = dir + xl_file
    try:
        xl = pd.ExcelFile(xl_path)
    except OSError:
        print(f'Could not open / read Excel file: {xl_file!r}.')
        sys.exit()
    else:
        print(f'Loading {xl_file!r}')
        df = pd.DataFrame()
        columns = None
        for idx, name in enumerate(xl.sheet_names):
            if name in ['event', 'occurrence', 'asv-table', 'emof']:
                print(f"Saving sheet '{name}' to '{name}.tsv'.")
                sheet = xl.parse(name)
                sheet.to_csv(f'{dir}{name}.tsv', sep='\t', index=False, encoding="utf-8")


def taxonomy_from_ranks(df):
    # print('Concatenating rank fields into single taxonomy field.')
    ranks = ['kingdom', 'phylum', 'class', 'order', 'family',
             'genus', 'specific_epithet', 'infraspecific_epithet', 'otu']
    cmd_str = "df['taxonomy'] = " + " + '|' + ".join([f"df['{r}'].fillna('')" for r in ranks])
    exec(cmd_str)
    df = df.drop(df.columns & ranks, axis=1)
    df = df.rename(columns={"taxonomy": "previous_identifications"})
    return df


def occ_fr_asv_tbl(src, trg):
    '''
    Reads classic ASV-by-Sample table in TSV file,
    and outputs TSV with 'Sample-ASV-Count-Metadata' rows,
    excluding zero-obs.
    '''
    df = pd.read_csv(src, delimiter='\t', header='infer')
    df = df.melt(['asv_id_alias', 'DNA_sequence', 'kingdom', 'phylum', 'class', 'order', 'family',
                  'genus', 'specificEpithet', 'infraspecificEpithet', 'otu'],
                 var_name='event_id_alias',
                 value_name='organismQuantity')
    df.columns = [inf.underscore(c) for c in df.columns]
    df.rename(columns={"dna_sequence": "asv_sequence"})
    df.organism_quantity = df.organism_quantity.astype(int)
    df = df[df.organism_quantity > 0]
    df.to_csv(trg, sep='\t', index=False)


def get_encoding_type(file):
    '''Returns file encoding, e.g. utf-8, ascii'''
    with open(file, 'rb') as f:
        rawdata = f.read()
    return detect(rawdata)['encoding']


def get_record_df(file, encoding=None):
    '''Loads tsv data into pandas dataframe (df)'''
    try:
        f = open(file, 'rb')
    except OSError:
        print("Could not open / read file:", file)
        sys.exit()
    with f:
        if not encoding:
            encoding = get_encoding_type(file)
        df = pd.read_csv(file, sep='\t', header=0, encoding=encoding)
        # Replace 'NaN'values with 'None' (will be set to NULL in db)
        df = df.where(pd.notnull(df), None)
        # Translate DwC camelcase to snake_case, if needed
        df.columns = [inf.underscore(c) for c in df.columns]
        if 'dna_sequence' in df.columns:
            df = df.rename(columns={"dna_sequence": "asv_sequence"})
        # print(file)
        # print_tbl(df)
        return(df)


def get_ds_meta():
    '''Will be figured out when data flow USER > BAS-MOL > BIOATLAS is clear
    '''
    dataset_id = 'SMHI:BalticPicoplankton'
    provider_email = 'maria.prager@scilifelab.se'
    return [dataset_id, provider_email]


def get_tbl_cols(cur, tbl=None):
    '''Returns sorted list of all column names in db'''
    query = "SELECT DISTINCT column_name FROM information_schema.columns \
             WHERE table_schema = 'public'"
    if tbl:
        query = sql.SQL(query + " AND table_name = {}").format(sql.Placeholder())
    cur.execute(query, [tbl])
    colnames = [r[0] for r in cur.fetchall()]
    return colnames


def get_insert_query(tbl, fields, pk):
    '''Returns an SQL query of specified tbl fields, with named
    placeholders for values that will be added at execution'''
    query = sql.SQL("INSERT INTO {}({}) VALUES({})RETURNING {}").format(
        sql.Identifier(tbl),
        sql.SQL(',').join(map(sql.Identifier, fields)),
        sql.SQL(',').join(map(sql.Placeholder, fields)),
        sql.Identifier(pk))
    return query


def insert_dataset(ds_meta, cur):
    meta = {'dataset_id': ds_meta[0],
            'provider_email': ds_meta[1],
            'insertion_time': dt.datetime.now()}
    query = get_insert_query('dataset', meta.keys(), 'dataset_id')
    cur.execute(query, meta)
    # Return formatted query, exactly as sent to db
    # print(cur.mogrify(query, meta).decode())
    ds_id = cur.fetchone()[0]
    return(ds_id)


def make_evt_df(df, ds_id, cur):
    '''Extracts data for db tbl sampling_event from larger 'event-level' df.
    Uses col list from db to save editing time during development'''
    # Get list of cols from db tbl
    cols = get_tbl_cols(cur, 'sampling_event')
    # Keep all except two which will be missing from user input
    cols = [c for c in cols if c not in ('event_id', 'dataset_id')]
    df = df[cols].copy()
    df['dataset_id'] = ds_id
    df['event_id'] = df['dataset_id'] + ':' + df['event_id_alias']
    return df


def insert_events(df, cur):
    '''Inserts df data into db tbl sampling_event, saving event id aliases
    (from user) and event ids (autogenerated) in dict to be used downstream'''
    query = get_insert_query('sampling_event', (list(df)), 'event_id')
    alias_id = {}
    # Iterate over events = df rows
    for index, row in df.iterrows():
        values = row.to_dict()
        cur.execute(query, values)
        # Return formatted query, exactly as sent to db
        # print(cur.mogrify(query, values).decode())
        alias = row['event_id_alias']
        id = cur.fetchone()[0]
        # Add dict item with key=user alias, value=generated event id
        alias_id[alias] = id
    return(alias_id)


def make_mixs_df(df, evt_alias_dict, cur):
    '''Extracts data for db tbl mixs from larger 'event-level' df.
    Uses col list from db to save editing time during development'''
    cols = get_tbl_cols(cur, 'mixs')
    # Replace event id alias (from user) with event id (generated in insert_events)
    df = df.replace({"event_id_alias": evt_alias_dict})
    df = df.rename(columns={"event_id_alias": "event_id"})
    df = df[cols]
    return df


def insert_mixs(df, cur):
    '''Inserts df data into db tbl mixs'''
    query = get_insert_query('mixs', (list(df)), 'event_id')
    # Iterate over events = df rows
    for index, row in df.iterrows():
        values = row.to_dict()
        cur.execute(query, values)
        # Return formatted query, exactly as sent to db
        # print(cur.mogrify(query, values).decode())


def prep_emof_df(df, evt_alias_dict, cur):
    '''  '''
    cols = get_tbl_cols(cur, 'emof')
    # Replace event id alias (from user) with event id (generated in insert_events)
    df = df.replace({"event_id_alias": evt_alias_dict})
    df = df.rename(columns={"event_id_alias": "event_id"})
    df['measurement_id'] = df['event_id'] + ':' + df['measurement_type']
    df = df[cols]
    return df


def insert_emof(df, cur):
    '''Inserts df data into db tbl emof'''
    query = get_insert_query('emof', (list(df)), 'event_id')
    # Iterate over measurements = df rows
    for index, row in df.iterrows():
        values = row.to_dict()
        cur.execute(query, values)
        # Return formatted query, exactly as sent to db
        # print(cur.mogrify(query, values).decode())


def md5(seq):
    '''Calculates md5 checksum of ASV sequence'''
    m = hashlib.md5()
    # Convert from byte
    m.update(seq.encode('utf-8'))
    return m.hexdigest()


def split_occ_asv_df(df, evt_alias_dict, cur):
    '''Returns occ and asv dfs from larger occ-level df, and replaces
    event id aliases (from user) with event ids (generated in insert_events).
    Adds sequence checksums as ASV ids'''
    df = taxonomy_from_ranks(df)
    df = df.replace({"event_id_alias": evt_alias_dict})
    df = df.rename(columns={"event_id_alias": "event_id"})
    # Calculate id from md5 checksum
    df['asv_id'] = df['asv_sequence'].apply(lambda x: 'ASV:' + md5(x))
    # Get occ cols from db tbl, but skip id col (missing from user input)
    occ_cols = get_tbl_cols(cur, 'occurrence')
    occ_cols.remove('occurrence_id')
    occ_df = df[occ_cols].copy()
    occ_df['occurrence_id'] = occ_df['event_id'] + ':' + occ_df['asv_id']
    # Repeat for asv tbl
    asv_cols = get_tbl_cols(cur, 'asv')
    asv_df = df[asv_cols]
    # Get unique ASVs
    asv_df = asv_df.drop_duplicates(subset="asv_sequence")
    return [occ_df, asv_df]


def make_temp_tbl_copy(tbl, cur):
    '''(Re-)creates an empty copy of a specified tbl in the db'''
    query = sql.SQL("DROP TABLE IF EXISTS {}; \
    CREATE TABLE {} AS SELECT * FROM {} WHERE false;").format(
        sql.Identifier('temp_' + tbl),
        sql.Identifier('temp_' + tbl),
        sql.Identifier(tbl)
    )
    cur.execute(query)


def insert_new_from_temp(tbl, cur):
    query = sql.SQL("INSERT INTO {} SELECT * FROM {} EXCEPT \
    SELECT * FROM {}").format(
        sql.Identifier(tbl),
        sql.Identifier('temp_' + tbl),
        sql.Identifier(tbl)
    )
    cur.execute(query)


def copy_tbl_from_df(tbl, df, cur):
    '''Copies df data, via 'filelike stringIO object', into db tbls.
    Presumably faster than insert, at least for larger dfs'''
    output = StringIO()
    df.to_csv(output, sep='\t', header=False, index=False)
    # Go to top of 'file'
    output.seek(0)
    contents = output.getvalue()
    cur.copy_from(output, tbl, columns=list(df))


def prep_annot_df(df, cur):
    '''Prepares annotation df for db import
    Uses some dummy values for now.
    '''
    # cols = get_tbl_cols('taxon_annotation', conn)
    df['asv_id'] = df['asv_sequence'].apply(lambda x: md5(x))
    # df = df[cols]
    df['status'] = 'valid'
    df['date_identified'] = '2019-11-01'
    df['identification_references'] = 'https://bioatlas.github.io/mol-data/ASV/identification-methods'
    df['reference_db'] = 'GTDB'
    df['annotation_algorithm'] = 'RDP'
    df['annotation_confidence'] = [round(random.uniform(0.9, 1.0), 2)
                                   for _ in range(0, len(df.index))]
    # df['taxonRemarks'] = 'Unite DOI'
    df = df.drop(columns='asv_sequence')
    return df


if __name__ == '__main__':
    main()
