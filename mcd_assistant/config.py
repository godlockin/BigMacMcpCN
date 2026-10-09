"""Configuration loader for McDonald's Assistant."""
import json
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LocationConfig:
    lat: float = 39.9042
    lng: float = 116.4074
    address: str = "北京市朝阳区"
    city: str = "北京"
    keyword: str = "朝阳区"
    be_type: int = 1  # 1=到店自取, 5=得来速


@dataclass
class Config:
    mcp_server_url: str = "https://mcp.mcd.cn"
    mcp_token: str = ""
    monitor_interval_minutes: int = 240
    auto_mode: bool = False
    notification_method: str = "console"
    default_location: LocationConfig = field(default_factory=LocationConfig)
    data_dir: str = ""

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> "Config":
        if config_path is None:
            config_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "config.json",
            )
        data = {}
        if Path(config_path).exists():
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("config must be a JSON object")
        token = data.get("mcp_token", "")
        if not token or token == "${MCD_MCP_TOKEN}":
            token = os.environ.get("MCD_MCP_TOKEN", "")
        if not isinstance(token, str):
            raise ValueError("mcp_token must be a string")

        loc_data = data.get("default_location", {})
        location = LocationConfig(
            lat=loc_data.get("lat", 39.9042),
            lng=loc_data.get("lng", 116.4074),
            address=loc_data.get("address", "北京市朝阳区"),
            city=loc_data.get("city", "北京"),
            keyword=loc_data.get("keyword", "朝阳区"),
            be_type=loc_data.get("be_type", 1),
        )

        data_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
        )

        return cls(
            mcp_server_url=data.get("mcp_server_url", "https://mcp.mcd.cn"),
            mcp_token=token,
            monitor_interval_minutes=data.get("monitor_interval_minutes", 240),
            auto_mode=data.get("auto_mode", False),
            notification_method=data.get("notification_method", "console"),
            default_location=location,
            data_dir=data_dir,
        )

    def save(self, config_path: Optional[str] = None):
        if config_path is None:
            config_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "config.json",
            )
        data = {
            "mcp_server_url": self.mcp_server_url,
            "mcp_token": self.mcp_token,
            "monitor_interval_minutes": self.monitor_interval_minutes,
            "auto_mode": self.auto_mode,
            "notification_method": self.notification_method,
            "default_location": {
                "lat": self.default_location.lat,
                "lng": self.default_location.lng,
                "address": self.default_location.address,
            },
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
