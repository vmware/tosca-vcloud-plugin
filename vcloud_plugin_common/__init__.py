# Copyright (c) 2014 GigaSpaces Technologies Ltd. All rights reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
#  * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  * See the License for the specific language governing permissions and
#  * limitations under the License.

import atexit
from functools import wraps
import yaml
import os
import requests
import time

from pyvcloud import vcloudair
from pyvcloud.schema.vcd.v1_5.schemas.vcloud import taskType

from cloudify import ctx
from cloudify import context
from cloudify import exceptions as cfy_exc

TASK_RECHECK_TIMEOUT = 2
RELOGIN_TIMEOUT = 3
TASK_STATUS_SUCCESS = 'success'
TASK_STATUS_ERROR = 'error'

STATUS_COULD_NOT_BE_CREATED = -1
STATUS_UNRESOLVED = 0
STATUS_RESOLVED = 1
STATUS_DEPLOYED = 2
STATUS_SUSPENDED = 3
STATUS_POWERED_ON = 4
STATUS_POWERED_OFF = 8
STATUS_WAITING_FOR_USER_INPUT = 5
STATUS_UNKNOWN_STATE = 6
STATUS_UNRECOGNIZED_STATE = 7
STATUS_INCONSISTENT_STATE = 9

VCLOUD_STATUS_MAP = {
    -1: "Could not be created",
    0: "Unresolved",
    1: "Resolved",
    2: "Deployed",
    3: "Suspended",
    4: "Powered on",
    5: "Waiting for user input",
    6: "Unknown state",
    7: "Unrecognized state",
    8: "Powered off",
    9: "Inconsistent state",
    10: "Children do not all have the same status",
    11: "Upload initiated, OVF descriptor pending",
    12: "Upload initiated, copying contents",
    13: "Upload initiated , disk contents pending",
    14: "Upload has been quarantined",
    15: "Upload quarantine period has expired"
}

SUBSCRIPTION_SERVICE_TYPE = 'subscription'
ONDEMAND_SERVICE_TYPE = 'ondemand'
PRIVATE_SERVICE_TYPE = 'vcd'


def transform_resource_name(res, ctx):
    """
        return name as prefix from bootstrap context + resource name
    """
    if isinstance(res, basestring):
        res = {'name': res}

    if not isinstance(res, dict):
        raise ValueError("transform_resource_name() expects either string or "
                         "dict as the first parameter")

    pfx = ctx.bootstrap_context.resources_prefix

    if not pfx:
        return get_mandatory(res, 'name')

    name = get_mandatory(res, 'name')
    res['name'] = pfx + name

    if name.startswith(pfx):
        ctx.logger.warn("Prefixing resource '{0}' with '{1}' but it "
                        "already has this prefix".format(name, pfx))
    else:
        ctx.logger.info("Transformed resource name '{0}' to '{1}'".format(
                        name, res['name']))

    return res['name']


class Config(object):
    """
        load global config
    """

    VCLOUD_CONFIG_PATH_ENV_VAR = 'VCLOUD_CONFIG_PATH'
    VCLOUD_CONFIG_PATH_DEFAULT = '~/vcloud_config.yaml'

    def get(self):
        """
            return settings from ~/vcloud_config.yaml
        """
        cfg = {}
        env_name = self.VCLOUD_CONFIG_PATH_ENV_VAR
        default_location_tpl = self.VCLOUD_CONFIG_PATH_DEFAULT
        default_location = os.path.expanduser(default_location_tpl)
        config_path = os.getenv(env_name, default_location)
        try:
            with open(config_path) as f:
                cfg = yaml.load(f.read())
        except IOError:
            pass
        return cfg


