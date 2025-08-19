import os
import yaml
from aws_cdk import Environment


def load_configurations() -> dict:
    """
    Load all YAML files in the 'configuration' directory into a dictionary.

    :return: A dict with filenames as keys and their parsed content as values.
    """
    current_script_directory = os.path.dirname(os.path.abspath(__file__))
    configurations = {}

    for filename in os.listdir(current_script_directory):
        if filename.endswith('.yaml') or filename.endswith('.yml'):
            filepath = os.path.join(current_script_directory, filename)
            with open(filepath, 'r') as file:
                # Removing the file extension from filename for dictionary key
                key_name = os.path.splitext(filename)[0]
                configurations[key_name] = yaml.safe_load(file)

    return configurations


# Deployment account
DEPLOYMENT_ACCOUNT_ID = ''
DEPLOYMENT_REGION = 'us-east-1'
DEPLOYMENT_ENV = Environment(
    account=DEPLOYMENT_ACCOUNT_ID,
    region=DEPLOYMENT_REGION,
)
