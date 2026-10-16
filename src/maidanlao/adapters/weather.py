"""区县定位与逐小时天气预报；只访问固定公开服务，不接受模型提供的 URL。"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BEIJING = timezone(timedelta(hours=8))
_GEO_LOCK = threading.Lock()
_LAST_GEO_REQUEST = 0.0
_GEO_CACHE: dict[tuple[str, str, str], tuple[float, dict]] = {}
MUNICIPALITIES = {"北京": "CN-BJ", "上海": "CN-SH", "天津": "CN-TJ", "重庆": "CN-CQ"}
WEATHER_CODES = {
    0: "晴",
    1: "大部晴朗",
    2: "局部多云",
    3: "阴",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "中等毛毛雨",
    55: "强毛毛雨",
    56: "轻冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "轻冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "小阵雨",
    81: "中等阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "强阵雪",
    95: "雷暴",
    96: "雷暴伴小冰雹",
    99: "雷暴伴强冰雹",
}


class WeatherError(RuntimeError):
    """稳定错误类别和用户提示；原始网络错误不交给模型或 QQ 用户。"""

    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(code)


def _place(value: str) -> str:
    """仅移除行政后缀以比较“衡阳”与“衡阳市”，不做模糊地名推断。"""
    for suffix in ("特别行政区", "自治区", "自治县", "省", "市", "区", "县"):
        if value.endswith(suffix):
            return value.removesuffix(suffix)
    return value


class WeatherClient:
    """OpenStreetMap Nominatim 定位中国区县，Open-Meteo 提供未来七天逐小时预报。

    区县行政中心附近的网格预报并不等同于全区每一处实时天气；工具明确这一限制。
    Nominatim 查询全进程串行、间隔至少一秒，并缓存已验证坐标，遵守公开服务限流。
    """

    def __init__(self, timeout: float = 15):
        self.timeout = timeout

    def _get_json(self, endpoint: str, params: dict) -> object:
        request = Request(
            endpoint + "?" + urlencode(params),
            headers={"User-Agent": "Maidanlao-Weather/0.1", "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = response.read(2 * 1024 * 1024 + 1)
            if len(payload) > 2 * 1024 * 1024:
                raise ValueError("weather_response_too_large")
            return json.loads(payload.decode("utf-8"))
        except Exception as exc:
            raise WeatherError("unavailable", "天气服务暂时不可用，我暂时无法核实逐小时降雨预报，请稍后重试。") from exc

    def locate(self, city: str, district: str, province: str = "") -> dict:
        """只接受市和区县同时匹配的行政区域；同名多候选必须补充省份。"""
        global _LAST_GEO_REQUEST
        key = (city, district, province)
        with _GEO_LOCK:
            cached = _GEO_CACHE.get(key)
            if cached and time.monotonic() - cached[0] < 86400:
                return dict(cached[1])
            delay = max(0.0, 1.0 - (time.monotonic() - _LAST_GEO_REQUEST))
            if delay:
                time.sleep(delay)
            _LAST_GEO_REQUEST = time.monotonic()
            rows = self._get_json(
                "https://nominatim.openstreetmap.org/search",
                {
                    "q": " ".join(part for part in (province, city, district) if part),
                    "format": "jsonv2",
                    "addressdetails": 1,
                    "limit": 10,
                    "countrycodes": "cn",
                    "accept-language": "zh-CN",
                },
            )
        if not isinstance(rows, list):
            raise WeatherError("unavailable", "天气定位服务返回异常，暂时无法确认城市和区县。")
        candidates = []
        for row in rows:
            if not isinstance(row, dict) or row.get("type") != "administrative":
                continue
            address = row.get("address")
            if not isinstance(address, dict) or address.get("country_code") != "cn":
                continue
            names = [
                _place(str(address.get(key) or ""))
                for key in ("city", "town", "county", "district", "borough", "state")
            ]
            # Nominatim 的中国县区结果有时把区名放在 city 字段，父级市只保留在 display_name。
            # 按完整行政名称分项比较，不用子串匹配，以免把同名区县定位到另一座城市。
            names.extend(_place(part.strip()) for part in re.split(r"[,，]", str(row.get("display_name") or "")))
            municipal = (
                address.get("ISO3166-2-lvl4") == MUNICIPALITIES.get(_place(city)) and _place(city) in MUNICIPALITIES
            )
            if _place(district) not in names or (_place(city) not in names and not municipal):
                continue
            if province and _place(province) not in names and not (municipal and _place(province) == _place(city)):
                continue
            try:
                lat, lon = float(row["lat"]), float(row["lon"])
                if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
                    raise ValueError("invalid_coordinates")
            except (KeyError, TypeError, ValueError):
                continue
            item = {
                "city": city,
                "district": district,
                "province": address.get("state", province),
                "latitude": lat,
                "longitude": lon,
            }
            if item not in candidates:
                candidates.append(item)
        if not candidates:
            raise WeatherError(
                "location_not_found", "没有找到与这个市和区县同时匹配的地点，请提供省、市、区县的完整名称。"
            )
        if len(candidates) != 1:
            raise WeatherError("ambiguous_location", "这个市区名称对应多个地点，请补充省份或更完整的行政区名称。")
        with _GEO_LOCK:
            if len(_GEO_CACHE) >= 256:
                _GEO_CACHE.pop(next(iter(_GEO_CACHE)))
            _GEO_CACHE[key] = (time.monotonic(), candidates[0])
        return candidates[0]

    def forecast(self, city: str, district: str, target_date, now: datetime, province: str = "") -> dict:
        """返回北京时间小时区间、降雨概率及累计量；不声称预测到精确开始分钟。"""
        location = self.locate(city, district, province)
        data = self._get_json(
            "https://api.open-meteo.com/v1/forecast",
            {
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "hourly": "temperature_2m,precipitation_probability,precipitation,rain,weather_code",
                "timezone": "Asia/Shanghai",
                "forecast_days": 8,
            },
        )
        try:
            if not isinstance(data, dict) or data.get("timezone") != "Asia/Shanghai":
                raise ValueError("invalid_timezone")
            hourly, units = data["hourly"], data["hourly_units"]
            columns = ("time", "temperature_2m", "precipitation_probability", "precipitation", "rain", "weather_code")
            if any(not isinstance(hourly[key], list) or len(hourly[key]) != len(hourly["time"]) for key in columns):
                raise ValueError("invalid_hourly_columns")
            if (
                units.get("precipitation_probability") != "%"
                or units.get("rain") != "mm"
                or units.get("temperature_2m") != "°C"
                or units.get("precipitation") != "mm"
            ):
                raise ValueError("invalid_units")
            rows = []
            for index, value in enumerate(hourly["time"]):
                end = datetime.fromisoformat(value).replace(tzinfo=BEIJING)
                start = end - timedelta(hours=1)
                # 累计量表示时次之前的一小时；按区间起点的日期选取，含次日00:00结束的最后一小时。
                if start.date() != target_date or end <= now.astimezone(BEIJING):
                    continue
                numbers = {key: hourly[key][index] for key in columns if key != "time"}
                for key, number in numbers.items():
                    if number is not None and (
                        isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number)
                    ):
                        raise ValueError("invalid_weather_value")
                    if key == "precipitation_probability" and number is not None and not 0 <= number <= 100:
                        raise ValueError("invalid_probability")
                    if key in {"rain", "precipitation"} and number is not None and number < 0:
                        raise ValueError("invalid_precipitation")
                rows.append(
                    {
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                        **numbers,
                        "condition": WEATHER_CODES.get(numbers["weather_code"], "天气现象未提供"),
                    }
                )
            if not rows or not any(
                row["precipitation_probability"] is not None or row["rain"] is not None for row in rows
            ):
                raise ValueError("missing_rain_forecast")
        except (KeyError, TypeError, ValueError) as exc:
            raise WeatherError("unavailable", "天气服务缺少有效的逐小时降雨数据，暂时不能判断几点可能下雨。") from exc
        return {
            "location": location,
            "date": target_date.isoformat(),
            "timezone": "Asia/Shanghai",
            "fetched_at": now.astimezone(BEIJING).isoformat(),
            "hours": rows,
            "source": "Open-Meteo 逐小时天气预报；OpenStreetMap 区县定位",
            "note": "小时区间是北京时间。rain为该区间预计降雨累计毫米数，precipitation包含雨雪；概率是降水概率。"
            "数据为区县行政中心附近的网格预报，不是实时实测，不能保证准确的下雨开始时间；空值表示缺测，不是0。",
        }