class VcloudAirClient(object):

    config = Config
    LOGIN_RETRY_NUM = 5

    def get(self, config=None, *args, **kw):
        """
            return new vca client
        """
        static_config = self.__class__.config().get()
        cfg = {}
        cfg.update(static_config)
        if config:
            cfg.update(config)
        return self.connect(cfg)

    def connect(self, cfg):
        """
            login to instance described in settings
        """
        url = cfg.get('url')
        username = cfg.get('username')
        password = cfg.get('password')
        token = cfg.get('token')
        service = cfg.get('service')
        org_name = cfg.get('org')
        service_type = cfg.get('service_type', SUBSCRIPTION_SERVICE_TYPE)
        instance = cfg.get('instance')
        org_url = cfg.get('org_url', None)
        api_version = cfg.get('api_version', '5.6')
        if not (all([url, token]) or all([url, username, password])):
            raise cfy_exc.NonRecoverableError(
                "Login credentials must be specified")
        if (service_type == SUBSCRIPTION_SERVICE_TYPE and not (
            service and org_name
        )):
            raise cfy_exc.NonRecoverableError(
                "vCloud service and vDC must be specified")

        if service_type == SUBSCRIPTION_SERVICE_TYPE:
            vcloud_air = self._subscription_login(
                url, username, password, token, service, org_name)
        elif service_type == ONDEMAND_SERVICE_TYPE:
            vcloud_air = self._ondemand_login(
                url, username, password, token, instance)
        # The actual service type for private is 'vcd', but we should accept
        # 'private' as well, for user friendliness of inputs
        elif service_type in (PRIVATE_SERVICE_TYPE, 'private'):
            vcloud_air = self._private_login(
                url, username, password, token, org_name, org_url, api_version)
        else:
            raise cfy_exc.NonRecoverableError(
                "Unrecognized service type: {0}".format(service_type))
        return vcloud_air

    def _subscription_login(self, url, username, password, token, service,
                            org_name):
        """
            login to subscription service
        """
        logined = False
        vdc_logined = False

        vca = vcloudair.VCA(
            url, username, service_type=SUBSCRIPTION_SERVICE_TYPE,
            version='5.6')

        # login with token
        if token:
            for _ in range(self.LOGIN_RETRY_NUM):
                logined = vca.login(token=token)
                if logined is False:
                    ctx.logger.info("Login using token failed.")
                    time.sleep(RELOGIN_TIMEOUT)
                    continue
                else:
                    ctx.logger.info("Login using token successful.")
                    break

        # outdated token, try login by password
        if logined is False and password:
            for _ in range(self.LOGIN_RETRY_NUM):
                logined = vca.login(password)
                if logined is False:
                    ctx.logger.info("Login using password failed. Retrying...")
                    time.sleep(RELOGIN_TIMEOUT)
                    continue
                else:
                    ctx.logger.info("Login using password successful.")
                    break

        # can't login to system at all
        if logined is False:
            raise cfy_exc.NonRecoverableError("Invalid login credentials")

        for _ in range(self.LOGIN_RETRY_NUM):
            vdc_logined = vca.login_to_org(service, org_name)
            if vdc_logined is False:
                ctx.logger.info("Login to VDC failed. Retrying...")
                time.sleep(RELOGIN_TIMEOUT)
                continue
            else:
                ctx.logger.info("Login to VDC successful.")
                break

        # we can login to system,
        # but have some troubles with login to organization,
        # lets retry later
        if vdc_logined is False:
            raise cfy_exc.RecoverableError(message="Could not login to VDC",
                                           retry_after=RELOGIN_TIMEOUT)

        atexit.register(vca.logout)
        return vca

    def _ondemand_login(self, url, username, password, token, instance_id):
        """
            login to ondemand service
        """
        def get_instance(vca, instance_id):
            instances = vca.get_instances() or []
            for instance in instances:
                if instance['id'] == instance_id:
                    return instance

        if instance_id is None:
            raise cfy_exc.NonRecoverableError(
                "Instance ID should be specified for OnDemand login")
        logined = False
        instance_logined = False

        vca = vcloudair.VCA(
            url, username, service_type=ONDEMAND_SERVICE_TYPE, version='5.7')

        # login with token
        if token:
            for _ in range(self.LOGIN_RETRY_NUM):
                logined = vca.login(token=token)
                if logined is False:
                    ctx.logger.info("Login using token failed.")
                    time.sleep(RELOGIN_TIMEOUT)
                    continue
                else:
                    ctx.logger.info("Login using token successful.")
                    break

        # outdated token, try login by password
        if logined is False and password:
            for _ in range(self.LOGIN_RETRY_NUM):
                logined = vca.login(password)
                if logined is False:
                    ctx.logger.info("Login using password failed. Retrying...")
                    time.sleep(RELOGIN_TIMEOUT)
                    continue
                else:
                    ctx.logger.info("Login using password successful.")
                    break

        # can't login to system at all
        if logined is False:
            raise cfy_exc.NonRecoverableError("Invalid login credentials")

        instance = get_instance(vca, instance_id)
        if instance is None:
            raise cfy_exc.NonRecoverableError(
                "Instance {0} could not be found.".format(instance_id))

        for _ in range(self.LOGIN_RETRY_NUM):
            instance_logined = vca.login_to_instance(
                instance_id, password, token, None)
            if instance_logined is False:
                ctx.logger.info("Login to instance failed. Retrying...")
                time.sleep(RELOGIN_TIMEOUT)
                continue
            else:
                ctx.logger.info("Login to instance successful.")
                break

        for _ in range(self.LOGIN_RETRY_NUM):

            instance_logined = vca.login_to_instance(
                instance_id,
                None,
                vca.vcloud_session.token,
                vca.vcloud_session.org_url)
            if instance_logined is False:
                ctx.logger.info("Login to instance failed. Retrying...")
                time.sleep(RELOGIN_TIMEOUT)
                continue
            else:
                ctx.logger.info("Login to instance successful.")
                break

        # we can login to system,
        # but have some troubles with login to instance,
        # lets retry later
        if instance_logined is False:
            raise cfy_exc.RecoverableError(
                message="Could not login to instance",
                retry_after=RELOGIN_TIMEOUT)

        atexit.register(vca.logout)
        return vca

    def _private_login(self, url, username, password, token, org_name,
                       org_url=None, api_version='5.6'):
        """
            login to private instance
        """
        logined = False

        vca = vcloudair.VCA(
            host=url,
            username=username,
            service_type=PRIVATE_SERVICE_TYPE,
            version=api_version)

        if logined is False and password:
            for _ in range(self.LOGIN_RETRY_NUM):
                logined = vca.login(password, org=org_name)
                if logined is False:
                    ctx.logger.info("Login using password failed. Retrying...")
                    time.sleep(RELOGIN_TIMEOUT)
                    continue
                else:
                    token = vca.token
                    # Set org_url based on the session, no matter what was
                    # passed in to the application, as this is guaranteed to
                    # be correct
                    org_url = vca.vcloud_session.org_url
                    ctx.logger.info("Login using password successful.")
                    break

        # Private mode requires being logged in with a token otherwise you
        # don't seem to be able to retrieve any VDCs
        if token:
            for _ in range(self.LOGIN_RETRY_NUM):
                logined = vca.login(token=token, org_url=org_url)
                if logined is False:
                    ctx.logger.info("Login using token failed.")
                    time.sleep(RELOGIN_TIMEOUT)
                    continue
                else:
                    ctx.logger.info("Login using token successful.")
                    break

        if logined is False:
            raise cfy_exc.NonRecoverableError("Invalid login credentials")

        atexit.register(vca.logout)
        return vca


