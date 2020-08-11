#!/usr/bin/env python
# -*- coding:utf-8 -*-
'''
Code for deleting all records in ASV db, and restarting sequences at 1.
'''
import os
from urllib.parse import urlparse

import psycopg2


def main():

    try:
        url = urlparse(get_env_variable('DATABASE_URL'))
        conn = psycopg2.connect(
            user=url.username,
            password=url.password,
            database=url.path[1:],
            host=url.hostname,
            port=url.port
        )
        cur = conn.cursor()
        cur.execute("TRUNCATE dataset CASCADE; \
                     TRUNCATE asv CASCADE; \
                     TRUNCATE temp_asv CASCADE; \
                     SELECT SETVAL(c.oid, 1, False) \
                     from pg_class c JOIN pg_namespace n \
                     on n.oid = c.relnamespace \
                     where c.relkind = 'S' and n.nspname = 'public'",)
        cur.close()
        conn.commit()
        print('\nData have been deleted, and sequences have been reset to 1.')

    except (Exception, psycopg2.DatabaseError) as error:
        print("Error in transaction. Reverting.", error)
        conn.rollback()

    finally:
        # closing database conn.
        if(conn):
            conn.close()
            print("PostgreSQL conn is closed.\n")


def get_env_variable(name):
    try:
        return os.environ[name]
    except KeyError:
        message = "Expected environment variable '{}' not set.".format(name)
        raise Exception(message)


# so that Python files can act as either reusable modules, or as standalone programs
if __name__ == '__main__':
    main()
