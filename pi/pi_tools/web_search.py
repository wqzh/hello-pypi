
from pi.pi_ai import TextContent
from pi.pi_agent_core import AgentToolResult

from tavily import AsyncTavilyClient
import os

class WebSearchTavilyTool:
    """前往 Tavily 官网注册即可获得 每月 1000 次免费额度，无需绑定信用卡。
    注册地址：https://app.tavily.com
    """
    
    name = "web_search"
    description = "联网搜索获取实时信息"
    label = "Search"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"}
        },
        "required": ["query"]
    }
    
    def __init__(self):
        TAVILY_API_KEY = os.getenv("TOOL_TAVILY_SEARCH_API_KEY", "")
        
        self.client = AsyncTavilyClient(api_key=TAVILY_API_KEY)
    
    async def execute(self, tool_call_id, params, cancel_event=None, on_update=None):
        query = params['query']
        
        # 调用 Tavily 搜索，max_results 可控制返回条数
        response = await self.client.search(
            query=query,
            max_results=5,
            search_depth="basic"  # basic(1 credit) 或 advanced(2 credits)
        )
        
        # 提取结果，格式化为文本
        results = []
        for r in response.get("results", []):
            title = r.get("title", "")
            url = r.get("url", "")
            content = r.get("content", "")[:200]
            results.append(f"- {title}\n  {url}\n  {content}")
        
        result_text = "\n".join(results) if results else "未找到相关结果"
        
        print(f"\n[工具执行] 搜索完成，返回 {len(results)} 条结果")
        return AgentToolResult(content=[TextContent(text=result_text)])
    
    