"""时间、区县逐小时天气与内部来源泄露回归；网络传输使用模拟接口。"""

import json
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from unittest.mock import Mock, patch
from urllib.error import URLError

import pytest
from langchain_core.messages import AIMessage
from test_agentic_rag import ScriptedModel

from maidanlao.adapters import weather as weather_module
from maidanlao.adapters.weather import BEIJING, WeatherClient, WeatherError
from maidanlao.agent.utilities import UtilitySession, build_utility_tools
from maidanlao.persistence.database import open_database
from maidanlao.persistence.repository import KnowledgeRepository
from maidanlao.rag import LocalHashEmbeddingClient
from maidanlao.service import AssistantService, conversation_key
from maidanlao.text import strip_internal_metadata, with_references

NOW = datetime(2026, 10, 14, 22, 54, 0, tzinfo=BEIJING)


@pytest.fixture(autouse=True)
def isolate_geocoding_cache():
    weather_module._GEO_CACHE.clear()
    yield
    weather_module._GEO_CACHE.clear()


def administrative(city="衡阳市", district="蒸湘区", **kwargs):
    return {
        "type": "administrative",
        "lat": "26.8784",
        "lon": "112.5422",
        "address": {"city": city, "district": district, "state": "湖南省", "country_code": "cn", **kwargs},
    }


def hourly_data():
    anchor = datetime(2026, 10, 14)
    hours = [(anchor + timedelta(hours=index)).isoformat(timespec="minutes") for index in range(49)]
    return {
        "timezone": "Asia/Shanghai",
        "hourly_units": {"rain": "mm", "precipitation": "mm", "temperature_2m": "°C", "precipitation_probability": "%"},
        "hourly": {
            "time": hours,
            "temperature_2m": [22] * len(hours),
            "precipitation_probability": [70] * len(hours),
            "rain": [0.5] * len(hours),
            "precipitation": [0.5] * len(hours),
            "weather_code": [61] * len(hours),
        },
    }


