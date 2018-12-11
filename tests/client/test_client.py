import datetime
import json
import os
import pytest
import requests
import uuid
from unittest.mock import MagicMock, mock_open

import prefect
from prefect.client import Client
from prefect.client.result_handlers import ResultHandler
from prefect.engine.state import (
    CachedState,
    Failed,
    Finished,
    Mapped,
    Paused,
    Pending,
    Retrying,
    Running,
    Scheduled,
    Skipped,
    State,
    Success,
    TimedOut,
    TriggerFailed,
)
from prefect.utilities.graphql import GraphQLResult
from prefect.utilities.tests import set_temporary_config


class AddOneHandler(ResultHandler):
    def deserialize(self, result):
        return int(result) + 1

    def serialize(self, result):
        return str(result - 1)


class DictHandler(ResultHandler):
    def __init__(self, *args, **kwargs):
        self.data = {}
        super().__init__(*args, **kwargs)

    def deserialize(self, key):
        return self.data[key]

    def serialize(self, result):
        key = str(uuid.uuid4())
        self.data[key] = result
        return key


#################################
##### Client Tests
#################################


def test_client_initializes_from_config():
    with set_temporary_config("cloud.api", "api_server"):
        with set_temporary_config("cloud.graphql", "graphql_server"):
            with set_temporary_config("cloud.auth_token", "token"):
                client = Client()
    assert client.api_server == "api_server"
    assert client.graphql_server == "graphql_server"
    assert client.token == "token"


def test_client_token_initializes_from_kwarg():
    with set_temporary_config("cloud.api", "api_server"):
        with set_temporary_config("cloud.graphql", "graphql_server"):
            with set_temporary_config("cloud.auth_token", "token"):
                client = Client(token="init-token")
    assert client.api_server == "api_server"
    assert client.graphql_server == "graphql_server"
    assert client.token == "init-token"


def test_client_token_initializes_from_file(monkeypatch):
    monkeypatch.setattr("os.path.exists", MagicMock(return_value=True))
    monkeypatch.setattr("builtins.open", mock_open(read_data="TOKEN"))
    client = Client()
    assert client.token == "TOKEN"


def test_client_token_priotizes_config_over_file(monkeypatch):
    monkeypatch.setattr("os.path.exists", MagicMock(return_value=True))
    monkeypatch.setattr("builtins.open", mock_open(read_data="file-token"))
    with set_temporary_config("cloud.auth_token", "config-token"):
        client = Client()
    assert client.token == "config-token"


def test_client_logs_in_and_saves_token(monkeypatch):
    post = MagicMock(
        return_value=MagicMock(
            ok=True, json=MagicMock(return_value=dict(token="secrettoken"))
        )
    )
    monkeypatch.setattr("os.path.exists", MagicMock(return_value=True))
    mock_file = mock_open()
    monkeypatch.setattr("builtins.open", mock_file)
    monkeypatch.setattr("requests.post", post)
    with set_temporary_config("cloud.api", "http://my-cloud.foo"):
        client = Client()
    client.login("test@example.com", "1234")
    assert post.called
    assert post.call_args[0][0] == "http://my-cloud.foo/login_email"
    assert post.call_args[1]["auth"] == ("test@example.com", "1234")
    assert client.token == "secrettoken"
    assert mock_file.call_args[0] == (
        os.path.expanduser("~/.prefect/.credentials/auth_token"),
        "w+",
    )


def test_client_logs_in_automatically(monkeypatch):
    post = MagicMock(
        return_value=MagicMock(
            ok=True, json=MagicMock(return_value=dict(token="secrettoken"))
        )
    )
    mock_file = mock_open()
    monkeypatch.setattr("builtins.open", mock_file)
    monkeypatch.setattr("requests.post", post)
    with set_temporary_config("cloud.api", "http://my-cloud.foo"):
        with set_temporary_config("prefect_cloud", True):
            with set_temporary_config("cloud.auth_token", None):
                with set_temporary_config("cloud.email", "test@example.com"):
                    with set_temporary_config("cloud.password", "1234"):
                        client = Client()
    assert post.called
    assert post.call_args[0][0] == "http://my-cloud.foo/login_email"
    assert post.call_args[1]["auth"] == ("test@example.com", "1234")
    assert client.token == "secrettoken"
    assert mock_file.call_args[0] == (
        os.path.expanduser("~/.prefect/.credentials/auth_token"),
        "w+",
    )


