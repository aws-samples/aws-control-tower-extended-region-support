#
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#

'''
This lamdba validates creates config in additional regions
with in AWS Control Tower
'''

import logging
from time import sleep
import os
import boto3
from botocore.exceptions import ClientError
import cfnresponse

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)
SUCCESS = "SUCCESS"
FAILED = "FAILED"

SESSION = boto3.session.Session()
MY_REGION = SESSION.region_name

CFT = SESSION.client('cloudformation')
ORG = SESSION.client('organizations')

EXEC_ROLE = 'AWSControlTowerExecution'
CONFIG_STACK = 'AWSControlTowerBP-BASELINE-CONFIG'
CNFPACK_URL = 'https://marketplace-sa-resources-ct-us-east-2.s3.us-east-2.amazonaws.com/ConformsBucket.yaml'


def list_stack_sets(status='ACTIVE'):
    '''Return stack sets if exists and matches the status optionally'''

    result = list()
    ss_list = list()

    try:
        cft_paginator = CFT.get_paginator('list_stack_sets')
        cft_page_iterator = cft_paginator.paginate()
    except Exception as exe:
        LOGGER.error('Unable to list stacksets: %s', str(exe))

    for page in cft_page_iterator:
        ss_list += page['Summaries']

    for item in ss_list:
        if item['Status'] == status:
            result.append(item['StackSetName'])

    return result


def get_stackset_parameters(ss_name):
    '''List all parameters from the given stackset name'''

    try:
        result = CFT.describe_stack_set(StackSetName=ss_name
                                        )['StackSet']['Parameters']
        return result
    except Exception as exe:
        LOGGER.error('Unable to get parameters %s', str(exe))


def get_stackset_body(ss_name):
    '''List all parameters from the given stackset name'''

    try:
        result = CFT.describe_stack_set(StackSetName=ss_name
                                        )['StackSet']['TemplateBody']
        return result
    except Exception as exe:
        LOGGER.error('Unable to get stack body %s', str(exe))


def does_stack_set_exists(ss_name):
    '''Return True if active StackSet exists'''

    result = False
    ss_list = list_stack_sets()

    if ss_name in ss_list:
        result = True
    else:
        LOGGER.warning('StackSet not found:%s', ss_name)

    return result


def add_stack_instance(ss_name, accounts, regions):
    ''' Adds StackSet Instances '''

    result = {'OperationId': None}
    ops = {
        'MaxConcurrentPercentage': 100,
        'FailureTolerancePercentage': 50
        }

    output = does_stack_set_exists(ss_name)
    LOGGER.info('OUTPUT:%s', output)

    if output:
        try:
            LOGGER.info('Add Stack Set Instances: %s, %s, %s',
                        ss_name, regions, accounts)
            result = CFT.create_stack_instances(StackSetName=ss_name,
                                                Accounts=accounts,
                                                Regions=regions,
                                                OperationPreferences=ops)
        except ClientError as exe:
            LOGGER.error("Unexpected error: %s", str(exe))
            result['Status'] = exe
    else:
        LOGGER.error('StackSet %s does not exist', ss_name)

    return result['OperationId']


def list_all_stack_instances(ss_name):
    '''List all stack instances in the account'''

    result = list()

    active_stacksets = list_stack_sets()

    if ss_name not in active_stacksets:
        LOGGER.error('StackSet %s not found in %s', ss_name, active_stacksets)
    else:
        try:
            cft_paginator = CFT.get_paginator('list_stack_instances')
            cft_page_iterator = cft_paginator.paginate(StackSetName=ss_name)
        except Exception as exe:
            LOGGER.error('Unable to list stack instances %s', str(exe))

        for page in cft_page_iterator:
            result += page['Summaries']

    return result


def list_from_stack_instances(ss_name, key='Account'):
    '''List of accounts that are part of stack instances'''

    result = list()
    ss_list = list()

    try:
        cft_paginator = CFT.get_paginator('list_stack_instances')
        cft_page_iterator = cft_paginator.paginate(StackSetName=ss_name)
    except Exception as exe:
        LOGGER.error('Unable to list stack instances: %s', str(exe))

    for page in cft_page_iterator:
        ss_list += page['Summaries']

    for item in ss_list:
        result.append(item[key])

    result = list(dict.fromkeys(result))

    return result


def delete_stack_instances(ss_name, accounts, regions, retain=False):
    '''Delete all stack instances with in the stackset'''

    result = False
    ops = {
        'MaxConcurrentPercentage': 100,
        'FailureTolerancePercentage': 50
        }

    try:
        CFT.delete_stack_instances(StackSetName=ss_name, Accounts=accounts,
                                   Regions=regions, RetainStacks=retain,
                                   OperationPreferences=ops)
        result = True
    except Exception as exe:
        LOGGER.error('Unable to delete stackset: %s', str(exe))

    return result


