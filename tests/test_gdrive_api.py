import os
import unittest
from unittest import mock

from tools import gdrive_api as g

ENV = {g.ENV_CLIENT_ID: "fake-id", g.ENV_CLIENT_SECRET: "fake-secret"}


class ClientConfigTests(unittest.TestCase):
    def test_env_first(self) -> None:
        with mock.patch.dict(os.environ, ENV), mock.patch.object(g, "_registry_user_env") as reg:
            cfg, source = g.load_client_config()
        reg.assert_not_called()
        self.assertEqual(source, "env")
        self.assertEqual(cfg["installed"]["client_id"], "fake-id")
        self.assertEqual(cfg["installed"]["client_secret"], "fake-secret")
        self.assertEqual(cfg["installed"]["redirect_uris"], ["http://localhost"])
        self.assertEqual(cfg["installed"]["token_uri"], g.TOKEN_URI)

    def test_registry_then_file(self) -> None:
        clean = {k: v for k, v in os.environ.items() if k not in ENV}
        with mock.patch.dict(os.environ, clean, clear=True):
            with mock.patch.object(g, "_registry_user_env", side_effect=lambda n: ENV[n]):
                self.assertEqual(g.load_client_config()[1], "реестр")
            with mock.patch.object(g, "_registry_user_env", return_value=None),                     mock.patch.object(g, "find_client_secret", side_effect=g.GdriveAuthError("нет")):
                with self.assertRaises(g.GdriveAuthError):
                    g.load_client_config()


if __name__ == "__main__":
    unittest.main()
