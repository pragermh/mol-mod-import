#!/usr/bin/env python3
# -*- coding:utf-8 -*-
'''
Code for deleting all records in ASV db, and restarting sequences at 1.
'''
import os
import sys
from urllib.parse import urlparse

import psycopg2


def main():

    url = urlparse(get_env_variable('DATABASE_URL'))

    try:
        conn = psycopg2.connect(
            user=url.username,
            password=url.password,
            database=url.path[1:],
            host=url.hostname,
            port=url.port
        )
    except (Exception, psycopg2.DatabaseError) as error:
        print("Connection error: ", error)
        sys.exit(1)
    else:
        print(
            f'Connected to DB {url.path[1:]} on {url.hostname}:{url.port} as user {url.username}.')

    try:
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
        print('Data have been deleted, and sequences have been reset to 1.')

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
        message = f'Expected environment variable {name} not set.'
        print(message)
        sys.exit(1)


# so that Python files can act as either reusable modules, or as standalone programs
if __name__ == '__main__':
    main()
