"""Agent 只读实用工具：服务器时钟及区县级逐小时天气。"""

import json
import logging
from datetime import UTC, date, datetime, timedelta

from langchain_core.tools import tool

from maidanlao.adapters.weather import BEIJING, WeatherClient, WeatherError

logger = logging.getLogger(__name__)


def utc_now():
    return datetime.now(UTC)


class UtilitySession:
    """请求内工具状态；天气失败不得凭模型记忆生成降雨结论，不进入学校来源列表。"""

    def __init__(self, weather=None, clock=utc_now):
        self.weather = weather if weather is not None else WeatherClient()
        self.clock = clock
        self.notice = ""

    def current_time(self) -> dict:
        """每次读取服务器时钟，显式转换到北京时间，避免服务器时区和模型知识日期影响。"""
        now = self.clock().astimezone(BEIJING)
        return {
            "status": "ok",
            "timezone": "Asia/Shanghai",
            "iso": now.isoformat(),
            "text": now.strftime("%Y年%m月%d日 %H:%M:%S") + " 星期" + "一二三四五六日"[now.weekday()] + "（北京时间）",
            "source": "服务器系统时钟",
        }

    def get_weather(self, city: str, district: str, day: str = "today", province: str = "") -> dict:
        """日期默认今天，支持明天、后天或ISO日期，最多查询未来七天；地点不从QQ号推断。"""
        now = self.clock().astimezone(BEIJING)
        city, district, province, day = (value.strip() for value in (city, district, province, day))
        self.notice = ""
        if not city or not district:
            return self._error(
                "needs_location", "你想查哪个市、哪个区或县的天气呀？请提供完整地点，比如“衡阳市蒸湘区”。"
            )
        if any(len(value) > 100 or "\n" in value for value in (city, district, province)):
            return self._error("invalid_location", "请提供简短、准确的市和区县名称。")
        offsets = {"today": 0, "今天": 0, "tomorrow": 1, "明天": 1, "after_tomorrow": 2, "后天": 2}
        try:
            target = now.date() + timedelta(days=offsets[day]) if day in offsets else date.fromisoformat(day)
            if not 0 <= (target - now.date()).days <= 6:
                return self._error(
                    "date_out_of_range", "只能查询今天起七天内的逐小时天气预报，请明确这段时间内的日期。"
                )
            data = self.weather.forecast(city, district, target, now, province)
            return {"status": "ok", "data": data}
        except ValueError:
            return self._error("invalid_date", "请明确查询日期，例如今天、明天或2026-10-15。")
        except WeatherError as exc:
            logger.warning("weather_query_failed reason=%s", exc.code)
            return self._error(exc.code, exc.message)
        except Exception as exc:
            logger.warning("weather_query_failed type=%s", type(exc).__name__)
            return self._error("unavailable", "天气查询暂时失败，无法核实几点可能下雨，请稍后重试。")

    def _error(self, status, message):
        self.notice = message
        return {"status": status, "message": message}


def build_utility_tools(session: UtilitySession):
    """只暴露查询参数，不向模型提供发送、数据库或任意HTTP访问能力。"""

    @tool
    def get_current_time() -> str:
        """查询当前北京时间、日期、星期。问现在几点、今天几号、计算相对日期时使用，不能凭记忆猜时间。"""
        return json.dumps(session.current_time(), ensure_ascii=False)

    @tool
    def get_weather(city: str, district: str, day: str = "today", province: str = "") -> str:
        """查询中国某市某区/县逐小时天气及降雨时段，提供降水概率、雨量和温度。

        city与district必须由用户或明确会话地点提供，不知道时先询问。day为today、tomorrow、
        after_tomorrow或YYYY-MM-DD；默认今天，最多七天。省份用于同名地点消歧。
        依据小时start/end区间回答几点可能下雨，只是预报，不保证精确开始时间。
        """
        return json.dumps(session.get_weather(city, district, day, province), ensure_ascii=False)

    return [get_current_time, get_weather]
