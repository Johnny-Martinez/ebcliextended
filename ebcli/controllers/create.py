# Copyright 2014 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You
# may not use this file except in compliance with the License. A copy of
# the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is
# distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF
# ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.

import re
import argparse

from ..core.abstractcontroller import AbstractBaseController
from ..resources.strings import strings, prompts, flag_text
from ..lib import elasticbeanstalk, utils
from ..objects.exceptions import NotFoundError, AlreadyExistsError, \
    InvalidOptionsError
from ..core import io, fileoperations
from ..objects.tier import Tier
from ..objects.requests import CreateEnvironmentRequest
from ..operations import saved_configs, commonops, createops


class CreateController(AbstractBaseController):
    class Meta:
        label = 'create'
        description = strings['create.info']
        epilog = strings['create.epilog']
        arguments = [
            (['environment_name'], dict(
                action='store', nargs='?', default=None,
                help=flag_text['create.name'])),
            (['-c', '--cname'], dict(help=flag_text['create.cname'])),
            (['-t', '--tier'], dict(help=flag_text['create.tier'])),
            (['-i', '--instance_type'], dict(
                help=flag_text['create.itype'])),
            (['-p', '--platform'], dict(help=flag_text['create.platform'])),
            (['-s', '--single'], dict(
                action='store_true', help=flag_text['create.single'])),
            (['--sample'], dict(
                action='store_true', help=flag_text['create.sample'])),
            (['-d', '--branch_default'], dict(
                action='store_true', help=flag_text['create.default'])),
            (['-ip', '--instance_profile'], dict(
                help=flag_text['create.iprofile'])),
            (['--version'], dict(help=flag_text['create.version'])),
            (['-k', '--keyname'], dict(help=flag_text['create.keyname'])),
            (['--scale'], dict(type=int, help=flag_text['create.scale'])),
            (['-nh', '--nohang'], dict(
                action='store_true', help=flag_text['create.nohang'])),
            (['--timeout'], dict(type=int, help=flag_text['general.timeout'])),
            (['--tags'], dict(help=flag_text['create.tags'])),
            (['--envvars'], dict(help=flag_text['create.envvars'])),
            (['--cfg'], dict(help=flag_text['create.config'])),
            (['-db', '--database'], dict(
                action="store_true", help=flag_text['create.database'])),
            ## Add addition hidden db commands
            (['-db.user', '--database.username'], dict(dest='db_user',
                                                   help=argparse.SUPPRESS)),
            (['-db.pass', '--database.password'],
                dict(dest='db_pass', help=argparse.SUPPRESS)),
            (['-db.i', '--database.instance'],
                dict(dest='db_instance', help=argparse.SUPPRESS)),
            (['-db.version', '--database.version'],
                dict(dest='db_version', help=argparse.SUPPRESS)),
            (['-db.size', '--database.size'],
                dict(type=int, dest='db_size', help=argparse.SUPPRESS)),
            (['-db.engine', '--database.engine'],
                dict(dest='db_engine', help=argparse.SUPPRESS)),
            (['--vpc'], dict(action='store_true',
                             help=flag_text['create.vpc'])),
            (['--vpc.id'], dict(dest='vpc_id', help=argparse.SUPPRESS)),
            (['--vpc.ec2subnets'], dict(
                dest='vpc_ec2subnets', help=argparse.SUPPRESS)),
            (['--vpc.elbsubnets'], dict(
                dest='vpc_elbsubnets', help=argparse.SUPPRESS)),
            (['--vpc.elbpublic'], dict(
                action='store_true', dest='vpc_elbpublic',
                help=argparse.SUPPRESS)),
            (['--vpc.publicip'], dict(
                action='store_true', dest='vpc_publicip',
                help=argparse.SUPPRESS)),
            (['--vpc.securitygroups'], dict(
                dest='vpc_securitygroups', help=argparse.SUPPRESS)),
            (['--vpc.dbsubnets'], dict(
                dest='vpc_dbsubnets', help=argparse.SUPPRESS)),

        ]
    print "controller CREATE here"
    def do_command(self):
        # save command line args
        env_name = self.app.pargs.environment_name
        cname = self.app.pargs.cname
        tier = self.app.pargs.tier
        itype = self.app.pargs.instance_type
        solution_string = self.app.pargs.platform
        single = self.app.pargs.single
        iprofile = self.app.pargs.instance_profile
        label = self.app.pargs.version
        branch_default = self.app.pargs.branch_default
        key_name = self.app.pargs.keyname
        sample = self.app.pargs.sample
        nohang = self.app.pargs.nohang
        tags = self.app.pargs.tags
        envvars = self.app.pargs.envvars
        scale = self.app.pargs.scale
        timeout = self.app.pargs.timeout
        cfg = self.app.pargs.cfg
        flag = False if env_name else True

        provided_env_name = env_name is not None

        if sample and label:
            raise InvalidOptionsError(strings['create.sampleandlabel'])

        if single and scale:
            raise InvalidOptionsError(strings['create.singleandsize'])

        app_name = self.get_app_name()

        # get tags
        tags = get_and_validate_tags(tags)

        #load solution stack
        if not solution_string:
            solution_string = fileoperations.get_default_solution_stack()

        # Test out sstack and tier before we ask any questions (Fast Fail)
        if solution_string:
            try:
                solution = commonops.get_solution_stack(solution_string)
            except NotFoundError:
                raise NotFoundError('Solution stack ' + solution_string +
                                    ' does not appear to be valid')

        if tier:
            if 'worker' in tier.lower() and cname:
                raise InvalidOptionsError(strings['worker.cname'])
            try:
                tier = Tier.parse_tier(tier)
            except NotFoundError:
                raise NotFoundError('Provided tier ' + tier + ' does not '
                                    'appear to be valid')

        if cname:
            if not commonops.is_cname_available(cname):
                raise AlreadyExistsError(strings['cname.unavailable'].
                                         replace('{cname}', cname))

        # If we still dont have what we need, ask for it
        if not env_name:
            # default is app-name plus '-dev'
            default_name = app_name + '-dev'
            current_environments = commonops.get_all_env_names()
            unique_name = utils.get_unique_name(default_name,
                                                current_environments)
            env_name = io.prompt_for_environment_name(unique_name)

        if not solution_string:
            solution = commonops.prompt_for_solution_stack()

        # Get template if applicable
        template_name = get_template_name(app_name, cfg)
        if template_name:
            template_contents = elasticbeanstalk.describe_template(
                app_name, template_name, platform=solution.name)

            if template_contents['Tier']['Name'] == 'Worker':
                tier = Tier.parse_tier('worker')

        if not tier or tier.name.lower() == 'webserver':
            if not cname and not provided_env_name:
                cname = get_cname(env_name)
            elif not cname:
                cname = None

        if not key_name:
            key_name = fileoperations.get_default_keyname()


        database = self.form_database_object()
        vpc = self.form_vpc_object()
        envvars = get_and_validate_envars(envvars)


        env_request = CreateEnvironmentRequest(
            app_name=app_name,
            env_name=env_name,
            cname=cname,
            template_name=template_name,
            platform=solution,
            tier=tier,
            instance_type=itype,
            version_label=label,
            instance_profile=iprofile,
            single_instance=single,
            key_name=key_name,
            sample_application=sample,
            tags=tags,
            scale=scale,
            database=database,
            vpc=vpc)

        env_request.option_settings += envvars
        createops.make_new_env(env_request,
                                branch_default=branch_default, nohang=nohang,
                                interactive=flag,
                                timeout=timeout)

    def complete_command(self, commands):
        app_name = fileoperations.get_application_name()

        self.complete_region(commands)

        # We only care about top command, because there are no positional
        ## args for this command
        cmd = commands[-1]
        if cmd in ['-t', '--tier']:
            io.echo(*Tier.get_all_tiers())
        if cmd in ['-s', '--solution']:
            io.echo(*elasticbeanstalk.get_available_solution_stacks())
        if cmd in ['-vl', '--versionlabel']:
            io.echo(*commonops.get_app_version_labels(app_name))

    def form_database_object(self):
        create_db = self.app.pargs.database
        username = self.app.pargs.db_user
        password = self.app.pargs.db_pass
        engine = self.app.pargs.db_engine
        size = self.app.pargs.db_size
        instance = self.app.pargs.db_instance
        version = self.app.pargs.db_version

        # Do we want a database?
        if create_db or username or password or engine or size \
                or instance or version:
            db_object = dict()
            if not username:
                io.echo()
                username = io.get_input(prompts['rds.username'],
                                        default='ebroot')
            if not password:
                password = io.get_pass(prompts['rds.password'])
            db_object['username'] = username
            db_object['password'] = password
            db_object['engine'] = engine
            db_object['size'] = str(size) if size else None
            db_object['instance'] = instance
            db_object['version'] = version
            return db_object
        else:
            return {}

    def form_vpc_object(self):
        vpc = self.app.pargs.vpc
        vpc_id = self.app.pargs.vpc_id
        ec2subnets = self.app.pargs.vpc_ec2subnets
        elbsubnets = self.app.pargs.vpc_elbsubnets
        elbpublic = self.app.pargs.vpc_elbpublic
        publicip = self.app.pargs.vpc_publicip
        securitygroups = self.app.pargs.vpc_securitygroups
        dbsubnets = self.app.pargs.vpc_dbsubnets
        database = self.app.pargs.database

        if vpc:
            # Interactively ask for vpc settings
            io.echo()
            if not vpc_id:
                vpc_id = io.get_input(prompts['vpc.id'])
            if not publicip:
                publicip = io.get_boolean_response(
                    text=prompts['vpc.publicip'])
            if not ec2subnets:
                ec2subnets = io.get_input(prompts['vpc.ec2subnets'])
            if not elbsubnets:
                elbsubnets = io.get_input(prompts['vpc.elbsubnets'])
            if not securitygroups:
                securitygroups = io.get_input(prompts['vpc.securitygroups'])
            if not elbpublic:
                publicip = io.get_boolean_response(
                    text=prompts['vpc.elbpublic'])
            if not dbsubnets and database:
                dbsubnets = io.get_input(prompts['vpc.dbsubnets'])

        if vpc_id or vpc:
            vpc_object = dict()
            vpc_object['id'] = vpc_id
            vpc_object['ec2subnets'] = ec2subnets
            vpc_object['elbsubnets'] = elbsubnets
            vpc_object['elbscheme'] = 'public' if elbpublic else 'internal'
            vpc_object['publicip'] = 'true' if publicip else 'false'
            vpc_object['securitygroups'] = securitygroups
            vpc_object['dbsubnets'] = dbsubnets
            return vpc_object

        else:
            return {}


