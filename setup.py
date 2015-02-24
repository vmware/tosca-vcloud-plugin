#########
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

from setuptools import setup

setup(
    zip_safe=True,
    name='cloudify-vcloud-plugin',
    version='1.0',
    packages=[
        'vcloud_plugin_common',
        'server_plugin',
        'network_plugin'
    ],
    license='LICENSE',
    description='Cloudify plugin for vmWare vCloud infrastructure.',
    install_requires=[
        'cloudify-plugins-common==3.1',
        'pyvcloud==9',
        'requests==2.4.3',
        'IPy==0.81'
    ]
)