def with_vca_client(f):
    """
        add vca client to function params
    """
    @wraps(f)
    def wrapper(*args, **kw):
        config = None
        if ctx.type == context.NODE_INSTANCE:
            config = ctx.node.properties.get('vcloud_config')
        elif ctx.type == context.RELATIONSHIP_INSTANCE:
            config = ctx.source.node.properties.get('vcloud_config')
        else:
            raise cfy_exc.NonRecoverableError("Unsupported context")
        client = VcloudAirClient().get(config=config)
        kw['vca_client'] = client
        return f(*args, **kw)
    return wrapper


def wait_for_task(vca_client, task):
    """
        check status of current task and make request for recheck
        task status in case when we have not well defined state
        (not error and not success)
    """
    status = task.get_status()
    while status != TASK_STATUS_SUCCESS:
        if status == TASK_STATUS_ERROR:
            error = task.get_Error()
            raise cfy_exc.NonRecoverableError(
                "Error during task execution: {0}".format(error.get_message()))
        else:
            time.sleep(TASK_RECHECK_TIMEOUT)
            response = requests.get(
                task.get_href(),
                headers=vca_client.vcloud_session.get_vcloud_headers())
            task = taskType.parseString(response.content, True)
            status = task.get_status()


def get_vcloud_config():
    """
        get vcloud config from node properties
    """
    config = None
    if ctx.type == context.NODE_INSTANCE:
        config = ctx.node.properties.get('vcloud_config')
    elif ctx.type == context.RELATIONSHIP_INSTANCE:
        config = ctx.source.node.properties.get('vcloud_config')
    else:
        raise cfy_exc.NonRecoverableError("Unsupported context")
    static_config = Config().get()
    if config:
        static_config.update(config)
    return static_config


def get_mandatory(obj, parameter):
    """
        return value for field or raise exception if field does not exist
    """
    value = obj.get(parameter)
    if value:
        return value
    else:
        raise cfy_exc.NonRecoverableError(
            "Mandatory parameter {0} is absent".format(parameter))


def is_subscription(service_type):
    """
        check service type is subscription or empty
    """
    return not service_type or service_type == SUBSCRIPTION_SERVICE_TYPE


def is_ondemand(service_type):
    """
        check service type is ondemand
    """
    return service_type == ONDEMAND_SERVICE_TYPE