def get_stack_operation_status(ss_name, operation_id):
    '''Wait and return the status of the operation'''

    count = 25
    status = 'UNKNOWN'

    while count > 0:
        count -= 1
        try:
            output = CFT.describe_stack_set_operation(StackSetName=ss_name,
                                                      OperationId=operation_id)
            status = output['StackSetOperation']['Status']
        except Exception as exe:
            count = 0
            LOGGER.error('Stackset operation check failed: %s', str(exe))

        if status == 'RUNNING':
            LOGGER.info('Stackset operation %s, waiting for 30 sec', status)
            sleep(30)
        elif status in ['FAILED', 'STOPPING', 'STOPPED', 'UNKNOWN']:
            LOGGER.error('Exception on stackset operation: %s', status)
            count = 0
            break
        elif status == 'SUCCEEDED':
            LOGGER.info('StackSet Operation Completed: %s', status)
            break

    return count > 0


def delete_stackset(ss_name):
    '''Delete all stack instances and delete stack set'''

    ss_delete = False
    ss_accounts = list()
    ss_regions = list()
    delete_status = False

    ss_list = list_all_stack_instances(ss_name)

    if len(ss_list) > 0:
        ss_accounts = [item["Account"] for item in ss_list]
        ss_regions = [item["Region"] for item in ss_list]
        ss_accounts = list(dict.fromkeys(ss_accounts))
        ss_regions = list(dict.fromkeys(ss_regions))
    else:
        LOGGER.warning('No stack instances found: %s - %s',
                       ss_name, ss_list)

    if len(ss_accounts) > 0 and len(ss_regions) > 0:
        ss_delete = delete_stack_instances(ss_name, ss_accounts,
                                           ss_regions)
    else:
        ss_delete = True

    if ss_delete:
        ss_list = list_all_stack_instances(ss_name)
        ss_count = len(ss_list)
        counter = 25

        while ss_count > 0:
            LOGGER.info('%s stacks to be deleted. Sleeping for 30 secs.',
                        ss_count)
            sleep(30)
            ss_list = list_all_stack_instances(ss_name)
            ss_count = len(ss_list)
            counter -= 1
            if counter == 0:
                LOGGER.error('FAILED to delete %s stacks. Timing out.',
                             ss_count)
                ss_count = 0

        if counter > 0:
            try:
                LOGGER.info('Deleting the StackSet: %s', ss_name)
                CFT.delete_stack_set(StackSetName=ss_name)
                delete_status = True
            except Exception as exe:
                LOGGER.error('Unable to delete the stackset %s', str(exe))

    return delete_status


def get_master_id():
    ''' Get the master Id from AWS Organization - Only on master'''

    master_id = None
    try:
        master_id = ORG.list_roots()['Roots'][0]['Arn'].rsplit(':')[4]
    except Exception as exe:
        LOGGER.error('Only Run on Organization Root: %s', str(exe))

    return master_id


def get_org_id():
    '''Return org-id'''

    result = None

    try:
        result = ORG.describe_organization()['Organization']['Id']
    except Exception as exe:
        LOGGER.error('Unable to get Org-id: %s', str(exe))

    return result


def launch_stackset(ss_name, template, params,
                    admin_role_arn, template_type='body'):
    ''' Launch Config Stackset on the Master Account '''

    result = True
    capabilities = ['CAPABILITY_IAM',
                    'CAPABILITY_NAMED_IAM',
                    'CAPABILITY_AUTO_EXPAND']
    description = 'Enable Config in additional regions'
    active_stack_sets = list_stack_sets()

    if ss_name not in active_stack_sets:
        try:
            LOGGER.info('Create Stack Set: %s', ss_name)
            if template_type == 'body':
                CFT.create_stack_set(StackSetName=ss_name,
                                     Description=description,
                                     TemplateBody=template, Parameters=params,
                                     AdministrationRoleARN=admin_role_arn,
                                     ExecutionRoleName=EXEC_ROLE,
                                     Capabilities=capabilities)
            else:
                CFT.create_stack_set(StackSetName=ss_name,
                                     Description=description,
                                     TemplateURL=template, Parameters=params,
                                     AdministrationRoleARN=admin_role_arn,
                                     ExecutionRoleName=EXEC_ROLE,
                                     Capabilities=capabilities)
        except ClientError as exe:
            if exe.response['Error']['Code'] == 'NameAlreadyExistsException':
                LOGGER.error("StackSet already exists: %s", str(exe))
            else:
                LOGGER.error("Unexpected error: %s", str(exe))
                result = False
    else:
        LOGGER.info('Given Stack Set already exist: %s', active_stack_sets)

    return result


