import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def check_name(name, zone_name) -> bool:
    """ Check if the DNS value `name` is for a subdomain of `zone_name` """
    offset = zone_name.count('.') + 2
    return '.'.join(name.split('.')[-offset:-1]) == zone_name


def get_zone_id_by_name(client, zone_name) -> str:
    """ Get the hosted zone ID using `zone_name` """
    # Ensure the zone name ends with a period
    if not zone_name.endswith('.'):
        zone_name += '.'

    # Call list_hosted_zones and iterate through the hosted zones
    paginator = client.get_paginator('list_hosted_zones')
    for page in paginator.paginate():
        for zone in page['HostedZones']:
            if zone['Name'] == zone_name:
                return zone['Id']

    raise Exception(f'Hosted Zone "{zone_name}" not found')


def handler(event, context):
    logger.info(f'event received: {json.dumps(event, indent=4)}')

    # Get zone name and role for DNS validation from environment variables
    hosted_zone_name = os.environ['HOSTED_ZONE_NAME']
    target_role_arn = os.environ['TARGET_ROLE_ARN']

    # Use `eventName` to determine if this is a create or delete operation
    event_name = event.get('detail', {}).get('eventName')

    if event_name == 'RequestCertificate':
        # Set action to UPSERT for create operation and get certificate ARN
        action = 'UPSERT'
        certificate_arn = event.get(
            'detail', {}).get('responseElements', {}).get('certificateArn')
        if not certificate_arn:
            raise ValueError('Unable to find certificate ARN.')

        try:
            # Retrieve certificate details
            acm = boto3.client('acm')
            cert_detail = acm.describe_certificate(
                CertificateArn=certificate_arn
            )

            # Get CNAME value used for DNS validation of ACM certificate
            cname_record = cert_detail['Certificate'][
                'DomainValidationOptions'][0]['ResourceRecord']

            # Check that the certificate is for the correct zone
            if not check_name(cname_record['Name'], hosted_zone_name):
                logger.info(
                    f'Cert is for another zone: {cname_record["Name"]}'
                )
                # Exit without error if certificate is for another zone
                return {
                    'statusCode': 200,
                    'body': json.dumps(
                        f'Cert is for another zone: {cname_record["Name"]}'
                    )
                }

        except Exception as e:
            logger.error(f'Error fetching ACM certificate details: {str(e)}')
            return {
                'statusCode': 500,
                'body': json.dumps(
                    f'Error fetching ACM certificate details: {str(e)}')
            }

    elif event_name == 'DeleteCertificate':
        # Set action to DELETE for delete operation and get certificate ARN
        action = 'DELETE'
        certificate_arn = event.get(
            'detail', {}).get('requestParameters', {}).get('certificateArn')
        if not certificate_arn:
            raise ValueError('Unable to find certificate ARN.')
        else:
            cname_record = None

    else:
        logger.error(f'Unsupported event name: {event_name}')
        return {
            'statusCode': 400,
            'body': json.dumps(f'Unsupported event name: {event_name}')
        }

    try:
        # Assume role for DNS validation in zone account
        sts = boto3.client('sts')
        zone_acct_session = sts.assume_role(
            RoleArn=target_role_arn,
            RoleSessionName='CreateDNSRecordSession',
        )
        credentials = zone_acct_session['Credentials']

        # Create SSM client with assumed role credentials
        ssm = boto3.client(
            'ssm',
            aws_access_key_id=credentials['AccessKeyId'],
            aws_secret_access_key=credentials['SecretAccessKey'],
            aws_session_token=credentials['SessionToken'],
            region_name='us-east-1'  # SSM params are stored in us-east-1
        )

        try:
            # Define string value used for SSM parameter name
            cert_ssm_key = certificate_arn.split('/')[-1]

            # Store or remove DNS validation values in SSM parameter
            if event_name == 'RequestCertificate':
                ssm.put_parameter(
                    Name=f'/acm/{cert_ssm_key}',
                    Description='DNS records for ACM certificate validation',
                    Value=json.dumps(cname_record),
                    Type='String',
                )
            elif event_name == 'DeleteCertificate':
                cname_record_param = ssm.get_parameter(
                    Name=f'/acm/{cert_ssm_key}'
                )
                cname_record = json.loads(
                    cname_record_param['Parameter']['Value']
                )
                # Check that the certificate is for the correct zone
                if not check_name(cname_record['Name'], hosted_zone_name):
                    logger.info(
                        f'Cert is for another zone: {cname_record["Name"]}'
                    )
                    # Exit without error if certificate is for another zone
                    return {
                        'statusCode': 200,
                        'body': json.dumps(
                            f'Cert is for another zone: {cname_record["Name"]}'
                        )
                    }

                # Delete ACM parameter from SSM parameter store
                ssm.delete_parameter(
                    Name=f'/acm/{cert_ssm_key}'
                )

        except Exception as e:
            logger.error(f'Error retrieving DNS values from SSM: {str(e)}')
            return {
                'statusCode': 500,
                'body': json.dumps(
                    f'Error retrieving DNS values from SSM: {str(e)}')
            }

        # Create Route 53 client with assumed role credentials
        route53 = boto3.client(
            'route53',
            aws_access_key_id=credentials['AccessKeyId'],
            aws_secret_access_key=credentials['SecretAccessKey'],
            aws_session_token=credentials['SessionToken'],
        )

        # Get hosted zone ID using the zone name
        zone_id = get_zone_id_by_name(route53, hosted_zone_name)

        # Update Route 53 DNS records
        route53.change_resource_record_sets(
            HostedZoneId=zone_id,
            ChangeBatch={
                'Changes': [
                    {
                        # Use the action determined by event_name
                        'Action': action,
                        'ResourceRecordSet': {
                            'Name': cname_record['Name'],
                            'Type': cname_record['Type'],
                            'TTL': 300,
                            'ResourceRecords': [
                                {
                                    'Value': cname_record['Value']
                                }
                            ]
                        }
                    }
                ]
            }
        )

    except Exception as e:
        logger.error(f'Error updating Route 53 DNS records: {str(e)}')
        return {
            'statusCode': 500,
            'body': json.dumps(
                f'Error updating Route 53 DNS records: {str(e)}')
        }

    logger.info(f'Action: "{action}" DNS record in Route 53 for ACM complete.')
    return {
        'statusCode': 200,
        'body': json.dumps(
            f'Action: "{action}" DNS record in Route 53 for ACM complete.')
    }