def test_client_logs_in_from_config_credentials(monkeypatch):
    post = MagicMock(
        return_value=MagicMock(
            ok=True, json=MagicMock(return_value=dict(token="secrettoken"))
        )
    )
    monkeypatch.setattr("os.path.exists", MagicMock(return_value=True))
    mock_file = mock_open()
    monkeypatch.setattr("builtins.open", mock_file)
    monkeypatch.setattr("requests.post", post)
    with set_temporary_config("cloud.api", "http://my-cloud.foo"):
        with set_temporary_config("cloud.email", "test@example.com"):
            with set_temporary_config("cloud.password", "1234"):
                client = Client()
                client.login()
    assert post.called
    assert post.call_args[0][0] == "http://my-cloud.foo/login_email"
    assert post.call_args[1]["auth"] == ("test@example.com", "1234")
    assert client.token == "secrettoken"
    assert mock_file.call_args[0] == (
        os.path.expanduser("~/.prefect/.credentials/auth_token"),
        "w+",
    )


def test_client_logs_out_and_deletes_auth_token(monkeypatch):
    post = MagicMock(
        return_value=MagicMock(
            ok=True, json=MagicMock(return_value=dict(token="secrettoken"))
        )
    )
    monkeypatch.setattr("requests.post", post)
    with set_temporary_config("cloud.api", "http://my-cloud.foo"):
        client = Client()
    client.login("test@example.com", "1234")
    token_path = os.path.expanduser("~/.prefect/.credentials/auth_token")
    assert os.path.exists(token_path)
    with open(token_path, "r") as f:
        assert f.read() == "secrettoken"
    client.logout()
    assert not os.path.exists(token_path)


def test_client_raises_if_login_fails(monkeypatch):
    post = MagicMock(return_value=MagicMock(ok=False))
    monkeypatch.setattr("requests.post", post)
    with set_temporary_config("cloud.api", "http://my-cloud.foo"):
        client = Client()
    with pytest.raises(ValueError):
        client.login("test@example.com", "1234")
    assert post.called
    assert post.call_args[0][0] == "http://my-cloud.foo/login_email"


def test_client_posts_raises_with_no_token(monkeypatch):
    post = MagicMock()
    monkeypatch.setattr("requests.post", post)
    with set_temporary_config("cloud.api", "http://my-cloud.foo"):
        client = Client()
    with pytest.raises(ValueError) as exc:
        result = client.post("/foo/bar")
    assert "Client.login" in str(exc.value)


def test_client_posts_to_api_server(monkeypatch):
    post = MagicMock(
        return_value=MagicMock(json=MagicMock(return_value=dict(success=True)))
    )
    monkeypatch.setattr("requests.post", post)
    with set_temporary_config("cloud.api", "http://my-cloud.foo"):
        client = Client(token="secret_token")
    result = client.post("/foo/bar")
    assert result == {"success": True}
    assert post.called
    assert post.call_args[0][0] == "http://my-cloud.foo/foo/bar"


def test_client_posts_retries_if_token_needs_refreshing(monkeypatch):
    error = requests.HTTPError()
    error.response = MagicMock(status_code=401)  # unauthorized
    post = MagicMock(
        return_value=MagicMock(
            raise_for_status=MagicMock(side_effect=error),
            json=MagicMock(return_value=dict(token="new-token")),
        )
    )
    monkeypatch.setattr("requests.post", post)
    with set_temporary_config("cloud.api", "http://my-cloud.foo"):
        client = Client(token="secret_token")
    with pytest.raises(requests.HTTPError) as exc:
        result = client.post("/foo/bar")
    assert exc.value is error
    assert post.call_count == 3  # first call -> refresh token -> last call
    assert post.call_args[0][0] == "http://my-cloud.foo/foo/bar"
    assert client.token == "new-token"