def deploy_config_stackset(ss_name, admin_role_arn, regions, deploy_to):
    '''Deploy config stackset'''

    config_result = False
    stack_status = False
    result = False

    all_accounts = list_from_stack_instances(CONFIG_STACK, key='Account')
    LOGGER.info('List of AWS Accounts: %s', all_accounts)

    if CONFIG_STACK in list_stack_sets():
        config_body = get_stackset_body(CONFIG_STACK)
        config_params = get_stackset_parameters(CONFIG_STACK)
        LOGGER.info('Config ParamList: %s', config_params)
        config_result = launch_stackset(ss_name, config_body,
                                        config_params, admin_role_arn)
        LOGGER.info('Config Stackset: %s', config_result)
        if deploy_to != 'Future Only':
            LOGGER.info('Deploy To setting: %s', deploy_to)
            operation_id = add_stack_instance(ss_name, all_accounts,
                                              regions)
            LOGGER.info('Operation ID: %s', operation_id)
            stack_status = get_stack_operation_status(ss_name,
                                                      operation_id)
        else:
            LOGGER.info('Skipping current accounts: %s', deploy_to)
            stack_status = True
    else:
        LOGGER.error('StackSet %s not found: %s',
                     CONFIG_STACK, list_stack_sets())

    if config_result and stack_status:
        result = True

    return result


def deploy_cnfpack_stackset(ss_name, admin_role_arn,
                            sse_algorithm, kms_key, log_account_id):
    '''Deploy config stackset'''

    result = False
    cnf_params = list()

    key_dict = dict()
    key_dict['ParameterKey'] = 'OrgId'
    key_dict['ParameterValue'] = get_org_id()
    cnf_params.append(key_dict)
    key_dict = dict()
    key_dict['ParameterKey'] = 'SSEAlgorithm'
    key_dict['ParameterValue'] = sse_algorithm
    cnf_params.append(key_dict)
    key_dict = dict()
    key_dict['ParameterKey'] = 'KMSMasterKeyID'
    key_dict['ParameterValue'] = kms_key
    cnf_params.append(key_dict)

    result = launch_stackset(ss_name, CNFPACK_URL, cnf_params,
                             admin_role_arn, template_type='URL')
    LOGGER.info('Conformance Stack Set Status: %s', result)

    if result:
        operation_id = add_stack_instance(ss_name, [log_account_id],
                                          [MY_REGION])
        LOGGER.info('Operation ID: %s', operation_id)
        stack_status = get_stack_operation_status(ss_name, operation_id)

    if result and stack_status:
        result = True

    return result


def lambda_handler(event, context):
    '''Lambda Handler module'''

    LOGGER.info('EVENT Received: %s', event)
    admin_role_arn = 'arn:aws:iam::' + get_master_id() + \
                     ':role/service-role/AWSControlTowerStackSetRole'
    deploy_to = os.environ['DeployTo']
    regions = os.environ['RegionsToDeploy'].split(',')
    sse_algorithm = os.environ['SSEAlgorithm']
    kms_key = os.environ['KMSMasterKeyID']
    set_cnfpack = os.environ['SetupConformancePackEnv'].upper()
    log_account_id = os.environ['LogArchiveAccountId']
    custom_stack = os.environ['NewStackSetName']

    cnfpack_stack = 'CNFPACK-LOGARCHIVE-' + custom_stack
    config_result = False
    response_data = {}
    status = False

    if event['RequestType'] == 'Create':
        config_result = deploy_config_stackset(custom_stack, admin_role_arn,
                                               regions, deploy_to)
        if config_result and set_cnfpack == 'YES':
            cnfpack_result = deploy_cnfpack_stackset(cnfpack_stack,
                                                     admin_role_arn,
                                                     sse_algorithm,
                                                     kms_key,
                                                     log_account_id)
        else:
            LOGGER.error('SKIPING CnfPack: %s, %s', config_result, set_cnfpack)

        if config_result and cnfpack_result:
            status = True

    elif event['RequestType'] == 'Update':
        status = True

    elif event['RequestType'] == 'Delete':
        config_result = delete_stackset(custom_stack)
        cnfpack_result = delete_stackset(cnfpack_stack)

        if config_result and cnfpack_result:
            status = True

    if status:
        cfnresponse.send(event, context, cfnresponse.SUCCESS,
                         response_data, "CustomResourcePhysicalID")
    else:
        cfnresponse.send(event, context, cfnresponse.FAILED,
                         response_data, "CustomResourcePhysicalID")
