# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------
import unittest
import mock

from msrestazure.azure_exceptions import CloudError
from azure.mgmt.web.models import (SourceControl, HostNameBinding, Site, SiteConfig,
                                   HostNameSslState, SslState, Certificate,
                                   AddressResponse, HostingEnvironmentProfile)
from azure.mgmt.web import WebSiteManagementClient
from azure.cli.core.adal_authentication import AdalAuthentication
from knack.util import CLIError
from azure.cli.command_modules.appservice.custom import (set_deployment_user,
                                                         update_git_token, add_hostname,
                                                         update_site_configs,
                                                         get_external_ip,
                                                         view_in_browser,
                                                         sync_site_repo,
                                                         _match_host_names_from_cert,
                                                         bind_ssl_cert,
                                                         list_publish_profiles,
                                                         config_source_control,
                                                         show_webapp,
                                                         get_streaming_log,
                                                         download_historical_logs,
                                                         validate_linux_create_options)

# pylint: disable=line-too-long
from vsts_cd_manager.continuous_delivery_manager import ContinuousDeliveryResult


class TestWebappMocked(unittest.TestCase):
    def setUp(self):
        self.client = WebSiteManagementClient(AdalAuthentication(lambda: ('bearer', 'secretToken')), '123455678')

    @mock.patch('azure.cli.command_modules.appservice.custom.web_client_factory', autospec=True)
    def test_set_deployment_user_creds(self, client_factory_mock):
        class MockClient:
            def update_publishing_user(self, user):
                # Don't do an actual call, just return the incoming user
                return user

        client_factory_mock.return_value = MockClient()

        # action
        user = set_deployment_user(mock.MagicMock(), 'admin', 'verySecret1')

        # assert things get wired up with a result returned
        assert user.publishing_user_name == 'admin'
        assert user.publishing_password == 'verySecret1'

    @mock.patch('azure.cli.command_modules.appservice.custom.web_client_factory', autospec=True)
    def test_set_source_control_token(self, client_factory_mock):
        client_factory_mock.return_value = self.client
        self.client._client = mock.MagicMock()
        sc = SourceControl('not-really-needed', source_control_name='GitHub', token='veryNiceToken')
        self.client._client.send.return_value = FakedResponse(200)
        self.client._deserialize = mock.MagicMock()
        self.client._deserialize.return_value = sc

        # action
        result = update_git_token(mock.MagicMock(), 'veryNiceToken')

        # assert things gets wired up
        self.assertEqual(result.token, 'veryNiceToken')

    @mock.patch('azure.cli.command_modules.appservice.custom.web_client_factory', autospec=True)
    def test_set_domain_name(self, client_factory_mock):
        client_factory_mock.return_value = self.client
        # set up the return value for getting a webapp
        webapp = Site('westus')
        webapp.name = 'veryNiceWebApp'
        self.client.web_apps.get = lambda _, _1: webapp

        # set up the result value of putting a domain name
        domain = 'veryNiceDomain'
        binding = HostNameBinding(webapp.location,
                                  domain_id=domain,
                                  custom_host_name_dns_record_type='A',
                                  host_name_type='Managed')
        self.client.web_apps._client = mock.MagicMock()
        self.client.web_apps._client.send.return_value = FakedResponse(200)
        self.client.web_apps._deserialize = mock.MagicMock()
        self.client.web_apps._deserialize.return_value = binding
        # action
        result = add_hostname(mock.MagicMock(), 'g1', webapp.name, domain)

        # assert
        self.assertEqual(result.domain_id, domain)

    @mock.patch('azure.cli.command_modules.appservice.custom.web_client_factory', autospec=True)
    def test_get_external_ip_from_ase(self, client_factory_mock):
        client = mock.Mock()
        client_factory_mock.return_value = client
        cmd_mock = mock.MagicMock()
        # set up the web inside a ASE, with an ip based ssl binding
        host_env = HostingEnvironmentProfile('id11')
        host_env.name = 'ase1'
        host_env.resource_group = 'myRg'

        host_ssl_state = HostNameSslState(ssl_state=SslState.ip_based_enabled, virtual_ip='1.2.3.4')
        client.web_apps.get.return_value = Site('antarctica', hosting_environment_profile=host_env,
                                                host_name_ssl_states=[host_ssl_state])
        client.app_service_environments.list_vips.return_value = AddressResponse()

        # action
        result = get_external_ip(cmd_mock, 'myRg', 'myWeb')

        # assert, we return the virtual ip from the ip based ssl binding
        self.assertEqual('1.2.3.4', result['ip'])

        # tweak to have no ip based ssl binding, but it is in an internal load balancer
        host_ssl_state2 = HostNameSslState(ssl_state=SslState.sni_enabled)
        client.web_apps.get.return_value = Site('antarctica', hosting_environment_profile=host_env,
                                                host_name_ssl_states=[host_ssl_state2])
        client.app_service_environments.list_vips.return_value = AddressResponse(internal_ip_address='4.3.2.1')

        # action
        result = get_external_ip(cmd_mock, 'myRg', 'myWeb')

        # assert, we take the ILB address
        self.assertEqual('4.3.2.1', result['ip'])

        # tweak to have no ip based ssl binding, and not in internal load balancer
        host_ssl_state2 = HostNameSslState(ssl_state=SslState.sni_enabled)
        client.web_apps.get.return_value = Site('antarctica', hosting_environment_profile=host_env,
                                                host_name_ssl_states=[host_ssl_state2])
        client.app_service_environments.list_vips.return_value = AddressResponse(service_ip_address='1.1.1.1')

        # action
        result = get_external_ip(cmd_mock, 'myRg', 'myWeb')

        # assert, we take service ip
        self.assertEqual('1.1.1.1', result['ip'])

    @mock.patch('azure.cli.command_modules.appservice.custom.web_client_factory', autospec=True)
    @mock.patch('azure.cli.command_modules.appservice.custom._resolve_hostname_through_dns', autospec=True)
    def test_get_external_ip_from_dns(self, resolve_hostname_mock, client_factory_mock):
        client = mock.Mock()
        client_factory_mock.return_value = client

        # set up the web inside a ASE, with an ip based ssl binding
        site = Site('antarctica')
        site.default_host_name = 'myweb.com'
        client.web_apps.get.return_value = site

        # action
        get_external_ip(mock.MagicMock(), 'myRg', 'myWeb')

        # assert, we return the virtual ip from the ip based ssl binding
        resolve_hostname_mock.assert_called_with('myweb.com')

    @mock.patch('azure.cli.command_modules.appservice.custom.web_client_factory', autospec=True)
    @mock.patch('azure.cli.command_modules.appservice.vsts_cd_provider.ContinuousDeliveryManager', autospec=True)
    @mock.patch('azure.cli.command_modules.appservice.vsts_cd_provider.Profile', autospec=True)
    def test_config_source_control_vsts(self, profile_mock, cd_manager_mock, client_factory_mock):
        # Mock the result of get auth token (avoiding REST call)
        profile = mock.Mock()
        profile.get_subscription.return_value = {'id': 'id1', 'name': 'sub1', 'tenantId': 'tenant1'}
        profile.get_current_account_user.return_value = None
        profile.get_login_credentials.return_value = None, None, None
        profile.get_access_token_for_resource.return_value = None
        profile_mock.return_value = profile

        # Mock the cd manager class so no REST calls are made
        cd_manager = mock.Mock()
        status = ContinuousDeliveryResult(None, None, None, None, None, None, "message1", None,
                                          None, None)
        cd_manager.setup_continuous_delivery.return_value = status
        cd_manager_mock.return_value = cd_manager

        # Mock the client and set the location
        client = mock.Mock()
        client_factory_mock.return_value = client
        site = Site('antarctica')
        site.default_host_name = 'myweb.com'
        client.web_apps.get.return_value = site

        config_source_control(mock.MagicMock(), 'group1', 'myweb', 'http://github.com/repo1', None, None, None,
                              None, None, 'ASPNet', 'working_directory', 'Gulp', 'Django',
                              'Python 2.7.12 x64', True, 'https://account1.visualstudio.com',
                              None, 'slot1', None, None)
        cd_app_type_details = {
            'cd_app_type': 'ASPNet',
            'app_working_dir': 'working_directory',
            'nodejs_task_runner': 'Gulp',
            'python_framework': 'Django',
            'python_version': 'Python 2.7.12 x64'
        }
        cd_manager.setup_continuous_delivery.assert_called_with('slot1', cd_app_type_details,
                                                                'https://account1.visualstudio.com',
                                                                True, None, None, None)

    @mock.patch('azure.cli.command_modules.appservice.custom._generic_site_operation', autospec=True)
    def test_update_site_config(self, site_op_mock):
        site_config = SiteConfig('antarctica')
        site_op_mock.side_effect = [site_config, None]
        cmd = mock.MagicMock()
        # action
        update_site_configs(cmd, 'myRG', 'myweb', java_version='1.8')
        # assert
        config_for_set = site_op_mock.call_args_list[1][0][5]
        self.assertEqual(config_for_set.java_version, '1.8')
        # point check some unrelated properties should stay at None
        self.assertEqual(config_for_set.use32_bit_worker_process, None)
        self.assertEqual(config_for_set.java_container, None)

    @mock.patch('azure.cli.command_modules.appservice.custom._generic_site_operation', autospec=True)
    def test_list_publish_profiles_on_slots(self, site_op_mock):
        site_op_mock.return_value = [b'<publishData><publishProfile publishUrl="ftp://123"/><publishProfile publishUrl="ftp://1234"/></publishData>']
        # action
        result = list_publish_profiles(mock.MagicMock(), 'myRG', 'myweb', 'slot1')
        # assert
        site_op_mock.assert_called_with(mock.ANY, 'myRG', 'myweb', 'list_publishing_profile_xml_with_secrets', 'slot1')
        self.assertTrue(result[0]['publishUrl'].startswith('ftp://123'))

    @mock.patch('azure.cli.command_modules.appservice.custom._generic_site_operation', autospec=True)
    @mock.patch('azure.cli.command_modules.appservice.custom.get_streaming_log', autospec=True)
    @mock.patch('azure.cli.command_modules.appservice.custom.open_page_in_browser', autospec=True)
    def test_browse_with_trace(self, webbrowser_mock, log_mock, site_op_mock):
        site = Site('antarctica')
        site.default_host_name = 'haha.com'
        site.enabled_host_names = [site.default_host_name]
        site.host_name_ssl_states = [HostNameSslState('does not matter',
                                                      ssl_state=SslState.ip_based_enabled)]

        site_op_mock.return_value = site
        # action
        view_in_browser(mock.MagicMock(), 'myRG', 'myweb', logs=True)
        # assert
        webbrowser_mock.assert_called_with('https://haha.com')
        log_mock.assert_called_with(mock.ANY, 'myRG', 'myweb', None, None)

    @mock.patch('azure.cli.command_modules.appservice.custom._generic_site_operation', autospec=True)
    @mock.patch('azure.cli.command_modules.appservice.custom._rename_server_farm_props', autospec=True)
    @mock.patch('azure.cli.command_modules.appservice.custom._fill_ftp_publishing_url', autospec=True)
    def test_show_webapp(self, file_ftp_mock, rename_mock, site_op_mock):
        faked_web = mock.MagicMock()
        site_op_mock.return_value = faked_web
        # action
        result = show_webapp(mock.MagicMock(), 'myRG', 'myweb', slot=None, app_instance=None)
        # assert (we invoke the site op)
        self.assertEqual(faked_web, result)
        self.assertTrue(rename_mock.called)
        self.assertTrue(file_ftp_mock.called)

    @mock.patch('azure.cli.command_modules.appservice.custom._generic_site_operation', autospec=True)
    def test_sync_repository_skip_bad_error(self, site_op_mock):
        resp = FakedResponse(200)  # because of bad spec, sdk throws on 200.
        setattr(resp, 'text', '{"Message": ""}')
        site_op_mock.side_effect = CloudError(resp, error="bad error")
        # action
        sync_site_repo(mock.MagicMock(), 'myRG', 'myweb')
        # assert
        pass  # if we are here, it means CLI has captured the bogus exception

    def test_match_host_names_from_cert(self):
        result = _match_host_names_from_cert(['*.mysite.com'], ['admin.mysite.com', 'log.mysite.com', 'mysite.com'])
        self.assertEqual(set(['admin.mysite.com', 'log.mysite.com']), result)

        result = _match_host_names_from_cert(['*.mysite.com', 'mysite.com'], ['admin.mysite.com', 'log.mysite.com', 'mysite.com'])
        self.assertEqual(set(['admin.mysite.com', 'log.mysite.com', 'mysite.com']), result)

    @mock.patch('azure.cli.command_modules.appservice.custom._generic_site_operation', autospec=True)
    @mock.patch('azure.cli.command_modules.appservice.custom._get_scm_url', autospec=True)
    @mock.patch('threading.Thread', autospec=True)
    def test_log_stream_supply_cli_ctx(self, threading_mock, get_scm_url_mock, site_op_mock):

        # test exception to exit the streaming loop
        class ErrorToExitInfiniteLoop(Exception):
            pass

        threading_mock.side_effect = ErrorToExitInfiniteLoop('Expected error to exit early')
        get_scm_url_mock.return_value = 'http://great_url'
        cmd_mock = mock.MagicMock()
        cli_ctx_mock = mock.MagicMock()
        cmd_mock.cli_ctx = cli_ctx_mock

        try:
            # action
            get_streaming_log(cmd_mock, 'rg', 'web1')
            self.fail('test exception was not thrown')
        except ErrorToExitInfiniteLoop:
            # assert
            site_op_mock.assert_called_with(cli_ctx_mock, 'rg', 'web1', 'list_publishing_credentials', None)

    @mock.patch('azure.cli.command_modules.appservice.custom._generic_site_operation', autospec=True)
    @mock.patch('azure.cli.command_modules.appservice.custom._get_scm_url', autospec=True)
    @mock.patch('azure.cli.command_modules.appservice.custom._get_log', autospec=True)
    def test_download_log_supply_cli_ctx(self, get_log_mock, get_scm_url_mock, site_op_mock):
        def test_result():
            res = mock.MagicMock()
            res.publishing_user_name, res.publishing_password = 'great_user', 'secret_password'
            return res
        test_scm_url = 'http://great_url'
        get_scm_url_mock.return_value = test_scm_url
        publish_cred_mock = mock.MagicMock()
        publish_cred_mock.result = test_result
        site_op_mock.return_value = publish_cred_mock
        cmd_mock = mock.MagicMock()
        cli_ctx_mock = mock.MagicMock()
        cmd_mock.cli_ctx = cli_ctx_mock

        # action
        download_historical_logs(cmd_mock, 'rg', 'web1')

        # assert
        site_op_mock.assert_called_with(cli_ctx_mock, 'rg', 'web1', 'list_publishing_credentials', None)
        get_log_mock.assert_called_with(test_scm_url + '/dump', 'great_user', 'secret_password', None)

    def test_valid_linux_create_options(self):
        some_runtime = 'TOMCAT|8.5-jre8'
        test_docker_image = 'lukasz/great-image:123'
        test_multi_container_config = 'some_config.yaml'
        test_multi_container_type = 'COMPOSE'

        self.assertTrue(validate_linux_create_options(some_runtime, None, None, None))
        self.assertTrue(validate_linux_create_options(None, test_docker_image, None, None))
        self.assertTrue(validate_linux_create_options(None, None, test_multi_container_config, test_multi_container_type))
        self.assertFalse(validate_linux_create_options(some_runtime, None, test_multi_container_config, test_multi_container_type))
        self.assertFalse(validate_linux_create_options(some_runtime, None, test_multi_container_config, None))
        self.assertFalse(validate_linux_create_options(some_runtime, test_docker_image, test_multi_container_config, None))
        self.assertFalse(validate_linux_create_options(None, None, test_multi_container_config, None))
        self.assertFalse(validate_linux_create_options(None, None, None, None))


class FakedResponse(object):  # pylint: disable=too-few-public-methods
    def __init__(self, status_code):
        self.status_code = status_code


if __name__ == '__main__':
    unittest.main()