def test_client_posts_graphql_to_graphql_server(monkeypatch):
    post = MagicMock(
        return_value=MagicMock(
            json=MagicMock(return_value=dict(data=dict(success=True)))
        )
    )
    monkeypatch.setattr("requests.post", post)
    with set_temporary_config("cloud.graphql", "http://my-cloud.foo/graphql"):
        client = Client(token="secret_token")
    result = client.graphql("{projects{name}}")
    assert result == {"success": True}
    assert post.called
    assert post.call_args[0][0] == "http://my-cloud.foo/graphql"


def test_client_graphql_retries_if_token_needs_refreshing(monkeypatch):
    error = requests.HTTPError()
    error.response = MagicMock(status_code=401)  # unauthorized
    post = MagicMock(
        return_value=MagicMock(
            raise_for_status=MagicMock(side_effect=error),
            json=MagicMock(return_value=dict(token="new-token")),
        )
    )
    monkeypatch.setattr("requests.post", post)
    with set_temporary_config("cloud.graphql", "http://my-cloud.foo/graphql"):
        client = Client(token="secret_token")
    with pytest.raises(requests.HTTPError) as exc:
        result = client.graphql("{}")
    assert exc.value is error
    assert post.call_count == 3  # first call -> refresh token -> last call
    assert post.call_args[0][0] == "http://my-cloud.foo/graphql"
    assert client.token == "new-token"


## test actual mutation and query handling
def test_graphql_errors_get_raised(monkeypatch):
    post = MagicMock(
        return_value=MagicMock(
            json=MagicMock(return_value=dict(data="42", errors="GraphQL issue!"))
        )
    )
    monkeypatch.setattr("requests.post", post)
    with set_temporary_config("cloud.graphql", "http://my-cloud.foo/graphql"):
        client = Client(token="secret_token")
    with pytest.raises(ValueError) as exc:
        res = client.graphql("query: {}")
    assert "GraphQL issue!" in str(exc.value)


def test_get_flow_run_info(monkeypatch):
    response = """
{
    "flow_run_by_pk": {
        "version": 0,
        "current_state": {
            "serialized_state": {
                "type": "Pending",
                "result": 42,
                "message": null,
                "__version__": "0.3.3+309.gf1db024",
                "cached_inputs": "null"
            }
        }
    }
}
    """
    post = MagicMock(
        return_value=MagicMock(
            json=MagicMock(return_value=dict(data=json.loads(response)))
        )
    )
    monkeypatch.setattr("requests.post", post)
    with set_temporary_config("cloud.graphql", "http://my-cloud.foo/graphql"):
        client = Client(token="secret_token")
    result = client.get_flow_run_info(flow_run_id="74-salt")
    assert isinstance(result, GraphQLResult)
    assert isinstance(result.state, Pending)
    assert result.state.result == 42
    assert result.state.message is None
    assert result.version == 0


def test_get_flow_run_info_raises_informative_error(monkeypatch):
    response = """
{
    "flow_run_by_pk": null
}
    """
    post = MagicMock(
        return_value=MagicMock(
            json=MagicMock(return_value=dict(data=json.loads(response)))
        )
    )
    monkeypatch.setattr("requests.post", post)
    with set_temporary_config("cloud.graphql", "http://my-cloud.foo/graphql"):
        client = Client(token="secret_token")
    with pytest.raises(ValueError) as exc:
        result = client.get_flow_run_info(flow_run_id="74-salt")
    assert "not found" in str(exc.value)


