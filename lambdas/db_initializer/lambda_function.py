import boto3
import psycopg2
import json
import os
import re


def getCredentials():
    credential = {}
    region_name = os.environ["REGION"]
    secret_name = os.environ["SECRETS_NAME"]
    client = boto3.client(service_name="secretsmanager", region_name=region_name)
    get_secret_value_response = client.get_secret_value(SecretId=secret_name)
    secret = json.loads(get_secret_value_response["SecretString"])
    credential["username"] = secret["username"]
    credential["password"] = secret["password"]
    credential["host"] = secret["host"]
    credential["db"] = secret["dbname"]
    return credential


def parse_db_config_sql(sql_file_path):
    """Parse the db_config.sql file to extract database names"""
    databases = []

    try:
        with open(sql_file_path, "r") as file:
            sql_content = file.read()

        # Use regex to extract database names between quotes after CREATE DATABASE
        pattern = r'CREATE DATABASE "([^"]+)"'

        # Find all SELECT statements and extract the database names
        select_statements = sql_content.split("SELECT")
        for statement in select_statements:
            if "CREATE DATABASE" in statement:
                match = re.search(pattern, statement)
                if match:
                    databases.append(match.group(1))
    except Exception as e:
        print(f"Error parsing SQL file: {e}")
        raise e

    return databases


def handler(event, context):
    credential = getCredentials()
    db_script_name = os.environ["DB_SCRIPT_NAME"]

    try:
        connection = psycopg2.connect(
            user=credential["username"],
            password=credential["password"],
            host=credential["host"],
            database="postgres",  # Connect to default postgres database first
        )
        connection.autocommit = True  # Important for CREATE DATABASE
        cursor = connection.cursor()

        # Get database names from SQL file
        script_path = os.path.join(os.path.dirname(__file__), db_script_name)
        databases = parse_db_config_sql(script_path)

        print(f"Found {len(databases)} databases to create: {databases}")

        # Check and create each database
        for db in databases:
            # Check if database exists
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,))
            exists = cursor.fetchone()

            if not exists:
                print(f"Creating database {db}")
                # Need to use SQL string literal here to handle dashes in DB names
                cursor.execute(f'CREATE DATABASE "{db}"')
                print(f"Created database {db}")
            else:
                print(f"Database {db} already exists")

        cursor.execute("SELECT version() AS version")
        results = cursor.fetchone()

        cursor.close()
        connection.close()

        return {
            "statusCode": 200,
            "body": f"Database creation completed. PostgreSQL version: {results[0]}",
        }
    except Exception as e:
        print(f"Error: {e}")
        return {"statusCode": 500, "body": f"Error creating databases: {e}"}
