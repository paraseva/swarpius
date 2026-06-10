"""ANALYSER_BATCH_SIZE is honoured by both the passive analyser and the
on-demand scan path, with matching fallback behaviour.
"""

import os
import unittest
from unittest.mock import patch

from analyser import analyse  # noqa: E402
from app.analysis.browser import _resolve_batch_size  # noqa: E402


class TestAnalyserResolveBatchSize(unittest.TestCase):
    def test_defaults_to_five_when_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANALYSER_BATCH_SIZE", None)
            assert analyse.resolve_batch_size() == 5

    def test_honours_env_value(self):
        with patch.dict(os.environ, {"ANALYSER_BATCH_SIZE": "10"}):
            assert analyse.resolve_batch_size() == 10

    def test_falls_back_on_non_numeric(self):
        with patch.dict(os.environ, {"ANALYSER_BATCH_SIZE": "oops"}):
            assert analyse.resolve_batch_size() == 5

    def test_falls_back_on_non_positive(self):
        for bad in ("0", "-3"):
            with patch.dict(os.environ, {"ANALYSER_BATCH_SIZE": bad}):
                assert analyse.resolve_batch_size() == 5

    def test_read_at_call_time(self):
        with patch.dict(os.environ, {"ANALYSER_BATCH_SIZE": "3"}):
            assert analyse.resolve_batch_size() == 3
        with patch.dict(os.environ, {"ANALYSER_BATCH_SIZE": "7"}):
            assert analyse.resolve_batch_size() == 7


class TestOnDemandResolveBatchSize(unittest.TestCase):
    """The on-demand path resolves through ``app.settings``, which is
    locked at first access. Each test gets a clean settings cache (see
    autouse fixture in conftest.py); within a test we reset between
    env mutations to exercise different values."""

    def test_defaults_to_five_when_unset(self):
        from app.settings import reset_settings_for_tests
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANALYSER_BATCH_SIZE", None)
            reset_settings_for_tests()
            assert _resolve_batch_size() == 5

    def test_honours_env_value(self):
        from app.settings import reset_settings_for_tests
        with patch.dict(os.environ, {"ANALYSER_BATCH_SIZE": "12"}):
            reset_settings_for_tests()
            assert _resolve_batch_size() == 12

    def test_falls_back_on_garbage(self):
        from app.settings import reset_settings_for_tests
        for bad in ("", "  ", "nope", "0", "-1"):
            with patch.dict(os.environ, {"ANALYSER_BATCH_SIZE": bad}):
                reset_settings_for_tests()
                assert _resolve_batch_size() == 5


class TestContractParity(unittest.TestCase):
    """Passive and on-demand paths must agree on the resolved value when
    each is given the same env. The on-demand path now locks its value
    via ``app.settings``, so we reset the cache between iterations to
    test parity at multiple values within a single test."""

    def test_matching_values_across_both_resolvers(self):
        from app.settings import reset_settings_for_tests
        for val in ("1", "5", "20"):
            with patch.dict(os.environ, {"ANALYSER_BATCH_SIZE": val}):
                reset_settings_for_tests()
                assert analyse.resolve_batch_size() == _resolve_batch_size()

    def test_matching_fallbacks_across_both_resolvers(self):
        from app.settings import reset_settings_for_tests
        for bad in ("garbage", "0", "-10"):
            with patch.dict(os.environ, {"ANALYSER_BATCH_SIZE": bad}):
                reset_settings_for_tests()
                assert analyse.resolve_batch_size() == _resolve_batch_size() == 5


if __name__ == "__main__":
    unittest.main()