def test_set_flow_run_state(monkeypatch):
    response = """
{
    "setFlowRunState": {
        "flow_run": {
            "version": 1
        }
    }
}
    """
    post = MagicMock(
        return_value=MagicMock(
            json=MagicMock(return_value=dict(data=json.loads(response)))
        )
    )
    monkeypatch.setattr("requests.post", post)
    with set_temporary_config("cloud.graphql", "http://my-cloud.foo/graphql"):
        client = Client(token="secret_token")
    result = client.set_flow_run_state(
        flow_run_id="74-salt", version=0, state=Pending()
    )
    assert isinstance(result, GraphQLResult)
    assert result.version == 1


def test_get_task_run_info(monkeypatch):
    response = """
{
    "getOrCreateTaskRun": {
        "task_run": {
            "id": "772bd9ee-40d7-479c-9839-4ab3a793cabd",
            "version": 0,
            "current_state": {
                "serialized_state": {
                    "type": "Pending",
                    "result": "42",
                    "message": null,
                    "__version__": "0.3.3+310.gd19b9b7.dirty",
                    "cached_inputs": "null"
                }
            }
        }
    }
}
    """
    post = MagicMock(
        return_value=MagicMock(
            json=MagicMock(return_value=dict(data=json.loads(response)))
        )
    )
    monkeypatch.setattr("requests.post", post)
    with set_temporary_config("cloud.graphql", "http://my-cloud.foo/graphql"):
        client = Client(token="secret_token")
    result = client.get_task_run_info(
        flow_run_id="74-salt", task_id="72-salt", map_index=None
    )
    assert isinstance(result, GraphQLResult)
    assert isinstance(result.state, Pending)
    assert result.state.result == "42"
    assert result.state.message is None
    assert result.id == "772bd9ee-40d7-479c-9839-4ab3a793cabd"
    assert result.version == 0


def test_set_task_run_state(monkeypatch):
    response = """
{
    "setTaskRunState": {
        "task_run": {
            "version": 1
        }
    }
}
    """
    post = MagicMock(
        return_value=MagicMock(
            json=MagicMock(return_value=dict(data=json.loads(response)))
        )
    )
    monkeypatch.setattr("requests.post", post)
    with set_temporary_config("cloud.graphql", "http://my-cloud.foo/graphql"):
        client = Client(token="secret_token")
    result = client.set_task_run_state(
        task_run_id="76-salt", version=0, state=Pending()
    )
    assert isinstance(result, GraphQLResult)
    assert result.version == 1


class TestResultHandlerSerialization:
    def test_set_flow_run_state_calls_result_handler(self, monkeypatch):
        monkeypatch.setattr("requests.post", MagicMock())
        monkeypatch.setattr(prefect.client.client, "json", MagicMock())

        with set_temporary_config("cloud.graphql", "http://my-cloud.foo/graphql"):
            client = Client(token="secret_token")

        serializer = MagicMock(return_value="empty")
        result = client.set_flow_run_state(
            flow_run_id="74-salt",
            version=0,
            state=Pending(result=2),
            result_handler=MagicMock(serialize=serializer),
        )
        assert serializer.call_count == 1
        assert serializer.call_args[0] == (2,)

    def test_set_task_run_state_calls_result_handler(self, monkeypatch):
        monkeypatch.setattr("requests.post", MagicMock())
        monkeypatch.setattr(prefect.client.client, "json", MagicMock())

        with set_temporary_config("cloud.graphql", "http://my-cloud.foo/graphql"):
            client = Client(token="secret_token")

        serializer = MagicMock(return_value="empty")
        result = client.set_task_run_state(
            task_run_id="76-salt",
            version=0,
            state=Pending(result=2),
            result_handler=MagicMock(serialize=serializer),
        )
        assert serializer.call_count == 1
        assert serializer.call_args[0] == (2,)


