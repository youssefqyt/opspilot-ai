
from app.agent import _extract_json
from app.tools import analyze_logs, check_local_service, execute_tool


def test_extract_json_parses_markdown_fenced_response():
    result = _extract_json(
        '```json\n{"summary":"Disk is full.","severity":"HIGH",'
        '"confidence":"medium","evidence":["Disk usage: 99.7%"],'
        '"recommendations":["Free disk space."]}\n```'
    )

    assert result["summary"] == "Disk is full."
    assert result["severity"] == "high"
    assert result["confidence"] == "medium"
    assert result["evidence"] == ["Disk usage: 99.7%"]
    assert result["recommendations"] == ["Free disk space."]


def test_extract_json_parses_object_surrounded_by_text():
    result = _extract_json(
        'Diagnosis follows:\n{"summary":"Database unavailable",'
        '"severity":"high"}\nEnd of response.'
    )

    assert result["summary"] == "Database unavailable"
    assert result["severity"] == "high"


def test_analyze_logs_detects_database_and_timeout():
    result = analyze_logs(
        "ERROR database connection refused\n"
        "ERROR request timed out"
    )

    assert "database" in result["detected_patterns"]
    assert "timeout" in result["detected_patterns"]
    assert len(result["error_lines"]) == 2


def test_unknown_tool_is_rejected():
    result = execute_tool("run_shell_command", {"command": "whoami"})
    assert "error" in result


def test_remote_health_check_is_rejected():
    result = check_local_service("http://example.com")
    assert "error" in result


def test_non_http_health_check_is_rejected():
    result = check_local_service("https://localhost")
    assert "error" in result
