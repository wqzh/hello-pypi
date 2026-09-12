
# from pi.pi_ai import Model, TextContent, UserMessage
# from pi.pi_agent_core import Agent, AgentOptions, AgentToolResult

from pi.pi_ai import TextContent
from pi.pi_agent_core import AgentToolResult

import aiohttp
from typing import Optional

import os
from dotenv import load_dotenv

load_dotenv()  # 加载 .env 到环境变量




## 注册可以使用的工具
class WeatherDemoTool:
    name = "get_weather_demo"
    description = "查询天气"
    label = "Weather"
    parameters = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}
    async def execute(self, tool_call_id, params, cancel_event=None, on_update=None):
        # 需替换成调用真实的 tool, 通过访问真实的api 获取天气
        
        city_weather_map = {
            '北京': '晴转多云 25°C',
            '天津': '小雨 20~23°C',
        }
        city = params['city']
        if city in city_weather_map:
            weather_result = city_weather_map[city]
        else:
            weather_result = f"{city} 天气，我猜可能是 小雪，-5°C"

        print(f"\n[工具执行] weather_result")
        return AgentToolResult(content=[TextContent(text=weather_result)])


class GetCityWeatherTool:
    """这个工具好！不用注册，简洁明了。"""
    name = "get_city_weather"
    description = "查询指定城市的实时天气"
    label = "Weather"
    parameters = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}

    async def execute(self, tool_call_id, params, cancel_event=None, on_update=None):
        city = params['city']
        url = f"https://wttr.in/{city}?format=%l:+%c+%t+%h+%w&lang=zh"

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                text = await resp.text()

        result = text.strip() or f"未找到城市：{city}"
        print(f"\n[工具执行] {result}")
        return AgentToolResult(content=[TextContent(text=result)])
    
    

class GetCityWeatherTool_v1:
    '''弃用了！使用太繁琐！！'''
    
    """API Key 获取方式：前往和风天气控制台（console.qweather.com）注册账号，
    创建项目后生成 API Key。免费版有调用次数限制。每月有5w次免费额度。
    选择GeoAPI服务： https://dev.qweather.com/docs/api/geoapi/city-lookup/
    
    【作者提示】：该网站后付费模式。如果余额为0，会继续扣费，下月支付账单。
    
    https://dev.qweather.com/docs/finance/pricing/
    价格适用于下列数据服务: 天气预报 • 分钟预报 • 预警 • 天气指数 • 空气质量 • 时光机 • GeoAPI • 天文 • 控制台API
        请求量(每月)	价格(每次请求)
        0–50000        CNY 0
        之后的 950000	CNY 0.0007
        之后的 4000000	CNY 0.0005
        之后的 5000000	CNY 0.00035
        之后的 40000000	CNY 0.00015
        之后的 50000000	CNY 0.0001
    """
    
    name = "get_city_weather"
    description = "查询指定城市的实时天气"
    label = "Weather"
    parameters = {"type": "object", "properties": {"city": {"type": "string", "description": "城市名称，如北京、上海"}}, "required": ["city"]}
    
    QWEATHER_API_KEY = os.getenv("TOOL_QWEATHER_API_KEY", "")
    QWEATHER_BASE_URL = os.getenv("TOOL_QWEATHER_BASE_URL", "https://devapi.qweather.com")

    API_KEY = QWEATHER_API_KEY  # 替换为你的和风天气 API Key
    BASE_URL = QWEATHER_BASE_URL # 或使用你的自定义 API Host
    
    async def execute(self, tool_call_id, params, cancel_event=None, on_update=None):
        city = params['city']
        async with aiohttp.ClientSession() as session:
            # Step 1: 城市名 → LocationID
            geo_url = f"{self.BASE_URL}/geo/v2/city/lookup"
            async with session.get(geo_url, params={"location": city, "key": self.API_KEY}) as resp:
                geo_data = await resp.json()
            
            if geo_data.get("code") != "200" or not geo_data.get("location"):
                return AgentToolResult(content=[TextContent(text=f"未找到城市：{city}")])
            
            location_id = geo_data["location"][0]["id"]
            
            # Step 2: 查询实时天气
            weather_url = f"{self.BASE_URL}/v7/weather/now"
            async with session.get(weather_url, params={"location": location_id, "key": self.API_KEY}) as resp:
                weather_data = await resp.json()
            
            if weather_data.get("code") != "200":
                return AgentToolResult(content=[TextContent(text=f"天气查询失败：{weather_data.get('code')}")])
            
            now = weather_data["now"]
            result = f"{city}：{now['text']} {now['temp']}°C，体感 {now['feelsLike']}°C，湿度 {now['humidity']}%"
        
        print(f"\n[工具执行] {result}")
        return AgentToolResult(content=[TextContent(text=result)])