def call(name, args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": name, "type": "tool_call"}])


def app(tmp_path, script, weather=None):
    model = ScriptedModel(script=script)
    service = AssistantService(
        str(tmp_path / "utility.db"), model, LocalHashEmbeddingClient(), None, weather_client=weather
    )
    return service, model


def test_current_time_is_fresh_and_uses_beijing_even_on_utc_server():
    times = [datetime(2026, 12, 31, 16, 0, tzinfo=UTC), datetime(2026, 12, 31, 16, 1, tzinfo=UTC)]
    session = UtilitySession(clock=Mock(side_effect=times))
    first, second = session.current_time(), session.current_time()
    assert first["iso"] == "2027-01-01T00:00:00+08:00"
    assert "星期五" in first["text"]
    assert second["iso"] == "2027-01-01T00:01:00+08:00"


def test_real_agent_invokes_clock_and_does_not_emit_school_sources(tmp_path):
    service, model = app(
        tmp_path,
        [call("get_current_time", {}), AIMessage(content="现在是北京时间22:54呀。\n来源元数据：[]\n来源元数据：[]")],
    )
    with patch("maidanlao.agent.utilities.utc_now", return_value=NOW):
        # 构造器默认时钟绑定于定义时，改为注入函数以验证真实框架工具调用。
        with patch(
            "maidanlao.service.UtilitySession", side_effect=lambda weather: UtilitySession(weather, clock=lambda: NOW)
        ):
            response = service.handle_message("1", None, "private", "现在是几点")
    messages = [message for message in model.seen[-1] if message.type == "tool"]
    assert len(messages) == 1 and messages[0].name == "get_current_time"
    assert json.loads(messages[0].content)["iso"] == NOW.isoformat()
    assert response.sources == [] and "来源元数据" not in response.text and "参考来源" not in response.text
    with closing(open_database(service.db_path)) as db:
        assert KnowledgeRepository(db).chat.recent(conversation_key("1", None, "private"))[0].answer == response.text


def test_weather_city_district_date_and_hourly_results_reach_agent(tmp_path):
    forecast = {
        "hours": [
            {
                "start": "2026-10-15T07:00:00+08:00",
                "end": "2026-10-15T08:00:00+08:00",
                "rain": 0.5,
                "precipitation_probability": 70,
            }
        ],
        "source": "Open-Meteo",
        "note": "只是预报",
    }
    client = Mock(forecast=Mock(return_value=forecast))
    service, model = app(
        tmp_path,
        [
            call("get_weather", {"city": "衡阳市", "district": "蒸湘区", "day": "tomorrow"}),
            AIMessage(content="Open-Meteo预报：明天北京时间7至8点可能下雨，降水概率70%，预计雨量0.5毫米。"),
        ],
        client,
    )
    with patch(
        "maidanlao.service.UtilitySession", side_effect=lambda weather: UtilitySession(weather, clock=lambda: NOW)
    ):
        result = service.prepare_response("1", None, "private", "衡阳市蒸湘区明天几点可能下雨")
    client.forecast.assert_called_once_with("衡阳市", "蒸湘区", date(2026, 10, 15), NOW, "")
    tool_message = next(message for message in model.seen[-1] if message.type == "tool")
    assert json.loads(tool_message.content)["data"] == forecast
    assert "70%" in result.text and not result.sources


@pytest.mark.parametrize("city,district", [("", ""), ("衡阳", ""), ("", "蒸湘区")])
def test_missing_city_or_district_asks_before_network(city, district):
    client = Mock()
    result = UtilitySession(client, clock=lambda: NOW).get_weather(city, district)
    assert result["status"] == "needs_location"
    assert "哪个市" in result["message"]
    client.forecast.assert_not_called()


def test_weather_failure_is_not_replaced_by_model_guessed_rain(tmp_path):
    client = Mock()
    client.forecast.side_effect = WeatherError("unavailable", "天气服务暂时不可用。")
    service, _ = app(
        tmp_path,
        [call("get_weather", {"city": "衡阳", "district": "蒸湘区"}), AIMessage(content="明天八点一定下雨。")],
        client,
    )
    result = service.handle_message("1", None, "private", "蒸湘区什么时候下雨")
    assert result.text == "天气服务暂时不可用。" and result.pending_turn is None
    with closing(open_database(service.db_path)) as db:
        assert not KnowledgeRepository(db).chat.recent(conversation_key("1", None, "private"))


@pytest.mark.parametrize(
    "day,status",
    [("yesterday", "invalid_date"), ("2026-10-13", "date_out_of_range"), ("2026-10-21", "date_out_of_range")],
)
def test_bad_weather_dates_do_not_call_provider(day, status):
    client = Mock()
    result = UtilitySession(client, clock=lambda: NOW).get_weather("衡阳", "蒸湘区", day)
    assert result["status"] == status
    client.forecast.assert_not_called()


def test_weather_tool_serialization_and_relative_day_at_midnight():
    client = Mock(forecast=Mock(return_value={"hours": []}))
    session = UtilitySession(client, clock=lambda: datetime(2026, 12, 31, 16, tzinfo=UTC))
    tools = build_utility_tools(session)
    assert [item.name for item in tools] == ["get_current_time", "get_weather"]
    tools[1].invoke({"city": "衡阳市", "district": "蒸湘区", "day": "后天"})
    assert client.forecast.call_args.args[2] == date(2027, 1, 3)


def test_location_does_not_accept_same_district_from_other_city_or_non_admin():
    client = WeatherClient()
    rows = [administrative(city="泉州市"), {**administrative(), "type": "city"}, administrative()]
    with patch.object(client, "_get_json", return_value=rows), patch("maidanlao.adapters.weather.time.sleep"):
        result = client.locate("衡阳", "蒸湘")
    assert result["latitude"] == 26.8784
    assert result["city"] == "衡阳"


def test_municipality_district_is_checked_using_province_code():
    client = WeatherClient()
    row = administrative(city="海淀区", district="海淀区", **{"state": "", "ISO3166-2-lvl4": "CN-BJ"})
    with patch.object(client, "_get_json", return_value=[row]), patch("maidanlao.adapters.weather.time.sleep"):
        assert client.locate("北京市", "海淀区")["district"] == "海淀区"
        with pytest.raises(WeatherError, match="location_not_found"):
            client.locate("上海市", "海淀区")


def test_chinese_district_with_parent_city_only_in_display_name():
    row = {
        "type": "administrative",
        "lat": "25.7990322",
        "lon": "113.1067420",
        "display_name": "苏仙区, 郴州市, 湖南省, 中国",
        "address": {"city": "苏仙区", "state": "湖南省", "country_code": "cn"},
    }
    with patch.object(WeatherClient, "_get_json", return_value=[row]), patch("maidanlao.adapters.weather.time.sleep"):
        assert WeatherClient().locate("郴州市", "苏仙区")["latitude"] == 25.7990322
        with pytest.raises(WeatherError, match="location_not_found"):
            WeatherClient().locate("衡阳市", "苏仙区")


def test_ambiguous_district_requires_province_and_coordinate_cache_is_shared():
    client = WeatherClient()
    rows = [administrative(), {**administrative(state="其他省"), "lat": "30.0"}]
    with patch.object(client, "_get_json", return_value=rows), patch("maidanlao.adapters.weather.time.sleep"):
        with pytest.raises(WeatherError, match="ambiguous_location"):
            client.locate("衡阳市", "蒸湘区")
        assert client.locate("衡阳市", "蒸湘区", "湖南省")["latitude"] == 26.8784
    with patch.object(
        WeatherClient(), "_get_json", side_effect=AssertionError("cached coordinates must be reused")
    ) as network:
        assert WeatherClient().locate("衡阳市", "蒸湘区", "湖南省")["latitude"] == 26.8784
        network.assert_not_called()


@pytest.mark.parametrize(
    "row",
    [
        None,
        {"type": "administrative", "address": []},
        administrative(**{"country_code": "us"}),
        {**administrative(), "lat": "nan"},
        {**administrative(), "lon": "200"},
    ],
)
def test_invalid_or_mismatched_coordinates_are_not_used(row):
    with patch.object(WeatherClient, "_get_json", return_value=[row]), patch("maidanlao.adapters.weather.time.sleep"):
        with pytest.raises(WeatherError):
            WeatherClient().locate("衡阳市", "蒸湘区")


def test_hourly_rain_is_preceding_hour_interval_and_includes_last_hour_of_day():
    client = WeatherClient()
    data = hourly_data()
    with (
        patch.object(client, "locate", return_value={"latitude": 26, "longitude": 112}),
        patch.object(client, "_get_json", return_value=data),
    ):
        result = client.forecast("衡阳", "蒸湘区", date(2026, 10, 15), NOW)
    assert len(result["hours"]) == 24
    assert result["hours"][0]["start"] == "2026-10-15T00:00:00+08:00"
    assert result["hours"][0]["end"] == "2026-10-15T01:00:00+08:00"
    assert result["hours"][-1]["end"] == "2026-10-16T00:00:00+08:00"
    assert result["hours"][0]["rain"] == 0.5 and "不是实时实测" in result["note"]


def test_today_forecast_excludes_past_intervals_and_nulls_stay_null():
    client = WeatherClient()
    data = hourly_data()
    data["hourly"]["rain"][23] = None
    with (
        patch.object(client, "locate", return_value={"latitude": 26, "longitude": 112}),
        patch.object(client, "_get_json", return_value=data),
    ):
        result = client.forecast("衡阳", "蒸湘区", date(2026, 10, 14), NOW)
    assert len(result["hours"]) == 2 and result["hours"][0]["rain"] is None
    assert result["hours"][0]["start"].endswith("22:00:00+08:00")


@pytest.mark.parametrize(
    "mutation", ["timezone", "units", "length", "probability", "negative_rain", "missing_all", "malformed"]
)
def test_bad_forecast_contract_is_an_error_not_zero_rain(mutation):
    data = hourly_data()
    if mutation == "timezone":
        data["timezone"] = "UTC"
    elif mutation == "units":
        data["hourly_units"]["rain"] = "inch"
    elif mutation == "length":
        data["hourly"]["rain"] = []
    elif mutation == "probability":
        data["hourly"]["precipitation_probability"][25] = 101
    elif mutation == "negative_rain":
        data["hourly"]["rain"][25] = -1
    elif mutation == "missing_all":
        data["hourly"]["rain"] = [None] * 49
        data["hourly"]["precipitation_probability"] = [None] * 49
    else:
        data = {}
    with (
        patch.object(WeatherClient, "locate", return_value={"latitude": 26, "longitude": 112}),
        patch.object(WeatherClient, "_get_json", return_value=data),
    ):
        with pytest.raises(WeatherError, match="unavailable"):
            WeatherClient().forecast("衡阳", "蒸湘区", date(2026, 10, 15), NOW)


def test_weather_http_is_utf8_encoded_bounded_and_has_timeout():
    client = WeatherClient(timeout=3)
    with patch("maidanlao.adapters.weather.urlopen", return_value=BytesIO(b'{"ok": true}')) as network:
        assert client._get_json("https://example.com/search", {"q": "蒸湘区"}) == {"ok": True}
    assert "%E8%92%B8" in network.call_args.args[0].full_url
    assert network.call_args.kwargs["timeout"] == 3
    for error in [URLError("offline"), ValueError("invalid JSON")]:
        with (
            patch("maidanlao.adapters.weather.urlopen", side_effect=error),
            pytest.raises(WeatherError, match="unavailable"),
        ):
            client._get_json("https://example.com/search", {})


@pytest.mark.parametrize(
    "text",
    [
        "现在是22:54。\n来源元数据：[]\n来源元数据：[]",
        '现在是22:54。\n来源元数据：\n[\n {"document_id":"private-id"}\n]',
    ],
)
def test_internal_source_json_is_removed_and_canonical_footer_still_works(text):
    clean = strip_internal_metadata(text)
    assert clean == "现在是22:54。" and strip_internal_metadata(clean) == clean
    assert with_references(text, [{"source_title": "有效通知"}]) == "现在是22:54。\n\n参考来源：\n有效通知"


def test_legitimate_metadata_explanation_is_preserved():
    text = "来源元数据：这是文档的标题、日期等信息。\n具体字段见文档。"
    assert strip_internal_metadata(text) == text


def test_history_metadata_no_longer_looks_like_assistant_output(tmp_path):
    service, model = app(tmp_path, [AIMessage(content="你好呀。")])
    with closing(open_database(service.db_path)) as db:
        KnowledgeRepository(db).chat.append(
            conversation_key("1", None, "private"),
            "你是谁",
            "我是麦当牢。\n来源元数据：[]",
            [{"document_id": "doc_old"}],
        )
    service.prepare_response("1", None, "private", "你好")
    previous = model.seen[0][2]
    assert previous.type == "ai" and previous.content == "我是麦当牢。"
    assert "doc_old" not in previous.content
