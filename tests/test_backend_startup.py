from unittest.mock import Mock

import pytest
from psycopg import OperationalError
from psycopg_pool import PoolTimeout

from backend import server


@pytest.mark.parametrize('error', [PoolTimeout, OperationalError])
def test_connection_failure_is_actionable_without_leaking_details(monkeypatch, capsys, error):
    monkeypatch.setattr(server, 'get_store', Mock(side_effect=error('secret-dsn')))
    http = Mock()
    monkeypatch.setattr(server, 'ThreadingHTTPServer', http)

    assert server.main([]) == 1
    output = capsys.readouterr()
    assert 'Comprobando' in output.out
    assert 'DNS/VPN' in output.err
    assert 'secret-dsn' not in output.err
    http.assert_not_called()


def test_successful_preflight_starts_server(monkeypatch, capsys):
    monkeypatch.setattr(server, 'get_store', Mock())
    http = Mock()
    monkeypatch.setattr(server, 'ThreadingHTTPServer', http)

    assert server.main(['--port', '8020']) == 0
    http.assert_called_once_with(('127.0.0.1', 8020), server.Handler)
    http.return_value.serve_forever.assert_called_once_with()
    assert 'http://127.0.0.1:8020' in capsys.readouterr().out
