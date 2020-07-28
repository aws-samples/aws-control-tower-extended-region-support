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
Lambda to handle lifecycle events
'''

import json
import logging
from time import sleep
import os
import boto3
from botocore.exceptions import ClientError

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)
CFT = boto3.client('cloudformation')
SSM = boto3.client('ssm')


def list_parameters():
    '''List all parameters'''

    param_list = list()
    result = list()

    try:
        ssm_paginator = SSM.get_paginator('describe_parameters')
        ssm_page_iterator = ssm_paginator.paginate()
    except Exception as exe:
        LOGGER.error('Unable to list parameters %s', str(exe))

    for page in ssm_page_iterator:
        param_list += page['Parameters']

    for param in param_list:
        result.append(param['Name'])

    return result


def get_param_value(param_name):
    '''Return list of parameter value '''

    result = list()

    if param_name in list_parameters():
        try:
            result = SSM.get_parameter(
                Name=param_name)['Parameter']['Value'].split(',')
        except Exception as exe:
            LOGGER.error('Unable to get parameter value: %s', str(exe))
    else:
        LOGGER.error('Unable to find the parameter: %s', param_name)

    return result


def does_stack_set_exists(ss_name):
    '''Return True if active StackSet exists'''
    result = False
    ss_list = list()

    try:
        cft_paginator = CFT.get_paginator('list_stack_sets')
        cft_page_iterator = cft_paginator.paginate()
    except Exception as exe:
        LOGGER.error('Unable to list stacksets %s', str(exe))

    for page in cft_page_iterator:
        ss_list += page['Summaries']

    for item in ss_list:
        if item['StackSetName'] == ss_name and item['Status'] == 'ACTIVE':
            result = True

    return result


def add_stack_instance(ss_name, accounts, regions):
    ''' Adds StackSet Instances '''

    result = {'OperationId': None}
    ops = {
        'MaxConcurrentPercentage': 100,
        'FailureTolerancePercentage': 20
        }
    output = does_stack_set_exists(ss_name)

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


def lambda_handler(event, context):
    '''Lambda Handler to process life cycle event'''

    LOGGER.info('Event: %s, Context: %s', event, context)

    ss_name = os.environ['NewStackSetName']
    param_name = os.environ['RegionsToDeploy']
    regions = get_param_value(param_name)
    event_info = json.loads(event['Records'][0]['body'])
    event_details = event_info['detail']
    event_name = event_details['eventName']
    srv_event_details = event_details['serviceEventDetails']
    response_data = {}

    if event_name == 'CreateManagedAccount':
        new_account_info = srv_event_details['createManagedAccountStatus']
        cmd_status = new_account_info['state']
        if cmd_status == 'SUCCEEDED':
            LOGGER.info('Sucessful event recieved: %s', event)
            account_id = new_account_info['account']['accountId']
            operation_id = add_stack_instance(ss_name, [account_id], regions)
            response_data['Result'] = cmd_status
            get_stack_operation_status(ss_name, operation_id)
        else:
            LOGGER.info('Unsucessful event recieved. SKIPPING: %s', event)
    else:
        LOGGER.info('Unexpected life cycle event captured: %s', event)
