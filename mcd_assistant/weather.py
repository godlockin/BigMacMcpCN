"""Optional hourly Open-Meteo weather; unknown remains unknown."""
import json
import math
from datetime import datetime
from typing import Callable
from urllib.parse import urlencode
from urllib.request import urlopen

from .decision import InputError


def fetch_weather(url: str) -> dict:
    with urlopen(url, timeout=12) as response:
        return json.loads(response.read(524288))


class Weather:
    def __init__(self, fetch: Callable[[str], dict] = fetch_weather):
        self.fetch = fetch

    def __call__(self, request: dict) -> dict:
        lat, lon = request.get('latitude'), request.get('longitude')
        if lat is None or lon is None:
            return {'status': 'unknown', 'source': None, 'note': '未指定天气坐标；不读取 IP 或猜测电脑位置'}
        if isinstance(lat, bool) or isinstance(lon, bool) or not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            raise InputError('天气坐标须为数字')
        if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise InputError('天气坐标超出范围')
        target = datetime.fromisoformat(request['target_time'])
        url = 'https://api.open-meteo.com/v1/forecast?' + urlencode({
            'latitude': lat, 'longitude': lon, 'hourly': 'temperature_2m,precipitation,weather_code',
            'timezone': 'Asia/Shanghai', 'forecast_days': 3})
        try:
            data = self.fetch(url)
            hourly = data['hourly']
            stamp = target.strftime('%Y-%m-%dT%H:00')
            index = hourly['time'].index(stamp)
            return {'status': 'forecast', 'source': 'Open-Meteo', 'forecast_time': stamp,
                    'temperature_c': hourly['temperature_2m'][index],
                    'precipitation_mm': hourly['precipitation'][index],
                    'weather_code': hourly['weather_code'][index],
                    'note': '所指定坐标的小时预报，不代表门店实况；非导航、非出餐预测'}
        except Exception:
            return {'status': 'unknown', 'source': 'Open-Meteo', 'note': '天气读取失败，未将未知天气当晴天'}