def get_cname(env_name):
    while True:
        cname = io.prompt_for_cname(default=env_name)
        if not cname:
            # Reverting to default
            break
        if not commonops.is_cname_available(cname):
            io.echo('That cname is not available. '
                    'Please choose another.')
        else:
            break
    return cname


def get_and_validate_tags(tags):
    if not tags:
        return []

    tags = tags.strip().strip('"').strip('\'')
    tags = tags.split(',')
    tag_list = []
    if len(tags) > 7:
        raise InvalidOptionsError(strings['tags.max'])
    for t in tags:
        # validate
        if not re.match('^[\w\s.:/+%@-]{1,128}=[\w\s.:/+%@-]{0,256}$', t):
            raise InvalidOptionsError(strings['tags.invalidformat'])
        else:
            # build tag
            key, value = t.split('=')
            tag_list.append(
                {'Key': key,
                 'Value': value}
            )
    return tag_list


def get_and_validate_envars(envvars):
    if not envvars:
        return []

    envvars = envvars.strip().strip('"').strip('\'')
    envvars = envvars.split(',')

    options, options_to_remove = commonops.create_envvars_list(envvars)
    return options


def get_template_name(app_name, cfg):
    if not cfg:
        # See if a default template exists
        if saved_configs.resolve_config_location('default') is None:
            return None
        else:
            cfg = 'default'
    return saved_configs.resolve_config_name(app_name, cfg)