class TestResultHandlerDeserialization:
    def test_get_flow_run_info_doesnt_call_result_handler_if_result_is_none(
        self, monkeypatch
    ):
        response = """
    {
        "flow_run_by_pk": {
            "version": 0,
            "current_state": {
                "serialized_state": {
                    "type": "Pending",
                    "result": null,
                    "message": null,
                    "__version__": "0.3.3+309.gf1db024",
                    "cached_inputs": "null"
                }
            }
        }
    }
        """
        post = MagicMock(
            return_value=MagicMock(
                json=MagicMock(return_value=dict(data=json.loads(response)))
            )
        )
        monkeypatch.setattr("requests.post", post)
        with set_temporary_config("cloud.graphql", "http://my-cloud.foo/graphql"):
            client = Client(token="secret_token")

        result = client.get_flow_run_info(
            flow_run_id="74-salt", result_handler=AddOneHandler()
        )
        assert isinstance(result, GraphQLResult)
        assert isinstance(result.state, Pending)
        assert result.state.result == None

    def test_get_task_run_info_doesnt_call_result_handler_if_result_is_none(
        self, monkeypatch
    ):
        response = """
    {
        "getOrCreateTaskRun": {
            "task_run": {
                "id": "772bd9ee-40d7-479c-9839-4ab3a793cabd",
                "version": 0,
                "current_state": {
                    "serialized_state": {
                        "type": "Pending",
                        "result": null,
                        "message": null,
                        "__version__": "0.3.3+310.gd19b9b7.dirty",
                        "cached_inputs": "null"
                    }
                }
            }
        }
    }
        """
        post = MagicMock(
            return_value=MagicMock(
                json=MagicMock(return_value=dict(data=json.loads(response)))
            )
        )
        monkeypatch.setattr("requests.post", post)
        with set_temporary_config("cloud.graphql", "http://my-cloud.foo/graphql"):
            client = Client(token="secret_token")

        result = client.get_task_run_info(
            flow_run_id="74-salt",
            task_id="72-salt",
            map_index=None,
            result_handler=AddOneHandler(),
        )
        assert isinstance(result, GraphQLResult)
        assert isinstance(result.state, Pending)
        assert result.state.result == None

    def test_get_flow_run_info_calls_result_handler(self, monkeypatch):
        response = """
    {
        "flow_run_by_pk": {
            "version": 0,
            "current_state": {
                "serialized_state": {
                    "type": "Pending",
                    "result": 42,
                    "message": null,
                    "__version__": "0.3.3+309.gf1db024",
                    "cached_inputs": "null"
                }
            }
        }
    }
        """
        post = MagicMock(
            return_value=MagicMock(
                json=MagicMock(return_value=dict(data=json.loads(response)))
            )
        )
        monkeypatch.setattr("requests.post", post)
        with set_temporary_config("cloud.graphql", "http://my-cloud.foo/graphql"):
            client = Client(token="secret_token")

        result = client.get_flow_run_info(
            flow_run_id="74-salt", result_handler=AddOneHandler()
        )
        assert isinstance(result, GraphQLResult)
        assert isinstance(result.state, Pending)
        assert result.state.result == 43

    def test_get_task_run_info_calls_result_handler(self, monkeypatch):
        response = """
    {
        "getOrCreateTaskRun": {
            "task_run": {
                "id": "772bd9ee-40d7-479c-9839-4ab3a793cabd",
                "version": 0,
                "current_state": {
                    "serialized_state": {
                        "type": "Pending",
                        "result": 42,
                        "message": null,
                        "__version__": "0.3.3+310.gd19b9b7.dirty",
                        "cached_inputs": "null"
                    }
                }
            }
        }
    }
        """
        post = MagicMock(
            return_value=MagicMock(
                json=MagicMock(return_value=dict(data=json.loads(response)))
            )
        )
        monkeypatch.setattr("requests.post", post)
        with set_temporary_config("cloud.graphql", "http://my-cloud.foo/graphql"):
            client = Client(token="secret_token")

        result = client.get_task_run_info(
            flow_run_id="74-salt",
            task_id="72-salt",
            map_index=None,
            result_handler=AddOneHandler(),
        )
        assert isinstance(result, GraphQLResult)
        assert isinstance(result.state, Pending)
        assert result.state.result == 43
