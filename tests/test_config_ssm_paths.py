import os
import unittest
from unittest.mock import patch

from config import (
    KOTAK_SSM_PROFILE,
    SSM_BASE_PATH,
    SSM_LOGIN_PASSWORD_PATH,
    SSM_LOGIN_USERNAME_PATH,
    _kotak_ssm_path,
    load_kotak_credentials,
    load_login_credentials,
)


class TestConfigSsmPaths(unittest.TestCase):
    def test_default_login_paths(self):
        self.assertEqual(SSM_LOGIN_USERNAME_PATH, f"{SSM_BASE_PATH}/5pindra/loginusername")
        self.assertEqual(SSM_LOGIN_PASSWORD_PATH, f"{SSM_BASE_PATH}/5pindra/loginpassword")

    def test_default_kotak_paths(self):
        self.assertEqual(
            _kotak_ssm_path("KOTAK_CONSUMER_KEY_S"),
            f"{SSM_BASE_PATH}/kotak/{KOTAK_SSM_PROFILE}/KOTAK_CONSUMER_KEY_S",
        )

    @patch("config._get_ssm_param")
    def test_load_login_from_ssm(self, mock_ssm):
        mock_ssm.side_effect = lambda path: {
            SSM_LOGIN_USERNAME_PATH: "ui-user",
            SSM_LOGIN_PASSWORD_PATH: "ui-pass",
        }[path]
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LOGIN_USERNAME_5P", None)
            os.environ.pop("LOGIN_PASSWORD_5P", None)
            creds = load_login_credentials()
        self.assertEqual(creds["login_username"], "ui-user")
        self.assertEqual(creds["login_password"], "ui-pass")

    @patch("config._get_ssm_param")
    def test_load_kotak_from_ssm(self, mock_ssm):
        base = f"{SSM_BASE_PATH}/kotak/{KOTAK_SSM_PROFILE}"
        mock_ssm.side_effect = lambda path: {
            f"{base}/KOTAK_CONSUMER_KEY_S": "ck",
            f"{base}/KOTAK_MOBILE_S": "+919999999999",
            f"{base}/KOTAK_UCC_S": "UCC1",
            f"{base}/KOTAK_MPIN_S": "123456",
            f"{base}/KOTAK_TOTP_SECRET": "GEZDGNBVGY3TQOJQ",
        }[path]
        with patch.dict(os.environ, {}, clear=False):
            for key in (
                "KOTAK_CONSUMER_KEY_S",
                "KOTAK_MOBILE_S",
                "KOTAK_UCC_S",
                "KOTAK_MPIN_S",
                "KOTAK_TOTP_SECRET",
            ):
                os.environ.pop(key, None)
            creds = load_kotak_credentials()
        self.assertEqual(creds["consumer_key"], "ck")
        self.assertEqual(creds["ucc"], "UCC1")
        self.assertEqual(creds["totp_secret"], "GEZDGNBVGY3TQOJQ")
